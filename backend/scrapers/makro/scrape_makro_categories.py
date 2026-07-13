#!/usr/bin/env python3
"""
Makro category scraper - Discovery ONCE, then Enrichment per branch, up to
MAX_CONCURRENT_BRANCHES branches running at the same time. Each branch still
gets its own single-storeCode GraphQL calls and its own output file/DB
writes exactly as before - nothing about the request shape changed, only
how many of these independent per-branch runs are in flight at once.

Every field/column is exactly what the original single-branch version had -
nothing removed or restructured. Edit BRANCH_STORE_CODES below to change
which branches get scraped, DISCOVERY_STORE_CODE to change which single
branch's context is used for the (branch-independent, spot-checked)
Fresh & Frozen page crawl, and MAX_CONCURRENT_BRANCHES to change how many
branches run in parallel.

Techniques used (see MAKRO_API_REFERENCE_BRIEF.md for full rationale/evidence):
  - Type A categories (Dry Grocery, Beverages, Snacks, Seafood, Meat):
    discovered directly via Search Index `categoryIds` filter, swept across
    both isSalesCustomer segments (confirmed: segments are NOT redundant,
    e.g. Alcohol was 6 vs 93 - always sweep both).
  - Fresh & Frozen (Type B - confirmed categoryId 782 returns found:0):
    discovered via Flexi-Page BFS crawl (seed handle "fresh-and-frozen")
    -> collectionId harvest -> per-collectionId Search Index sweep.
    Boundary check applied as a performance optimization: BFS does not
    recurse into a sibling page whose handle matches another already-known
    Category's own flexiPageHandle (confirmed 10 of 27 siblings are exactly
    this - drinks, grocery, snacks-confectionery, etc).
  - Cross-category dedup: Type A categories are scraped FIRST. Fresh &
    Frozen products already seen (by makroId) during the Type A pass are
    recognized and skipped for re-insertion, but products found ONLY via
    Fresh & Frozen are kept - this is what makes "Fresh & Frozen" numbers
    meaningful (genuinely new items) rather than re-counting Seafood/Meat/
    Fruit&Veg items that are already fully covered by their own categoryId.
  - Enrichment: since exactly ONE storeCode is queried at a time, all of the
    fields the brief calls AMBIGUOUS (seller, status, displayPrice,
    originPrice, slabPrices, ...) are safe to read directly here - the
    single-store-context caveat from the brief does not apply when there is
    only one store in the query.
"""

import concurrent.futures
import json
import os
import re
import sys
import time
import csv
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))

# Force line-by-line flushing instead of Python's default buffered stdout -
# without this, print() output can sit in a buffer and not appear in
# Railway's log viewer until the buffer fills or the process exits, which
# makes an actually-running job look like it's stuck or never started.
sys.stdout.reconfigure(line_buffering=True)

# ============================================================================
# CONFIG - edit these to change what gets scraped. Nothing else in this
# script needs to change.
# ============================================================================

# Which single branch's context is used for the Fresh & Frozen page crawl
# (Discovery). Spot-checked store 03 vs store 09 (Bangkok vs Hat Yai, about
# as far apart as two branches can be) on the seed page - identical 36
# collections both ways - so one discovery pass with one default branch is
# treated as sufficient; not re-run per branch.
DISCOVERY_STORE_CODE = "03"       # Srinakarin (422 Moo 5, Srinakarin Road,
                                   # Tumbol Sumrongnua, Aumphur Muang,
                                   # Samutprakarn 10270)

# Every branch listed here gets its own full enrichment pass and its own
# output file - never combined into one multi-storeCode call, so every
# AMBIGUOUS field (seller, status, displayPrice, slabPrices, ...) stays safe
# to read as-is. What changes with concurrency is only how many of these
# independent per-branch runs are in flight at the same time (see
# MAX_CONCURRENT_BRANCHES below), not the shape of any request.
BRANCH_STORE_CODES = [
    "156", "03", "41", "62", "166", "27", "140", "18", "136", "161",
    "12", "15", "06", "162", "44", "804", "08", "01", "10", "09",
]

