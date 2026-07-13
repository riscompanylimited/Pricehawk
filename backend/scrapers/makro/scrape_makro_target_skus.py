#!/usr/bin/env python3
"""
Makro scraper - Technique 2: targeted SKU list instead of category sweep.

Discovery here is a fixed list of known makroId/SKU values, resolved
directly via the Search Index (q=<makroId>) - confirmed exact-match, not
fuzzy: tested SKUs 77948, 3185, 32019, 78000 each returned found=1 with the
matching makroId, even for short numbers that could plausibly substring-
match many products.

Everything after Discovery (per-branch enrichment, CSV/DB writes, branch
concurrency) is the exact same code as scrape_makro_categories.py -
imported directly, not duplicated, so any fix made there (field mapping,
rate limiting, DB schema, etc.) applies here automatically too.

Field mapping note: the category-sweep technique attaches "category_meta"
based on WHICH of the 5 known Type A categories (or the Fresh & Frozen BFS)
a product was discovered through. There's no equivalent "discovered via"
context for a direct SKU lookup, so here the category/categoryId columns
are filled from the product's own native `deepestCategory`/`categoryIds`
fields instead, and slug/flexiPageHandle are left blank (not applicable).

Output files are prefixed "sku_target_" so they never collide with or get
confused for the category-sweep technique's output files in the same
folder.
"""

import concurrent.futures
import time

import scrape_makro_categories as base
from scrape_makro_categories import (
    SEARCH_URL, curl_post, BRANCH_STORE_CODES, MAX_CONCURRENT_BRANCHES,
    STAGGER_START_SECONDS, enrich_and_write_branch, load_branch_names,
)

base.OUTPUT_CSV_TEMPLATE = base.OUTPUT_CSV_TEMPLATE.replace(
    "output_", "sku_target_output_"
)

# Raw target SKU (makroId) list, exactly as provided - duplicates within
# the list itself (e.g. 187896, 187891, 187895, 187898, 187857 each appear
# twice) are deduped at lookup time, not removed here, so this stays a
# faithful copy of the source list.
TARGET_SKUS_RAW = [
    # List 1 (100 rows, source numbering skips 4 and 6)
    "77948", "218553", "77103", "78000", "78520", "178152", "78026", "78260",
    "78273", "77974", "177619", "144596", "77051", "826486", "831507",
    "826489", "482859", "142363", "274287", "3185", "264810", "103896",
    "176089", "78507", "829475", "78442", "78481", "132612", "169572",
    "824265", "578422", "578370", "831286", "810099", "810101", "200325",
    "821516", "821517", "847938", "852985", "859639", "78910", "75361",
    "75491", "831501", "175904", "75829", "831497", "118948", "76843",
    "821612", "847688", "847930", "179054", "192493", "75569", "831499",
    "233616", "186973", "75608", "831505", "233747", "859637", "906918",
    "829474", "110366", "824264", "832590", "853419", "218173", "220900",
    "855287", "178100", "76934", "130262", "173542", "187896", "161846",
    "826153", "826154", "187896", "187897", "187891", "187895", "187891",
    "187895", "187898", "187857", "200724", "200727", "200740", "200741",
    "200808", "200732", "218179", "218183", "187898", "187857",
    # List 2 (11 rows)
    "918605", "32019", "32032", "32955", "31915", "32929", "32370",
    "901769", "230538", "230541", "230543",
    # List 3 (37 rows)
    "208572", "177593", "900544", "904727", "159683", "190618", "223314",
    "855383", "891837", "896195", "855377", "855373", "855361", "855365",
    "855353", "855369", "855357", "136348", "103543", "182758", "175353",
    "111256", "111258", "128441", "102150", "82775", "221241", "129271",
    "859004", "220659", "192572", "926863", "108787", "227932", "111259",
    "850307", "848648",
    # List 4 (15 rows)
    "901172", "901175", "198368", "913732", "864156", "913734", "899975",
    "928388", "913728", "901422", "812399", "812402", "812398", "812401",
    "812400",
]

