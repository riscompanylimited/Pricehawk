#!/usr/bin/env python3
"""
Collect Makro price + per-branch step_prices (slab ladder) into
makro_location_prices — a MANUAL / backfill companion to the cron
`update_makro_location_prices.py`.

The cron does every monitored product x every monitored branch, unattended and
on a schedule. This script is for ad-hoc runs: backfill one SKU, re-check a
handful of products, or dry-run to eyeball the slab without touching the DB.

Both use the SAME fetch (`fetch_makro_price_by_location`, imported below), so the
slab logic lives in one place — this file only adds product/branch selection,
CLI flags, and the upsert loop.

Selection (defaults to ALL Makro products x ALL active branches):
    python collect_location_step_prices.py                    # everything
    python collect_location_step_prices.py --sku 120948       # one SKU (repeatable)
    python collect_location_step_prices.py --sku 120948 --sku 218951
    python collect_location_step_prices.py --limit 10         # first 10 products
    python collect_location_step_prices.py --monitored        # only pbl_monitored_locations
    python collect_location_step_prices.py --dry-run          # fetch + print, no DB write

Environment (same as the cron):
    DATABASE_URL  or  DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE
    MAKRO_LOC_DELAY    delay between requests, seconds (default 1.0)
    MAKRO_LOC_TIMEOUT  HTTP timeout per request, seconds (default 15)
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reuse the cron's DB connector + fetch (fetch already returns step_prices).
from update_makro_location_prices import get_conn, fetch_makro_price_by_location

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_branches(cur, monitored_only: bool):
    """Active Makro branches with a usable (postcode, subdistrict) cookie pair.

    branch_code holds the postcode and name holds the subdistrict in the base
    schema. With --monitored, restrict to branches in pbl_monitored_locations
    (what the cron actually scrapes)."""
    sql = [
        "SELECT ml.id AS location_id, ml.branch_code AS postal_code, ml.name AS subdistrict",
        "FROM makro_locations ml",
    ]
    if monitored_only:
        sql.append("JOIN pbl_monitored_locations pml ON pml.location_id = ml.id")
    sql += [
        "WHERE ml.is_active = TRUE",
        "AND ml.branch_code IS NOT NULL AND ml.branch_code <> ''",
        "AND ml.name IS NOT NULL AND ml.name <> ''",
        "ORDER BY ml.id",
    ]
    cur.execute(" ".join(sql))
    return cur.fetchall()


def load_products(cur, skus, limit):
    """Active Makro products with a URL. Optionally filter by SKU / cap count."""
    sql = [
        "SELECT id AS makro_product_id, sku, url FROM products",
        "WHERE retailer_id = 'makro'",
        "AND is_active = TRUE",
        "AND url IS NOT NULL AND url <> ''",
    ]
    params = []
    if skus:
        sql.append("AND sku = ANY(%s)")
        params.append(list(skus))
    sql.append("ORDER BY id")
    if limit:
        sql.append("LIMIT %s")
        params.append(limit)
    cur.execute(" ".join(sql), params)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Collect Makro per-branch step_prices into makro_location_prices.")
    ap.add_argument("--sku", action="append", metavar="SKU",
                    help="only these Makro SKUs (repeatable)")
    ap.add_argument("--limit", type=int, metavar="N", help="max products")
    ap.add_argument("--monitored", action="store_true",
                    help="only branches in pbl_monitored_locations (default: all active)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + print, no DB write")
    args = ap.parse_args()

    delay   = float(os.environ.get("MAKRO_LOC_DELAY", 1.0))
    timeout = int(os.environ.get("MAKRO_LOC_TIMEOUT", 15))

    from psycopg2.extras import Json  # branch step_prices -> JSONB

    conn = get_conn()
    cur = conn.cursor()

    branches = load_branches(cur, args.monitored)
    products = load_products(cur, args.sku, args.limit)
    if not branches or not products:
        logger.info(f"Nothing to do (products={len(products)}, branches={len(branches)}).")
        conn.close()
        return 0

    total = len(products) * len(branches)
    logger.info("=" * 70)
    logger.info(f"  COLLECT MAKRO STEP PRICES BY LOCATION{'  [DRY RUN]' if args.dry_run else ''}")
    logger.info(f"  Started: {datetime.now().isoformat()}")
    logger.info(f"  Products {len(products)} x Branches {len(branches)} = {total} fetches")
    logger.info("=" * 70)

    ok = fail = with_slab = i = 0
    for pr in products:
        for br in branches:
            i += 1
            result = fetch_makro_price_by_location(
                pr["url"], br["postal_code"], br["subdistrict"], timeout=timeout)

            if not result["success"]:
                fail += 1
                logger.warning(f"[{i}/{total}] FAIL {pr['sku']} @ {br['postal_code']} "
                               f"({br['subdistrict']}) — {result['error']}")
            else:
                price = result["price"]
                step_prices = result.get("step_prices")
                slab = f" slab={step_prices}" if step_prices else ""
                if step_prices:
                    with_slab += 1
                if args.dry_run:
                    logger.info(f"[{i}/{total}] DRY  {pr['sku']} @ {br['postal_code']} "
                                f"({br['subdistrict']}) -> ฿{price}{slab}")
                else:
                    try:
                        cur.execute(
                            "INSERT INTO makro_location_prices "
                            "(makro_product_id, location_id, price, step_prices, scraped_at) "
                            "VALUES (%s, %s, %s, %s, NOW()) "
                            "ON CONFLICT (makro_product_id, location_id) "
                            "DO UPDATE SET price = EXCLUDED.price, "
                            "step_prices = EXCLUDED.step_prices, scraped_at = NOW()",
                            (pr["makro_product_id"], br["location_id"], price,
                             Json(step_prices) if step_prices else None))
                        conn.commit()
                        ok += 1
                        logger.info(f"[{i}/{total}] OK   {pr['sku']} @ {br['postal_code']} "
                                    f"({br['subdistrict']}) -> ฿{price}{slab}")
                    except Exception as e:
                        conn.rollback()
                        fail += 1
                        logger.error(f"[{i}/{total}] DB ERROR {pr['sku']} @ "
                                     f"{br['postal_code']}: {e}")

            if i < total and delay:
                time.sleep(delay)

    cur.close()
    conn.close()

    logger.info("=" * 70)
    logger.info(f"  DONE: {ok} ok, {fail} failed, {with_slab} with slab / {total} total")
    logger.info(f"  Finished: {datetime.now().isoformat()}")
    logger.info("=" * 70)
    return 1 if fail > ok else 0


if __name__ == "__main__":
    sys.exit(main())