# This is only used by fetch_flexi_page() during the ONE-TIME, single-
# threaded Fresh & Frozen discovery pass in Step 2 of main() - before any
# branch worker starts. It is fixed to DISCOVERY_STORE_CODE and never
# reassigned; per-branch enrichment threads each pass their own branch_code
# explicitly into enrich_batch() instead of touching this global (a shared
# mutable global would be a race condition once branches run concurrently).
BRANCH_STORE_CODE = DISCOVERY_STORE_CODE

# One output file per branch, stamped with the date+time this run started -
# {run_ts} and {code} get filled in at write time. Using the run's start
# time (captured once in main(), shared by every branch in that run) rather
# than each branch's own finish time, so all files from one job are easy to
# group together by filename alone.
OUTPUT_CSV_TEMPLATE = os.path.join(_HERE, "output_{run_ts}_branch_{code}.csv")

# How many branches run at the same time. Each branch takes roughly
# 15-20 minutes end to end (enrichment batches + CSV + DB write), so 20
# branches fully sequential is ~6-7 hours; at 8 concurrent it's ~20 branches
# / 8 workers ~= 2.5 "waves" ~= 40-50 minutes, which is what fits the
# "all 20 branches inside about an hour" target. Raise this if it's still
# comfortably fast and no 403/429s show up in the logs - the marketplace and
# search APIs showed no rate limiting or bot protection in testing (see
# Part 6 of DEPLOYMENT_HANDBOOK.md); lower it if you ever do see one.
MAX_CONCURRENT_BRANCHES = 8

# Small delay between *starting* consecutive branch workers, so the first
# MAX_CONCURRENT_BRANCHES branches don't all fire their very first request
# in the same instant. Only affects the initial ramp-up - once the pool is
# full, a new branch only starts when another one finishes, which already
# staggers things naturally.
STAGGER_START_SECONDS = 3.0

# Real branch names, from the earlier makro.co.th scrape (fetch_branch_postcodes.py)
# - used for the branch_name column in the DB write. Deliberately NOT sourced
# from GraphQL's `seller` field, which is AMBIGUOUS.
BRANCH_POSTCODES_JSON = os.path.join(_HERE, "branch_postcodes.json")

GRAPHQL_URL = "https://marketplace.mango-prod.siammakro.cloud/product/api/v1/graphql?apiVersion=20230109"
SEARCH_URL = "https://search.maknet.siammakro.cloud/search/api/v1/indexes/products/search"
FLEXI_PAGE_URL = "https://www.makro.pro/product/api/v2/content/flexi-page"

# Type A target categories: (Thai/English name, categoryId, slug)
TYPE_A_CATEGORIES = [
    ("อาหารและเครื่องปรุง / Dry Grocery", "10000004", "dry-grocery"),
    ("เครื่องดื่ม / Beverages", "1000000", "beverages"),
    ("ขนม / Snacks", "2000", "snacks-confectionery"),
    ("อาหารทะเล / Seafood", "1000000110", "fish-seafood"),
    ("เนื้อสัตว์ / Meat", "1000000120", "meat"),
]

# Type B target: Fresh & Frozen
FRESH_FROZEN_NAME = "อาหารสดและแช่แข็ง / Fresh & Frozen"
FRESH_FROZEN_CATEGORY_ID = "782"          # kept for the output column even
                                            # though it never filters anything
FRESH_FROZEN_SLUG = "fresh-food-destination"
FRESH_FROZEN_HANDLE = "fresh-and-frozen"

