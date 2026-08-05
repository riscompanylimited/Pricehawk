import json as _json
import logging
import os
import subprocess
import urllib.request
import uuid
import re as _re
import sys

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from database import get_db
from routers.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER_SCRIPT = os.path.join(BACKEND_DIR, "scraper-url", "adws", "adw_ecommerce_product_scraper.py")
RESULTS_DIR = os.path.join(BACKEND_DIR, "results")


def normalize_url(url: str) -> str:
    if not url:
        return url
    return url.split('?')[0].rstrip('/')


def _is_scraper_browser(pinfo: dict) -> bool:
    name = pinfo['name'].lower() if pinfo['name'] else ''
    if not any(b in name for b in ['chrome', 'chromium']):
        return False
    cmdline = pinfo['cmdline'] if pinfo['cmdline'] else []
    cmdline_str = ' '.join(cmdline).lower()
    if 'playwright' in cmdline_str or 'crawl4ai' in cmdline_str:
        return True
    if any(flag in cmdline for flag in ['--disable-dev-shm-usage', '--no-sandbox']) and '--headless' in cmdline:
        has_user_profile = any(
            '--profile-directory' in str(arg) or
            ('--user-data-dir' in str(arg) and os.path.expanduser('~') in str(arg))
            for arg in cmdline
        )
        return not has_user_profile
    return False


def _cleanup_scraper_browsers(zombies_only: bool = False) -> int:
    if not PSUTIL_AVAILABLE:
        return 0
    try:
        killed_count = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pinfo = proc.info
                if not _is_scraper_browser(pinfo):
                    continue
                proc_obj = psutil.Process(pinfo['pid'])
                if zombies_only:
                    is_zombie = proc_obj.status() == psutil.STATUS_ZOMBIE
                    is_stuck = (proc_obj.cpu_percent(interval=0.1) == 0 and
                                proc_obj.create_time() < psutil.boot_time() + 300)
                    if not (is_zombie or is_stuck):
                        continue
                proc_obj.kill()
                killed_count += 1
                if not zombies_only:
                    try:
                        proc_obj.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        proc_obj.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                pass
        return killed_count
    except Exception as e:
        logger.error("[CLEANUP] Error: %s", e)
        return 0


def cleanup_zombie_browser_processes() -> int:
    return _cleanup_scraper_browsers(zombies_only=True)


def cleanup_all_scraper_browsers() -> int:
    return _cleanup_scraper_browsers(zombies_only=False)


def scrape_single_url(url: str) -> dict:
    process = None
    try:
        output_file = os.path.join(RESULTS_DIR, f"scrape_{uuid.uuid4().hex}.json")
        os.makedirs(RESULTS_DIR, exist_ok=True)

        cmd = [sys.executable, SCRAPER_SCRIPT, "--url", url, "--output-file", output_file, "--timeout", "60"]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=BACKEND_DIR, env=env, encoding="utf-8", errors="replace"
        )

        try:
            stdout, stderr = process.communicate(timeout=120)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            logger.warning("[SCRAPER] TIMEOUT: %s", url)
            if PSUTIL_AVAILABLE:
                try:
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    parent.kill()
                    process.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
            else:
                process.kill()
                process.wait(timeout=5)
            return {"success": False, "url": url, "error": "Scraper timed out"}
        finally:
            if process and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception:
                    pass

        # Forward the subprocess's [SCRAPER] lines to the server log (e.g. which extractor was used)
        for line in (stderr or "").splitlines():
            if "[SCRAPER]" in line:
                logger.info("%s", line.strip())

        if returncode != 0:
            logger.warning("[SCRAPER] FAILED rc=%d %s\nSTDERR: %s", returncode, url, stderr[-500:])
            return {"success": False, "url": url, "error": f"Scraper failed (exit {returncode})"}

        retailer_files = ["makro.json", "unknown.json"]
        output_dir = os.path.dirname(output_file)

        for fname in retailer_files:
            fpath = os.path.join(output_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        scraped_data = _json.load(f)
                    if isinstance(scraped_data, list):
                        for item in scraped_data:
                            if normalize_url(item.get('url', '')) == normalize_url(url) or item.get('url') == url:
                                item["source_url"] = url
                                logger.info("[SCRAPER] SUCCESS: %s -> %s", url, (item.get('name') or '')[:50])
                                try:
                                    os.remove(output_file)
                                except OSError:
                                    pass
                                return {"success": True, "url": url, "data": item}
                except Exception as e:
                    logger.error("[SCRAPER] Error reading %s: %s", fpath, e)

        if os.path.exists(output_file):
            try:
                with open(output_file, 'r', encoding='utf-8') as f:
                    scraped_data = _json.load(f)
                if isinstance(scraped_data, list) and scraped_data:
                    item = scraped_data[0]
                    item["source_url"] = url
                    os.remove(output_file)
                    return {"success": True, "url": url, "data": item}
            except Exception as e:
                logger.error("[SCRAPER] Error reading output file: %s", e)

        try:
            if os.path.exists(output_file):
                os.remove(output_file)
        except Exception:
            pass

        return {"success": False, "url": url, "error": "Scraper output not found"}

    except Exception as e:
        if process and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        return {"success": False, "url": url, "error": str(e)}


def _fetch_makro_price(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"success": False, "error": str(e)}

    match = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, _re.DOTALL)
    if not match:
        return {"success": False, "error": "__NEXT_DATA__ not found"}

    try:
        data = _json.loads(match.group(1))
    except Exception as e:
        return {"success": False, "error": f"JSON parse error: {e}"}

    p = data.get("props", {}).get("pageProps", {}).get("product")
    if not p:
        return {"success": False, "error": "product not found in __NEXT_DATA__"}

    current_price = None
    try:
        current_price = float(p.get("displayPrice") or 0) or None
    except (TypeError, ValueError):
        pass

    step_prices = []
    slab_data = p.get("slabPrices")
    if slab_data and isinstance(slab_data, dict):
        tiers = sorted(slab_data.get("slabPriceTiers") or [], key=lambda t: t.get("tier", 0))
        if tiers and current_price:
            step_prices = [[1, current_price]] + [
                [t["quantity"], float(t["priceInVat"])]
                for t in tiers
                if t.get("quantity") is not None and t.get("priceInVat") is not None
            ]

    images = p.get("imageUrls") or []
    return {
        "success": True,
        "current_price": current_price,
        "step_prices": step_prices,
        "image": images[0] if images else None,
    }


