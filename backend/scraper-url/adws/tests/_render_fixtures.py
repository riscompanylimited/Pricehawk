"""One-off helper: render Makro product pages to HTML fixtures for tests.

Run from backend/ with the project venv:
    ./.venv/bin/python scraper-url/adws/tests/_render_fixtures.py

Makro's slab (step-price) section is client-rendered under "Buy more save more!"
and only appears after JS + scroll, so fixtures MUST be captured with a real
browser render (crawl4ai), not a plain fetch. This script mirrors the production
scroll behaviour so the saved HTML matches what the scraper actually sees.
"""
import asyncio
import os
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig

HERE = os.path.dirname(__file__)
FIXTURES = os.path.join(HERE, "fixtures")

# name -> url. Chosen to cover the case matrix:
#   slab + no discount      : 120948  (tiers 1->38, 4->35, 8->33)
#   slab + discount         : 868200  (299 vs 329, tier 259)
#   no slab + discount      : 835081  (45 vs 48)
#   no slab + no discount   : 813007  (83)
#   kg slab (weighed)       : 235457  (tiers "1 - 9 kg"->78/kg, "10+ kg"->75/kg)
#     — per-kg tier prices carry a "/kg" suffix; regression guard for that parse.
PRODUCTS = {
    "120948_slab_nodiscount": "https://www.makro.pro/en/p/120948-7275732730051",
    "868200_slab_discount":   "https://www.makro.pro/en/p/868200-7275693015235",
    "835081_noslab_discount": "https://www.makro.pro/en/p/835081-6974707695811",
    "813007_noslab_plain":    "https://www.makro.pro/en/p/813007-7606381740227",
    "235457_kgslab":          "https://www.makro.pro/en/p/235457-7606382067907",
}


async def main():
    os.makedirs(FIXTURES, exist_ok=True)
    browser = BrowserConfig(headless=True, user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"))
    run = CrawlerRunConfig(
        only_text=False, scan_full_page=True, scroll_delay=0.4,
        wait_until="networkidle", page_timeout=60000,
        delay_before_return_html=2.5,
    )
    async with AsyncWebCrawler(config=browser) as crawler:
        for name, url in PRODUCTS.items():
            res = await crawler.arun(url=url, config=run)
            path = os.path.join(FIXTURES, name + ".html")
            open(path, "w").write(res.html or "")
            print(f"{name}: success={res.success} len={len(res.html or '')} -> {path}")


asyncio.run(main())