TARGET_SKUS = list(dict.fromkeys(TARGET_SKUS_RAW))  # dedupe, keep order


def find_product_by_sku(sku: str) -> dict | None:
    """Exact-match lookup via Search Index q=<sku>. Sweeps both
    isSalesCustomer segments for consistency with the category-sweep
    technique (confirmed non-redundant there - Alcohol was 6 vs 93) even
    though every direct-SKU test so far returned the same result both
    ways - cheap insurance against a SKU that behaves differently."""
    for is_sales_customer in (True, False):
        body = {
            "q": sku, "size": 5, "page": 1,
            "filters": {
                "countryCode": "TH", "lang": "th",
                "isSalesCustomer": is_sales_customer,
            },
        }
        d = curl_post(SEARCH_URL, body)
        for h in d.get("hits", []):
            doc = h["document"]
            if str(doc.get("makroId")) == str(sku):
                return doc
    return None


def main():
    print(f"=== scrape_makro_target_skus.py started at "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    print(f"Run started: {run_ts}")
    print(f"Target SKUs: {len(TARGET_SKUS)} unique "
          f"({len(TARGET_SKUS_RAW)} listed, "
          f"{len(TARGET_SKUS_RAW) - len(TARGET_SKUS)} duplicates removed)")
    print(f"Target branches ({len(BRANCH_STORE_CODES)}): {BRANCH_STORE_CODES}\n")

    branch_names = load_branch_names()
    all_products: dict[str, dict] = {}
    not_found = []

    for sku in TARGET_SKUS:
        doc = find_product_by_sku(sku)
        if doc is None:
            not_found.append(sku)
            continue
        makro_id = doc["makroId"]
        if makro_id in all_products:
            continue
        categories = doc.get("categories") or []
        category_ids = doc.get("categoryIds") or []
        all_products[makro_id] = {
            "doc": doc,
            "category_meta": {
                "category": doc.get("deepestCategory") or (categories[-1] if categories else ""),
                "categoryId": category_ids[-1] if category_ids else "",
                "slug": "",
                "flexiPageHandle": "",
            },
        }

    print(f"Resolved {len(all_products)}/{len(TARGET_SKUS)} target SKUs "
          f"({len(not_found)} not found)")
    if not_found:
        print(f"  Not found (discontinued/typo/not first-party?): {not_found}")

    if not all_products:
        print("Nothing resolved - exiting.")
        return

    product_ids = [entry["doc"]["productId"] for entry in all_products.values()]

    print(f"Running with up to {MAX_CONCURRENT_BRANCHES} branches concurrently...\n")

    def _run_branch_safe(branch_code):
        try:
            enrich_and_write_branch(branch_code, run_ts, all_products, product_ids, branch_names)
            return branch_code, None
        except Exception as exc:  # noqa: BLE001
            return branch_code, exc

    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BRANCHES) as pool:
        futures = []
        for i, branch_code in enumerate(BRANCH_STORE_CODES):
            print(f"Starting branch {branch_code} ({i + 1}/{len(BRANCH_STORE_CODES)})...")
            futures.append(pool.submit(_run_branch_safe, branch_code))
            if i < min(MAX_CONCURRENT_BRANCHES, len(BRANCH_STORE_CODES)) - 1:
                time.sleep(STAGGER_START_SECONDS)

        done = 0
        for future in concurrent.futures.as_completed(futures):
            branch_code, exc = future.result()
            done += 1
            if exc is not None:
                failed.append(branch_code)
                print(f"[{done}/{len(BRANCH_STORE_CODES)}] Branch {branch_code} FAILED: {exc}")
            else:
                print(f"[{done}/{len(BRANCH_STORE_CODES)}] Branch {branch_code} complete.")

    if failed:
        print(f"\n{len(failed)} branch(es) failed and were skipped: {failed}")
    print(f"\nAll {len(BRANCH_STORE_CODES)} branches done. Run: {run_ts}")


if __name__ == "__main__":
    main()