class ScrapeUrlRequest(BaseModel):
    urls: list[str]


@router.post("/api/scrape")
def scrape_urls(data: ScrapeUrlRequest, user: dict = Depends(get_current_user)):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    cleanup_zombie_browser_processes()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    results = []
    errors = []
    max_workers = min(len(data.urls), 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(scrape_single_url, url): url for url in data.urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                result = future.result()
                if result["success"]:
                    results.append(result["data"])
                else:
                    errors.append({"url": url, "error": result["error"]})
            except Exception as e:
                errors.append({"url": url, "error": str(e)})

    cleanup_all_scraper_browsers()
    return {"success": len(errors) == 0, "results": results, "errors": errors,
            "total_scraped": len(results), "total_errors": len(errors)}


@router.post("/api/products/{sku}/rescrape")
def rescrape_product(sku: str, user: dict = Depends(get_current_user)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM products WHERE sku = %s AND retailer_id = 'cfw'", (sku,))
            cfw_row = cur.fetchone()
            if not cfw_row:
                raise HTTPException(status_code=404, detail=f"CFW product with SKU '{sku}' not found")
            product_id = cfw_row["id"]

            cur.execute("""
                SELECT mp.id, mp.sku, mp.url FROM product_matches pm
                JOIN products mp ON pm.makro_product_id = mp.id
                WHERE pm.cfw_product_id = %s AND pm.is_verified = TRUE AND pm.is_same = TRUE
                  AND mp.url IS NOT NULL AND mp.url != ''
            """, (product_id,))
            makro_products = cur.fetchall()

    if not makro_products:
        return {"successful": 0, "failed": 0, "message": "No verified Makro matches with URLs to rescrape"}

    successful = 0
    failed = 0

    for mp in makro_products:
        result = _fetch_makro_price(mp["url"])
        if result["success"]:
            new_price = result.get("current_price")
            new_step_prices = _json.dumps(result.get("step_prices") or [])
            new_image = result.get("image")

            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE products SET current_price = %s, step_prices = %s,
                            image_url = COALESCE(%s, image_url), updated_at = NOW()
                        WHERE id = %s
                    """, (new_price, new_step_prices, new_image, mp["id"]))
                    if new_price:
                        cur.execute("INSERT INTO price_history (product_id, price, step_prices) VALUES (%s, %s, %s)",
                                    (mp["id"], new_price, new_step_prices))
                    conn.commit()
            successful += 1
        else:
            failed += 1

    return {"successful": successful, "failed": failed,
            "message": f"Updated {successful} Makro product{'s' if successful != 1 else ''}"}


@router.get("/api/products/sku/{sku}/matches")
def get_product_matches_by_sku(sku: str, user: dict = Depends(get_current_user)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, current_price, url, image_url FROM products WHERE sku = %s AND retailer_id = 'cfw'", (sku,))
            cfw_product = cur.fetchone()
            if not cfw_product:
                return {"found": False, "product": None, "matches": []}

            cur.execute("""
                SELECT mp.id as makro_id, mp.sku as makro_sku, mp.name as makro_name,
                       mp.current_price as makro_price, mp.url as makro_url,
                       mp.image_url as makro_image, pm.match_id, pm.is_verified, pm.is_same
                FROM product_matches pm
                JOIN products mp ON pm.makro_product_id = mp.id
                WHERE pm.cfw_product_id = %s AND pm.is_verified = TRUE AND pm.is_same = TRUE
                ORDER BY mp.name
            """, (cfw_product["id"],))
            matches = cur.fetchall()

            return {
                "found": True,
                "product": {
                    "id": cfw_product["id"],
                    "name": cfw_product["name"],
                    "price": float(cfw_product["current_price"]) if cfw_product["current_price"] else None,
                    "url": cfw_product["url"],
                    "image": cfw_product["image_url"],
                },
                "matches": [
                    {"retailer_id": "makro", "retailer_name": "Makro",
                     "product_id": m["makro_id"], "sku": m["makro_sku"],
                     "name": m["makro_name"],
                     "price": float(m["makro_price"]) if m["makro_price"] else None}
                    for m in matches
                ]
            }


class ManualComparisonRequest(BaseModel):
    cfw_sku: str
    makro_url: str
    scraped_data: dict | None = None


@router.post("/api/comparison/manual")
def manual_comparison(data: ManualComparisonRequest, user: dict = Depends(get_current_user)):
    scraped = data.scraped_data or {}

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, current_price FROM products WHERE sku = %s AND retailer_id = 'cfw'", (data.cfw_sku,))
            cfw_product = cur.fetchone()
            if not cfw_product:
                raise HTTPException(status_code=404, detail=f"CFW product with SKU '{data.cfw_sku}' not found")

            makro_sku = str(scraped.get('sku') or '').strip() or None
            makro_name = (scraped.get('name') or '').strip() or None
            makro_price = scraped.get('current_price')
            makro_brand = (scraped.get('brand') or '').strip() or None
            makro_image = (scraped.get('images') or [None])[0] if scraped.get('images') else None
            makro_step_prices = _json.dumps(scraped.get('step_prices') or [])

            if not makro_sku:
                raise HTTPException(status_code=400, detail="Scraped Makro product has no SKU")

            makro_category_name = (scraped.get('category') or '').strip() or None
            makro_category_id = None
            if makro_category_name:
                cur.execute("SELECT category_id FROM categories WHERE retailer_id = 'makro' AND category_name ILIKE %s LIMIT 1", (makro_category_name,))
                row = cur.fetchone()
                if row:
                    makro_category_id = row["category_id"]
                else:
                    makro_category_id = makro_category_name.upper().replace(' ', '_')[:50]
                    cur.execute(
                        "INSERT INTO categories (retailer_id, category_id, category_name) VALUES ('makro', %s, %s) ON CONFLICT (retailer_id, category_id) DO NOTHING",
                        (makro_category_id, makro_category_name)
                    )

            cur.execute("""
                INSERT INTO products (retailer_id, sku, name, brand, category_id, current_price, url, image_url, step_prices)
                VALUES ('makro', %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (retailer_id, sku) DO UPDATE SET
                    name = EXCLUDED.name, brand = EXCLUDED.brand, category_id = EXCLUDED.category_id,
                    current_price = EXCLUDED.current_price, url = EXCLUDED.url,
                    image_url = EXCLUDED.image_url, step_prices = EXCLUDED.step_prices, updated_at = NOW()
                RETURNING id
            """, (makro_sku, makro_name, makro_brand, makro_category_id,
                  makro_price, data.makro_url, makro_image, makro_step_prices))
            makro_product_id = cur.fetchone()["id"]

            cur.execute("""
                SELECT pm.match_id, mp.name as makro_name FROM product_matches pm
                JOIN products mp ON pm.makro_product_id = mp.id
                WHERE pm.cfw_product_id = %s AND pm.is_verified = TRUE AND pm.is_same = TRUE LIMIT 1
            """, (cfw_product["id"],))
            existing_verified = cur.fetchone()
            if existing_verified:
                raise HTTPException(
                    status_code=409,
                    detail=f"This CFW product already has a verified match: \"{existing_verified['makro_name']}\". Undo the existing match first before adding a new one."
                )

            cur.execute("""
                INSERT INTO product_matches (cfw_product_id, makro_product_id, match_score, is_verified, is_same, verified_at)
                VALUES (%s, %s, 100, TRUE, TRUE, NOW())
                ON CONFLICT (cfw_product_id, makro_product_id) DO UPDATE SET
                    is_verified = TRUE, is_same = TRUE, verified_at = NOW(), updated_at = NOW()
                RETURNING match_id
            """, (cfw_product["id"], makro_product_id))
            match_id = cur.fetchone()["match_id"]
            conn.commit()

            return {
                "success": True,
                "cfw_product": {"id": cfw_product["id"], "name": cfw_product["name"],
                                 "price": float(cfw_product["current_price"]) if cfw_product["current_price"] else None},
                "makro_product": {"id": makro_product_id, "sku": makro_sku, "name": makro_name,
                                   "price": float(makro_price) if makro_price else None,
                                   "url": data.makro_url, "image": makro_image},
                "match_id": match_id,
                "message": "Match created. Go to the product detail page to verify."
            }