# Non-thematic "batch" collections (e.g. "3P Product Collection - Batch 2",
# collectionId 19957 - confirmed to hold 3,758 completely unrelated products:
# sports drinks, garbage bags, a Panasonic refrigerator, ...) get swept up by
# the BFS the same way real thematic collections do, since they're
# structurally identical in the flexi-page JSON (just a collectionId +
# collectionName) - the boundary check (KNOWN_CATEGORY_HANDLES) can't catch
# this because it isn't caused by recursing into another Category's page, it
# slips in from a generic collection referenced on an otherwise-legitimate
# page. Filtered here instead, post-discovery, by internalCategories'
# super-category (Dept level - first entry of the list, always present) -
# confirmed real Fresh & Frozen items (salmon, chicken, chili) map to
# "seafood"/"meat"/"fruits-and-vegetables"/etc, never to any of these.
BLOCKED_INTERNAL_SUPERCATEGORIES = {
    "cleaning-supplies", "personal-care", "pet-supplies", "kitchen-and-dining",
    "appliances-and-electronics", "baby-and-kids-care", "beauty-and-cosmetics",
    "automotive", "stationery-and-office", "home-and-furniture", "beverages",
    "mobile-and-wearables", "dry-grocery-and-staples", "fashion-apparel",
    "health-wellness-and-nutrition", "sports-and-outdoor", "toys-games-and-media",
    "alcoholic-beverages", "computers-and-accessories",
}


def is_blocked_for_fresh_frozen(doc: dict) -> bool:
    internal = doc.get("internalCategories") or []
    super_cat = internal[0] if internal else None
    return super_cat in BLOCKED_INTERNAL_SUPERCATEGORIES

# Known flexiPageHandles of OTHER Categories - the BFS boundary check stops
# here instead of recursing further (performance optimization; see brief
# Section 5 discussion). Sourced from _ssrCategories.
KNOWN_CATEGORY_HANDLES = {
    "electronics", "gold-container", "pre-cny-ff-2026", "live-like-a-pro-makro-milli",
    "home-care", "grocery", "drinks", "snacks-confectionery", "buy-1-get-1-free",
    "top-brands-hot-deal", "makro-mail-14-2026", "pallet-bulk-pack-big-value-all",
    "kitchen", "employee-mall-18-feb-1-mar-26", "new-acquisition-makropro",
    "personalcare-1", "axtra-finds", "mom-and-baby", "professional-picks-b2b-weekly",
    "horeca-2026", "office-supplies", "makro-exclusive-brand", "loyalty",
    "petzfriend-gold", "lifestyle", "all-on-sale", "chinese-ghost-festival",
    "1px-only-online-exclusive", "roll-max", "electronics-express-delivery",
    "top-brands", "pro-mall", "brand-depot-may-26", "lotus",
}

MAX_BFS_PAGES = 40
SIZE = 120           # search index page size - confirmed hard server cap is
                     # 120 (requesting more silently truncates to 120, not an
                     # error) - using the real ceiling minimizes round trips
