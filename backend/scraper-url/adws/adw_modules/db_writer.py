"""
Direct DB writer for the e-commerce scraper (`--output-db`) — CFW schema.

Self-contained so the scraper stays a standalone `uv run` script: this module
owns ALL database access and does NOT import backend internals. It targets the
`cfw` schema (database/cfw/01_schema.sql), which differs from the main/TWD
schema used by the feature/makro-extractor branch:
  - retailer_id is the long code ('makro'/'cfw'), not 'mk'
  - product URL/image columns are `url` / `image_url` (not link/image)
  - there is NO lowest_price/highest_price/currency/json_ld
  - step pricing lives in `step_prices` JSONB

Scope (mirrors backend/load_scraped_products.py):
  - Upsert scraped ProductData into `products`, keyed on (retailer_id, sku).
  - Register the retailer row on first write.
  - Record a `price_history` row per product that has a current_price.
  - barcode / name_en are NOT touched (the scraper doesn't capture them; they are
    seeded separately by seed_makro_products.py via the search index).
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Any, Dict
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor, Json


# Map the human retailer name emitted by extractors -> the products.retailer_id
# code used by the cfw schema. Extend as new BUs are added.
RETAILER_ID_MAP = {
    "makro": "makro",
    "central food wholesale": "cfw",
    "cfw": "cfw",
}


def _retailer_id(retailer_name: Optional[str]) -> Optional[str]:
    """Resolve a retailer display name to its products.retailer_id code."""
    if not retailer_name:
        return None
    key = " ".join(retailer_name.strip().lower().split())
    return RETAILER_ID_MAP.get(key, key.replace(" ", "")[:20] or None)


def _load_db_config() -> Dict[str, Any]:
    """Build psycopg2 connection kwargs from backend/.env.

    Prefers DATABASE_URL; falls back to individual DB_* vars, matching
    backend/database.py. Adds sslmode=require for non-local hosts (Neon).
    """
    # backend/.env is three directories up from this file:
    #   adw_modules -> adws -> scraper-url -> backend
    backend_env = Path(__file__).resolve().parents[3] / ".env"
    try:
        from dotenv import load_dotenv
        if backend_env.exists():
            load_dotenv(backend_env)
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        cfg = {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "database": (parsed.path or "/").lstrip("/"),
            "user": parsed.username,
            "password": parsed.password,
        }
        host = parsed.hostname or ""
    else:
        host = os.environ.get("DB_HOST", "localhost")
        cfg = {
            "host": host,
            "port": int(os.environ.get("DB_PORT", 5432)),
            "database": os.environ.get("DB_NAME", "pricehawk"),
            "user": os.environ.get("DB_USER", "postgres"),
            "password": os.environ.get("DB_PASSWORD", ""),
        }

    sslmode = os.environ.get("DB_SSLMODE")
    if sslmode:
        cfg["sslmode"] = sslmode
    elif host and host != "localhost":
        cfg["sslmode"] = "require"
    return cfg


def _ensure_retailer(cur, retailer_id: str, name: str, url: Optional[str]) -> None:
    """Register the retailer row if missing (safety net; migration seeds Makro/CFW)."""
    domain = None
    if url:
        try:
            domain = urlparse(url).netloc.replace("www.", "") or None
        except Exception:
            domain = None
    cur.execute(
        """
        INSERT INTO retailers (retailer_id, name, domain)
        VALUES (%s, %s, %s)
        ON CONFLICT (retailer_id) DO NOTHING
        """,
        (retailer_id, name or retailer_id, domain),
    )


def _step_prices_json(step_prices: Any) -> Json:
    """Normalize step_prices (list of tuples) to a JSONB array-of-arrays."""
    if not step_prices:
        return Json([])
    return Json([list(tier) for tier in step_prices])


def upsert_products(products: List[Any], record_history: bool = True) -> Dict[str, int]:
    """Upsert scraped ProductData rows into `products` (cfw schema).

    Returns a summary dict: {inserted, updated, skipped, errors}.
    A product is skipped (not an error) when it lacks the sku or retailer needed
    to form the (retailer_id, sku) upsert key.
    """
    summary = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    if not products:
        return summary

    # Row-isolated: each product is committed on its own so one bad row cannot
    # abort the whole batch's transaction.
    conn = psycopg2.connect(**_load_db_config(), cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            seen_retailers = set()
            for p in products:
                retailer_id = _retailer_id(getattr(p, "retailer", None))
                sku = getattr(p, "sku", None)
                sku = sku.strip() if isinstance(sku, str) else sku
                if not retailer_id or not sku:
                    summary["skipped"] += 1
                    print(
                        f"[db_writer] SKIP (missing retailer/sku): "
                        f"retailer={getattr(p, 'retailer', None)!r} sku={sku!r} "
                        f"url={getattr(p, 'url', None)!r}",
                        file=sys.stderr,
                    )
                    continue

                try:
                    if retailer_id not in seen_retailers:
                        _ensure_retailer(cur, retailer_id, getattr(p, "retailer", ""), getattr(p, "url", None))
                        conn.commit()
                        seen_retailers.add(retailer_id)

                    images = getattr(p, "images", None) or []
                    image_url = images[0] if images else None
                    current_price = getattr(p, "current_price", None)
                    step_prices = _step_prices_json(getattr(p, "step_prices", None))

                    # xmax = 0 distinguishes a fresh insert from an update.
                    cur.execute(
                        """
                        INSERT INTO products (
                            retailer_id, sku, name, brand, current_price,
                            step_prices, url, image_url, is_active,
                            created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
                        ON CONFLICT (retailer_id, sku) DO UPDATE SET
                            name          = COALESCE(EXCLUDED.name, products.name),
                            brand         = COALESCE(EXCLUDED.brand, products.brand),
                            current_price = EXCLUDED.current_price,
                            step_prices   = EXCLUDED.step_prices,
                            url           = COALESCE(EXCLUDED.url, products.url),
                            image_url     = COALESCE(EXCLUDED.image_url, products.image_url),
                            is_active     = TRUE,
                            updated_at    = NOW()
                        RETURNING id, (xmax = 0) AS inserted
                        """,
                        (
                            retailer_id,
                            sku,
                            getattr(p, "name", None) or sku,
                            getattr(p, "brand", None),
                            current_price,
                            step_prices,
                            getattr(p, "url", None),
                            image_url,
                        ),
                    )
                    row = cur.fetchone()
                    product_id = row["id"]

                    if record_history and current_price is not None:
                        cur.execute(
                            """
                            INSERT INTO price_history (product_id, price, step_prices, recorded_at)
                            VALUES (%s, %s, %s, NOW())
                            """,
                            (product_id, current_price, step_prices),
                        )

                    conn.commit()
                    if row.get("inserted"):
                        summary["inserted"] += 1
                    else:
                        summary["updated"] += 1
                except Exception as e:
                    conn.rollback()
                    summary["errors"] += 1
                    print(
                        f"[db_writer] ERROR upserting retailer={retailer_id} sku={sku}: {e}",
                        file=sys.stderr,
                    )
    finally:
        conn.close()

    return summary
