#!/usr/bin/env python3
"""
Load scraped product JSON (output of adws/adw_ecommerce_product_scraper.py) into
the `products` table — including price, step_prices and image, in one pass.

The scraper writes one JSON file per retailer (e.g. results/makro.json), each a
list of ProductData dicts. This reads those file(s) and upserts each product into
`products` keyed by (retailer_id, sku). Unlike seed_makro_products.py (which
resolves SKUs via the search index and leaves price NULL), the scraper already
captured current_price / step_prices / images, so those go straight in and a
price_history row is recorded.

Usage:
    # after: adws/adw_ecommerce_product_scraper.py --urls-file urls.txt --output-file ./results/products.json
    python load_scraped_products.py ./results/makro.json
    python load_scraped_products.py ./results/            # all *.json in a folder
    python load_scraped_products.py ./results/makro.json --dry-run
    python load_scraped_products.py ./results/makro.json --no-history

Reads DB creds from backend/.env (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE).
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load backend/.env (same pattern as seed_makro_products.py)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# Map the scraper's human retailer name (ProductData.retailer) to a retailer_id.
RETAILER_ID_MAP = {
    "makro": "makro",
    "central food wholesale": "cfw",
    "cfw": "cfw",
}


def get_conn():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ.get("DB_NAME", "pricehawk"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        sslmode=os.environ.get("DB_SSLMODE", "prefer"),
        cursor_factory=RealDictCursor,
    )


def gather_files(paths: list) -> list:
    """Expand each path: a .json file is taken as-is; a directory contributes its
    *.json files. Dedupe, preserving order."""
    out, seen = [], set()
    for p in paths:
        if os.path.isdir(p):
            found = sorted(glob.glob(os.path.join(p, "*.json")))
        else:
            found = [p]
        for f in found:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def resolve_retailer_id(product: dict, override: str) -> str:
    if override:
        return override
    name = (product.get("retailer") or "").strip().lower()
    return RETAILER_ID_MAP.get(name, name)


def main() -> int:
    ap = argparse.ArgumentParser(description="Load scraped product JSON into the products table.")
    ap.add_argument("paths", nargs="+", help="JSON file(s) and/or folder(s) of scraper output")
    ap.add_argument("--retailer-id", help="force this retailer_id for every product "
                                          "(default: map from each product's 'retailer' field)")
    ap.add_argument("--no-history", action="store_true", help="don't insert price_history rows")
    ap.add_argument("--dry-run", action="store_true", help="parse + print, no DB write")
    args = ap.parse_args()

    files = gather_files(args.paths)
    if not files:
        ap.error("no JSON files found in the given path(s)")

    # Load + flatten all products
    products = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print(f"SKIP {f}: {e}")
            continue
        rows = data if isinstance(data, list) else [data]
        products.extend(rows)
        print(f"Loaded {len(rows)} product(s) from {f}")

    print(f"Upserting {len(products)} product(s){'  [DRY RUN]' if args.dry_run else ''}...")

    conn = None if args.dry_run else get_conn()
    cur = conn.cursor() if conn else None

    ok = skipped = failed = 0
    for i, p in enumerate(products, 1):
        sku = (p.get("sku") or "").strip()
        retailer_id = resolve_retailer_id(p, args.retailer_id)
        name = p.get("name") or sku
        if not sku or not retailer_id:
            skipped += 1
            print(f"[{i}/{len(products)}] SKIP — missing sku/retailer_id (name={name!r})")
            continue

        brand = p.get("brand")
        url = p.get("url")
        current_price = p.get("current_price")
        step_prices = json.dumps(p.get("step_prices") or [])
        images = p.get("images") or []
        image_url = images[0] if images else None

        if args.dry_run:
            ok += 1
            print(f"[{i}/{len(products)}] OK   {retailer_id}/{sku}  {name[:40]}  "
                  f"price={current_price} steps={len(p.get('step_prices') or [])} img={'Y' if image_url else '-'}")
            continue

        try:
            cur.execute("""
                INSERT INTO products
                    (retailer_id, sku, name, brand, current_price, step_prices,
                     url, image_url, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, TRUE, NOW(), NOW())
                ON CONFLICT (retailer_id, sku) DO UPDATE SET
                    name          = EXCLUDED.name,
                    brand         = COALESCE(EXCLUDED.brand, products.brand),
                    current_price = EXCLUDED.current_price,
                    step_prices   = EXCLUDED.step_prices,
                    url           = COALESCE(EXCLUDED.url, products.url),
                    image_url     = COALESCE(EXCLUDED.image_url, products.image_url),
                    is_active     = TRUE,
                    updated_at    = NOW()
                RETURNING id
            """, (retailer_id, sku, name, brand, current_price, step_prices, url, image_url))
            product_id = cur.fetchone()["id"]

            if not args.no_history and current_price is not None:
                cur.execute("""
                    INSERT INTO price_history (product_id, price, step_prices, recorded_at)
                    VALUES (%s, %s, %s::jsonb, NOW())
                """, (product_id, current_price, step_prices))

            conn.commit()
            ok += 1
            print(f"[{i}/{len(products)}] OK   {retailer_id}/{sku}  {name[:40]}  price={current_price}")
        except Exception as e:
            conn.rollback()
            failed += 1
            print(f"[{i}/{len(products)}] DBERR {retailer_id}/{sku}: {e}")

    if cur:
        cur.close()
    if conn:
        conn.close()

    print("=" * 60)
    print(f"DONE: {ok} upserted, {skipped} skipped, {failed} failed / {len(products)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
