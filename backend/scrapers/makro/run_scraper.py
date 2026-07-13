#!/usr/bin/env python3
"""
Single entrypoint for both scraping techniques - lets Railway switch which
one runs via the SCRAPER_MODE env var instead of changing the service's
Branch/Start Command every time. Point Railway's Start Command at this file
once, then flip SCRAPER_MODE in the Variables tab to switch technique.

SCRAPER_MODE=legacy       -> scrape_makro_categories.py
                              (category/collection sweep, ~15k+ products,
                              ~1 hour across 20 branches)
SCRAPER_MODE=target_skus  -> scrape_makro_target_skus.py
                              (fixed list of ~156 known SKUs, a couple
                              minutes across 20 branches)

No default value on purpose - a missing/misconfigured SCRAPER_MODE fails
loudly here instead of silently running the wrong technique in production.
"""

import os
import sys

MODE = os.environ.get("SCRAPER_MODE")

if MODE == "legacy":
    import scrape_makro_categories as scraper
elif MODE == "target_skus":
    import scrape_makro_target_skus as scraper
else:
    sys.exit(
        f"SCRAPER_MODE must be 'legacy' or 'target_skus', got: {MODE!r}. "
        f"Set it in Railway's Variables tab for this service."
    )

if __name__ == "__main__":
    scraper.main()