ENRICH_BATCH_SIZE = 100  # single-store -> can safely batch much larger than
                          # the 20-30 used for all-88-stores batches

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def curl_post(url: str, body: dict) -> dict:
    """Named curl_post for historical reasons (this used to shell out to the
    curl binary) - now uses urllib (stdlib) so it doesn't depend on curl
    being present in whatever container this runs in (e.g. Railway/Railpack,
    which has no proven precedent of including curl - the existing
    alert_checker.py service uses Python's own HTTP stack, not curl)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def curl_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


# ============================================================================
# DISCOVERY - Type A categories (direct categoryId, provably complete)
# ============================================================================

def search_by_category(category_id: str) -> list[dict]:
    """Paginate the Search Index for one categoryId, sweeping BOTH
    isSalesCustomer segments (confirmed non-redundant - Alcohol was 6 vs 93)."""
    docs = {}
    for is_sales_customer in (False, True):
        page = 1
        while True:
            body = {
                "q": "*", "size": SIZE, "page": page,
                "filters": {
                    "categoryIds": [category_id],
                    "isSalesCustomer": is_sales_customer,
                    "countryCode": "TH", "lang": "th",
                },
            }
            d = curl_post(SEARCH_URL, body)
            found = d.get("found", 0)
            hits = d.get("hits", [])
            for h in hits:
                doc = h["document"]
                docs[doc["makroId"]] = doc
            if page * SIZE >= found or not hits:
                break
            page += 1
    return list(docs.values())


# ============================================================================
# DISCOVERY - Fresh & Frozen (Type B: Flexi-Page -> Collection Graph -> Product)
# ============================================================================

def fetch_flexi_page(handle: str) -> dict:
    params = f"?platform=WEB&handle={handle}&locale=th&countryCode=TH&storeCodes[]={BRANCH_STORE_CODE}&limit=20"
    raw = curl_get(FLEXI_PAGE_URL + params)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def walk_components(node, collections: dict, links: set):
    if isinstance(node, dict):
        cid = node.get("collectionId")
        if cid:
            collections[str(cid)] = node.get("collectionName")
        if node.get("targetType") == "FLEXI_PAGE" and node.get("pageHandle"):
            links.add(node["pageHandle"])
        for v in node.values():
            walk_components(v, collections, links)
    elif isinstance(node, list):
        for v in node:
            walk_components(v, collections, links)


def crawl_fresh_frozen_collections(seed_handle: str) -> dict[str, str]:
    """BFS the flexi-page graph starting at seed_handle. Boundary check:
    does not recurse into a sibling page whose handle is itself another
    Category's flexiPageHandle - performance optimization, avoids
    re-crawling e.g. the entire 'drinks'/'grocery' subtree from here."""
    visited = set()
    queue = [seed_handle]
    all_collections: dict[str, str] = {}

    while queue and len(visited) < MAX_BFS_PAGES:
        handle = queue.pop(0)
        if handle in visited:
            continue
        visited.add(handle)

        data = fetch_flexi_page(handle)
        collections: dict[str, str] = {}
        links: set = set()
        walk_components(data.get("components", []), collections, links)
        all_collections.update(collections)

        for link in links:
            if link in visited:
                continue
            if link in KNOWN_CATEGORY_HANDLES:
                # boundary hit - record nothing further, don't recurse
                continue
            if link not in queue:
                queue.append(link)

    return all_collections


def search_by_collection(collection_id: str) -> list[dict]:
    docs = {}
    for is_sales_customer in (False, True):
        page = 1
        while True:
            body = {
                "q": "*", "size": SIZE, "page": page,
                "filters": {
                    "collectionIds": [collection_id],
                    "isSalesCustomer": is_sales_customer,
                    "countryCode": "TH", "lang": "th",
                },
            }
            d = curl_post(SEARCH_URL, body)
            found = d.get("found", 0)
            hits = d.get("hits", [])
            for h in hits:
                doc = h["document"]
                docs[doc["makroId"]] = doc
            if page * SIZE >= found or not hits:
                break
            page += 1
    return list(docs.values())


# ============================================================================
# ENRICHMENT - batched GraphQL, single storeCode (safe for AMBIGUOUS fields)
# ============================================================================

ENRICH_QUERY = """
query products($ids: [String!]!, $storeCodes: [String!], $lang: String, $countryCode: String) {
  products(ids: $ids, storeCodes: $storeCodes, lang: $lang, countryCode: $countryCode) {
    id
    makroId
    providerSku
    title
    titleTh
    titleEn
    brand
    brandTh
    brandEn
    status
    barcode
    skuCode
    seller
    size
    priceUnit
    displayPrice
    originPrice
    mainImage
    imageUrls
    miraklProductId
    packagingWeight
    moq
    productOptionId
    optionsLength
    dateCreated
    dateModified
    vendor
    slabPrices {
      slabPromotionId
      slabPriceDescription
      slabPriceTiers { quantity tier discount priceInVat }
    }
  }
}
"""


def enrich_batch(product_ids: list[str], store_code: str) -> list[dict]:
    body = {
        "operationName": "products",
        "variables": {
            "ids": product_ids,
            "storeCodes": [store_code],
            "lang": "th", "countryCode": "TH",
        },
        "query": ENRICH_QUERY,
    }
    d = curl_post(GRAPHQL_URL, body)
    return (d.get("data") or {}).get("products") or []


def build_product_url(makro_id: str, product_id: str) -> str:
    return f"https://www.makro.pro/th/p/{makro_id}-{product_id}"


def excel_text(value) -> str:
    """Force Excel to treat a value as literal text, preserving leading
    zeros, instead of auto-converting it to a number on CSV open (confirmed
    bug symptom: '00027' and '000027' both silently became 27). Uses the
    ="..." formula trick, which Excel evaluates back to the exact original
    string regardless of import method."""
    if value is None:
        return ""
    s = str(value).replace('"', '""')
    return f'="{s}"'


def unwrap_excel_text(value: str) -> str | None:
    """Reverses excel_text() - ="00027" -> "00027". Needed because the row
    dicts built for the CSV already have id/sku/barcode wrapped, and the DB
    needs the raw value, not the spreadsheet formula."""
    if not value:
        return None
    m = re.match(r'^="(.*)"$', value)
    return m.group(1).replace('""', '"') if m else value


def load_branch_names() -> dict:
    """storeCode -> branch name, from the earlier makro.co.th scrape."""
    try:
        with open(BRANCH_POSTCODES_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return {code: row.get("name", "") for code, row in data.items()}
    except FileNotFoundError:
        return {}


# ============================================================================
# DATABASE - optional. Only runs if DATABASE_URL is set in the environment
# (e.g. on Railway/Neon) - local CSV-only runs work exactly as before with
# no DB configured at all. Schema: database/init/13_makro_store_pricing.sql
# in the Pricehawk repo.
# ============================================================================

def get_db_connection():
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_HERE, ".env"))  # local convenience only -
        # on Railway, DATABASE_URL is injected directly as an env var and
        # there is no .env file, so this is a harmless no-op there.
    except ImportError:
        pass
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None
    import psycopg2
    from urllib.parse import urlparse
    parsed = urlparse(database_url)
    return psycopg2.connect(
        host=parsed.hostname, port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"), user=parsed.username,
        password=parsed.password, sslmode="require",
    )


def write_rows_to_db(conn, rows: list[dict], branch_code: str, branch_name: str) -> None:
    """Upserts product identity + this branch's current price snapshot into
    Postgres, and appends to makro_store_price_history ONLY when
    display_price or compare_at_price actually changed since the last
    snapshot for that (product_id, store_code) - matches the insert-on-change
    design in 13_makro_store_pricing.sql (21/30 sampled SKUs showed zero
    price variance across branches, so logging every run would be mostly
    duplicate rows)."""
    if conn is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO makro_stores (store_code, name)
               VALUES (%s, %s)
               ON CONFLICT (store_code) DO UPDATE SET name = EXCLUDED.name
               WHERE makro_stores.name IS NULL OR makro_stores.name = ''""",
            (branch_code, branch_name),
        )

        for row in rows:
            product_id = unwrap_excel_text(row["id"])
            sku = unwrap_excel_text(row["sku"])
            barcode = unwrap_excel_text(row["barcode"])
            if not product_id:
                continue

            cur.execute(
                """INSERT INTO makro_products
                     (product_id, sku, sku_code, barcode, title_th, title_en,
                      brand_th, brand_en, size, price_unit, vendor, main_image,
                      mirakl_product_id, product_option_id, options_length,
                      date_created, date_modified, last_synced_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                   ON CONFLICT (product_id) DO UPDATE SET
                     sku = EXCLUDED.sku, sku_code = EXCLUDED.sku_code,
                     barcode = EXCLUDED.barcode, title_th = EXCLUDED.title_th,
                     title_en = EXCLUDED.title_en, brand_th = EXCLUDED.brand_th,
                     brand_en = EXCLUDED.brand_en, main_image = EXCLUDED.main_image,
                     last_synced_at = NOW()""",
                (product_id, sku, row["skuCode"] or None, barcode,
                 row["title"] or None, row["titleEn"] or None,
                 row["brandTh"] or None, row["brandEn"] or None,
                 row["size"] or None, row["priceUnit"] or None,
                 row["vendor"] or None, row["mainImage"] or None,
                 row["miraklProductId"] or None,
                 row["productOptionId"] or None, row["optionsLength"] or None,
                 row["dateCreated"] or None, row["dateModified"] or None),
            )

            if row.get("categoryId"):
                cur.execute(
                    """INSERT INTO makro_collections (collection_id, title)
                       VALUES (%s, %s)
                       ON CONFLICT (collection_id) DO UPDATE SET title = EXCLUDED.title""",
                    (row["categoryId"], row["category"]),
                )
                cur.execute(
                    """INSERT INTO makro_product_collections (product_id, collection_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (product_id, row["categoryId"]),
                )

            new_price = row["displayPrice"] if row["displayPrice"] != "" else None
            new_compare = row["originPrice"] if row["originPrice"] != "" else None

            cur.execute(
                """SELECT display_price, compare_at_price FROM makro_store_prices
                   WHERE product_id = %s AND store_code = %s""",
                (product_id, branch_code),
            )
            prev = cur.fetchone()
            changed = prev is None or (
                str(prev[0]) != str(new_price) or str(prev[1]) != str(new_compare)
            )

            cur.execute(
                """INSERT INTO makro_store_prices
                     (product_id, store_code, display_price, compare_at_price,
                      status, moq, scraped_at)
                   VALUES (%s,%s,%s,%s,%s,%s, NOW())
                   ON CONFLICT (product_id, store_code) DO UPDATE SET
                     display_price = EXCLUDED.display_price,
                     compare_at_price = EXCLUDED.compare_at_price,
                     status = EXCLUDED.status, moq = EXCLUDED.moq,
                     scraped_at = NOW()""",
                (product_id, branch_code, new_price, new_compare,
                 row["status"] or None, row["moq"] if row["moq"] != "" else None),
            )

            if changed and new_price is not None:
                cur.execute(
                    """INSERT INTO makro_store_price_history
                         (product_id, store_code, display_price, compare_at_price, scraped_at)
                       VALUES (%s,%s,%s,%s, NOW())""",
                    (product_id, branch_code, new_price, new_compare),
                )

            slab_tiers_raw = row.get("slabPriceTiers")
            if slab_tiers_raw and slab_tiers_raw != "[]":
                try:
                    tiers = json.loads(slab_tiers_raw)
                except json.JSONDecodeError:
                    tiers = []
                for t in tiers:
                    cur.execute(
                        """INSERT INTO makro_slab_tiers
                             (product_id, store_code, tier, quantity_breakpoint,
                              discount, price_in_vat, scraped_at)
                           VALUES (%s,%s,%s,%s,%s,%s, NOW())
                           ON CONFLICT (product_id, store_code, tier) DO UPDATE SET
                             quantity_breakpoint = EXCLUDED.quantity_breakpoint,
                             discount = EXCLUDED.discount,
                             price_in_vat = EXCLUDED.price_in_vat,
                             scraped_at = NOW()""",
                        (product_id, branch_code, t.get("tier"),
                         t.get("quantity"), t.get("discount"), t.get("priceInVat")),
                    )

    conn.commit()


def enrich_and_write_branch(branch_code: str, run_ts: str, all_products: dict,
                             product_ids: list[str], branch_names: dict) -> None:
    """Runs enrichment + writes the output file (and DB rows, if configured)
    for ONE branch, start to finish, before returning. Field list and
    row-building logic are exactly what the original single-branch version
    had - unchanged. Safe to call from multiple threads at once: branch_code
    is passed explicitly all the way down to enrich_batch() rather than
    going through any shared mutable state, and every branch writes to its
    own CSV file and (for the DB) its own psycopg2 connection."""

    # ---- Step 3: Enrichment, batched, this branch only ----
    total_batches = (len(product_ids) + ENRICH_BATCH_SIZE - 1) // ENRICH_BATCH_SIZE
    print(f"[Branch {branch_code}] Started - enriching {len(product_ids)} products "
          f"({total_batches} batches)")

    enriched_by_id = {}
    for i in range(0, len(product_ids), ENRICH_BATCH_SIZE):
        batch = product_ids[i:i + ENRICH_BATCH_SIZE]
        batch_num = i // ENRICH_BATCH_SIZE + 1
        results = enrich_batch(batch, branch_code)
        for p in results:
            enriched_by_id[p["id"]] = p
        # Only log every 10th batch (plus the last one) - one line per batch
        # would be 100+ lines per branch, times up to 8 concurrent branches,
        # which drowns out everything else in Railway's log viewer.
        if batch_num % 10 == 0 or batch_num == total_batches:
            print(f"[Branch {branch_code}] Enriching... {batch_num}/{total_batches} batches done")
        time.sleep(0.3)

    print(f"[Branch {branch_code}] Enrichment done, writing output...")

    # ---- Step 4: merge + write output ----
    fieldnames = [
        "id", "sku", "title", "titleEn", "brand", "brandTh", "brandEn",
        "status", "barcode", "skuCode", "seller", "size", "priceUnit",
        "displayPrice", "originPrice", "mainImage", "miraklProductId",
        "packagingWeight", "moq", "productOptionId", "optionsLength",
        "dateCreated", "dateModified", "vendor", "url_product",
        "category", "categoryId", "slug", "flexiPageHandle",
        "internalCategories", "slabPriceDescription", "slabPriceTiers",
    ]

    rows = []
    for mk, entry in all_products.items():
        doc = entry["doc"]
        meta = entry["category_meta"]
        product_id = doc["productId"]
        p = enriched_by_id.get(product_id, {})
        slab = p.get("slabPrices") or {}
        rows.append({
            "id": excel_text(product_id),
            # makroId is null for 3P/marketplace listings (confirmed live: 5/5
            # sampled 3P products had makroId=null but a valid providerSku) -
            # dict.get(key, default) does NOT fall back on an explicit None
            # value, only on a missing key, so this must be an `or` chain,
            # not a .get() default.
            "sku": excel_text(p.get("makroId") or p.get("providerSku") or mk),
            "title": p.get("titleTh") or p.get("title", ""),
            "titleEn": p.get("titleEn", ""),
            "brand": p.get("brand", ""),
            "brandTh": p.get("brandTh", ""),
            "brandEn": p.get("brandEn", ""),
            "status": p.get("status", ""),
            "barcode": excel_text(p.get("barcode")) if p.get("barcode") else "",
            "skuCode": p.get("skuCode", ""),
            "seller": p.get("seller", ""),
            "size": p.get("size", ""),
            "priceUnit": p.get("priceUnit", ""),
            "displayPrice": p.get("displayPrice", ""),
            "originPrice": p.get("originPrice", ""),
            "mainImage": p.get("mainImage", ""),
            "miraklProductId": p.get("miraklProductId", ""),
            "packagingWeight": p.get("packagingWeight", ""),
            "moq": p.get("moq", ""),
            "productOptionId": p.get("productOptionId", ""),
            "optionsLength": p.get("optionsLength", ""),
            "dateCreated": p.get("dateCreated", ""),
            "dateModified": p.get("dateModified", ""),
            "vendor": p.get("vendor", ""),
            "url_product": build_product_url(mk, product_id),
            "category": meta["category"],
            "categoryId": meta["categoryId"],
            "slug": meta["slug"],
            "flexiPageHandle": meta["flexiPageHandle"],
            "internalCategories": " | ".join(doc.get("internalCategories") or []),
            "slabPriceDescription": slab.get("slabPriceDescription", ""),
            "slabPriceTiers": json.dumps(slab.get("slabPriceTiers") or [], ensure_ascii=False),
        })

    output_path = OUTPUT_CSV_TEMPLATE.format(run_ts=run_ts, code=branch_code)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. Wrote {len(rows)} rows to {output_path}")

    conn = get_db_connection()
    if conn is not None:
        branch_name = branch_names.get(branch_code, "")
        write_rows_to_db(conn, rows, branch_code, branch_name)
        conn.close()
        print(f"Also wrote {len(rows)} rows to the database (branch {branch_code}).")
    else:
        print("DATABASE_URL not set - skipped DB write, CSV only.")

    print(f"Branch scraped: {branch_code}\n")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print(f"=== scrape_makro_categories.py started at "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    run_ts = time.strftime("%Y%m%d_%H%M%S")   # captured once, at the start of
                                                # this run - shared by every
                                                # branch's output filename
    print(f"Run started: {run_ts}")
    print(f"Target branches ({len(BRANCH_STORE_CODES)}): {BRANCH_STORE_CODES}\n")

    branch_names = load_branch_names()
    all_products: dict[str, dict] = {}   # makroId -> {"doc": search_doc, "category_meta": {...}}

    # ---- Step 1: Type A categories first ----
    for name, cid, slug in TYPE_A_CATEGORIES:
        print(f"Discovering Type A category: {name} (categoryId {cid})...")
        docs = search_by_category(cid)
        new_count = 0
        for doc in docs:
            mk = doc["makroId"]
            if mk not in all_products:
                all_products[mk] = {
                    "doc": doc,
                    "category_meta": {
                        "category": name, "categoryId": cid, "slug": slug,
                        "flexiPageHandle": "",
                    },
                }
                new_count += 1
        print(f"  -> {len(docs)} found, {new_count} new unique products")

    type_a_total = len(all_products)
    print(f"\nType A total unique products so far: {type_a_total}\n")

    # ---- Step 2: Fresh & Frozen last, dedup against Type A ----
    print(f"Discovering Type B category: {FRESH_FROZEN_NAME} (BFS crawl from '{FRESH_FROZEN_HANDLE}')...")
    collections = crawl_fresh_frozen_collections(FRESH_FROZEN_HANDLE)
    print(f"  -> {len(collections)} collections discovered via BFS (boundary-checked)")

    ff_new = 0
    ff_already_known = 0
    ff_blocked = 0
    for cid, cname in collections.items():
        docs = search_by_collection(cid)
        for doc in docs:
            mk = doc["makroId"]
            if mk in all_products:
                ff_already_known += 1
                continue
            if is_blocked_for_fresh_frozen(doc):
                ff_blocked += 1
                continue
            all_products[mk] = {
                "doc": doc,
                "category_meta": {
                    "category": FRESH_FROZEN_NAME,
                    "categoryId": FRESH_FROZEN_CATEGORY_ID,
                    "slug": FRESH_FROZEN_SLUG,
                    "flexiPageHandle": FRESH_FROZEN_HANDLE,
                },
            }
            ff_new += 1

    print(f"  -> {ff_new} genuinely new products from Fresh & Frozen, "
          f"{ff_already_known} already covered by Type A categories (skipped as duplicate), "
          f"{ff_blocked} blocked as non-food (internalCategories super-category match)\n")

    print(f"TOTAL unique products to enrich: {len(all_products)}\n")

    # ---- Step 3: up to MAX_CONCURRENT_BRANCHES branches at once, own file
    # each. Each branch is still one storeCode per GraphQL call - only the
    # orchestration (how many of these independent per-branch runs are in
    # flight at once) is different from a strictly sequential loop. ----
    makro_ids = list(all_products.keys())
    product_ids = [all_products[mk]["doc"]["productId"] for mk in makro_ids]

    print(f"Running with up to {MAX_CONCURRENT_BRANCHES} branches concurrently...\n")

    def _run_branch_safe(branch_code):
        try:
            enrich_and_write_branch(branch_code, run_ts, all_products, product_ids, branch_names)
            return branch_code, None
        except Exception as exc:  # noqa: BLE001 - one branch's failure must
            return branch_code, exc  # not take down the others in the pool

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
        print(f"\n{len(failed)} branch(es) failed and were skipped: {failed}. "
              f"Re-run with BRANCH_STORE_CODES set to just this list to retry them.")
    print(f"\nAll {len(BRANCH_STORE_CODES)} branches done. Run: {run_ts}")


if __name__ == "__main__":
    main()
