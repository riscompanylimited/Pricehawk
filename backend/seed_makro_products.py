#!/usr/bin/env python3
"""
Seed Makro products into `products` from a plain list of SKUs (article codes).

The docs (docs/sku_makro.txt) give only the Makro SKU / article code, with no
barcode, so a product URL (https://www.makro.pro/en/p/{sku}-{productId}) can't
be built directly. This resolves each SKU to its productId + title via Makro's
public search index (same endpoint scrapers/makro/scrape_makro_categories.py
uses), then upserts a row into `products` (retailer_id='makro').

Price is left NULL here on purpose — run update_makro_prices.py afterwards to
fill current_price / step_prices / image_url (that's the real test of that cron).

Usage:
    python seed_makro_products.py --file ../docs/sku_makro.txt
    python seed_makro_products.py --sku 77948 --sku 120948
    python seed_makro_products.py --file ../docs/sku_makro.txt --dry-run

Reads DB creds from backend/.env (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load backend/.env
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

SEARCH_URL = "https://search.maknet.siammakro.cloud/search/api/v1/indexes/products/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


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


def parse_sku_file(path: str) -> list:
    """One SKU per line; ignore blank lines and '# category' headers. Dedupe,
    preserving first-seen order."""
    seen, out = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = re.sub(r"\s+", "", s)
            if s.isdigit() and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def resolve_sku(sku: str, timeout: int = 20) -> dict:
    """Look up a SKU in the Makro search index. Returns the matching document
    (makroId == sku) or None."""
    body = json.dumps({
        "q": sku, "size": 5, "page": 1,
        "filters": {"isSalesCustomer": False, "countryCode": "TH", "lang": "th"},
    }).encode()
    req = urllib.request.Request(
        SEARCH_URL, data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"error": str(e)}
    for h in data.get("hits", []):
        doc = h.get("document", {})
        if str(doc.get("makroId")) == str(sku):
            return {"doc": doc}
    # fall back to first hit if exact makroId not present
    hits = data.get("hits", [])
    if hits:
        return {"doc": hits[0]["document"], "fuzzy": True}
    return {"error": "not found"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Makro products from a SKU list.")
    ap.add_argument("--file", help="path to a SKU list file (one per line, '#' = header)")
    ap.add_argument("--sku", action="append", help="individual SKU (repeatable)")
    ap.add_argument("--delay", type=float, default=0.2, help="delay between lookups (s)")
    ap.add_argument("--dry-run", action="store_true", help="resolve + print, no DB write")
    args = ap.parse_args()

    skus = list(args.sku or [])
    if args.file:
        skus = parse_sku_file(args.file) + [s for s in skus if s not in skus]
    if not skus:
        ap.error("provide --file and/or --sku")

    print(f"Resolving {len(skus)} SKU(s){'  [DRY RUN]' if args.dry_run else ''}...")

    conn = None if args.dry_run else get_conn()
    cur = conn.cursor() if conn else None

    ok = failed = 0
    unresolved = []
    for i, sku in enumerate(skus, 1):
        r = resolve_sku(sku)
        if "error" in r:
            failed += 1
            unresolved.append(sku)
            print(f"[{i}/{len(skus)}] MISS {sku} — {r['error']}")
        else:
            doc = r["doc"]
            product_id = doc.get("productId")
            name = doc.get("title") or doc.get("titleEn") or sku
            name_en = doc.get("titleEn")
            brand = doc.get("brand")
            barcode = doc.get("barcode")
            url = f"https://www.makro.pro/en/p/{sku}-{product_id}" if product_id else None
            tag = " [fuzzy]" if r.get("fuzzy") else ""
            if not url:
                failed += 1
                unresolved.append(sku)
                print(f"[{i}/{len(skus)}] MISS {sku} — no productId")
            elif args.dry_run:
                ok += 1
                print(f"[{i}/{len(skus)}] OK   {sku} -> {url}  {name}{tag}")
            else:
                try:
                    cur.execute("""
                        INSERT INTO products
                            (retailer_id, sku, barcode, name, name_en, brand, url, is_active, created_at, updated_at)
                        VALUES ('makro', %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
                        ON CONFLICT (retailer_id, sku) DO UPDATE SET
                            barcode = COALESCE(EXCLUDED.barcode, products.barcode),
                            name    = EXCLUDED.name,
                            name_en = EXCLUDED.name_en,
                            brand   = COALESCE(EXCLUDED.brand, products.brand),
                            url     = EXCLUDED.url,
                            is_active = TRUE,
                            updated_at = NOW()
                    """, (sku, barcode, name, name_en, brand, url))
                    conn.commit()
                    ok += 1
                    print(f"[{i}/{len(skus)}] OK   {sku} -> {url}  {name}{tag}")
                except Exception as e:
                    conn.rollback()
                    failed += 1
                    unresolved.append(sku)
                    print(f"[{i}/{len(skus)}] DBERR {sku}: {e}")
        if i < len(skus):
            time.sleep(args.delay)

    if cur:
        cur.close()
    if conn:
        conn.close()

    print("=" * 60)
    print(f"DONE: {ok} seeded, {failed} failed / {len(skus)} total")
    if unresolved:
        print(f"Unresolved SKUs ({len(unresolved)}): {' '.join(unresolved)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
