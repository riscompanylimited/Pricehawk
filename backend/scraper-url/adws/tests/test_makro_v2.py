"""Fixture-based tests for MakroExtractor.

Seam under test: MakroExtractor(url).extract_from_html(html, url) -> ProductData
We assert only on the public ProductData output, never on private helpers.

Fixtures are browser-rendered HTML (see _render_fixtures.py) because Makro's
slab / step-price section is client-rendered and absent from a plain fetch.

Expected values are an INDEPENDENT oracle: read from __NEXT_DATA__ (which the
extractor deliberately does NOT use) and confirmed against the visible page.

Run from backend/:
    ./.venv/bin/python scraper-url/adws/tests/test_makro_v2.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> adws/

from adw_modules.product_extractor import (  # noqa: E402
    MakroExtractor, get_extractor,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# name -> product URL (needed for sku-from-URL and as extract_from_html arg)
URLS = {
    "120948_slab_nodiscount": "https://www.makro.pro/en/p/120948-7275732730051",
    "868200_slab_discount":   "https://www.makro.pro/en/p/868200-7275693015235",
    "835081_noslab_discount": "https://www.makro.pro/en/p/835081-6974707695811",
    "813007_noslab_plain":    "https://www.makro.pro/en/p/813007-7606381740227",
    "235457_kgslab":          "https://www.makro.pro/en/p/235457-7606382067907",
}

# Oracle from __NEXT_DATA__ + visible page. Only fields exercised by the current
# slice are filled in; more get added as later slices land.
EXPECTED = {
    "120948_slab_nodiscount": {"current_price": 38.0, "sku": "120948",
                               "step_prices": [[1, 38.0], [4, 35.0], [8, 33.0]],
                               "brand": "ARO GOLD",
                               "original_price": None, "has_discount": False,
                               "name": "ARO GOLD Open Top Bread 540 g",
                               "images": ["https://images.mango-prod.siammakro.cloud/SOURCE/f32e2835935a434a910fe98e7ef96d32"],
                               "volume": "1 unit(s)", "unit_price": 38.0},
    "868200_slab_discount":   {"current_price": 299.0, "sku": "868200",
                               "step_prices": [[1, 299.0], [2, 259.0]],
                               "brand": "MAKRO",
                               "original_price": 329.0, "has_discount": True,
                               "name": "Blueberry 500 g",
                               "volume": "1 unit(s)", "unit_price": 299.0},
    "835081_noslab_discount": {"current_price": 45.0, "sku": "835081",
                               "step_prices": [],
                               "brand": "ERAWAN BRAND",
                               "original_price": 48.0, "has_discount": True,
                               "name": "ERAWAN BRAND Bua Roy 500 g",
                               "volume": "1 unit(s)", "unit_price": 45.0},
    "813007_noslab_plain":    {"current_price": 83.0, "sku": "813007",
                               "step_prices": [],
                               "brand": "MAKRO",
                               "original_price": None, "has_discount": False,
                               "name": "Chicken Boneless Breast Mince 1 kg",
                               "volume": None, "unit_price": 83.0},
    # Weighed (kg) slab product. Per-kg tier prices render with a "/kg" suffix
    # ("฿ 78/kg") that used to break price parsing and drop the whole ladder.
    # original_price is None because the strike-through renders at parity (78==78).
    "235457_kgslab":          {"current_price": 78.0, "sku": "235457",
                               "step_prices": [[1, 78.0], [10, 75.0]],
                               "brand": "MAKRO",
                               "original_price": None, "has_discount": False,
                               "name": "Chicken Boneless Breast Skin-On 1 kg",
                               "volume": None, "unit_price": 78.0},
}

_failures = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        _failures.append(msg)


def load(name):
    with open(os.path.join(FIXTURES, name + ".html")) as f:
        return f.read()


def run():
    print("routing")
    # makro.pro URLs route to MakroExtractor.
    makro_url = "https://www.makro.pro/en/p/120948-7275732730051"
    check(isinstance(get_extractor(makro_url), MakroExtractor),
          f"get_extractor -> MakroExtractor for {makro_url}")

    for name, exp in EXPECTED.items():
        print(name)
        url = URLS[name]
        product = MakroExtractor(url).extract_from_html(load(name), url)
        if "current_price" in exp:
            check(product.current_price == exp["current_price"],
                  f"current_price: got {product.current_price!r}, want {exp['current_price']!r}")
        if "sku" in exp:
            check(product.sku == exp["sku"],
                  f"sku: got {product.sku!r}, want {exp['sku']!r}")
        if "step_prices" in exp:
            got = [list(t) for t in (product.step_prices or [])]
            check(got == exp["step_prices"],
                  f"step_prices: got {got!r}, want {exp['step_prices']!r}")
        if "brand" in exp:
            check(product.brand == exp["brand"],
                  f"brand: got {product.brand!r}, want {exp['brand']!r}")
        if "original_price" in exp:
            check(product.original_price == exp["original_price"],
                  f"original_price: got {product.original_price!r}, want {exp['original_price']!r}")
        if "has_discount" in exp:
            check(product.has_discount == exp["has_discount"],
                  f"has_discount: got {product.has_discount!r}, want {exp['has_discount']!r}")
        if "name" in exp:
            check(product.name == exp["name"],
                  f"name: got {product.name!r}, want {exp['name']!r}")
        if "images" in exp:
            check(product.images == exp["images"],
                  f"images: got {product.images!r}, want {exp['images']!r}")
        if "volume" in exp:
            check(product.volume == exp["volume"],
                  f"volume: got {product.volume!r}, want {exp['volume']!r}")
        if "unit_price" in exp:
            check(product.unit_price == exp["unit_price"],
                  f"unit_price: got {product.unit_price!r}, want {exp['unit_price']!r}")
    print("alarm: JSON-LD price missing but DOM rendered")
    # Synthesize the never-yet-observed case from a real browser-rendered fixture:
    # blank the JSON-LD offers.price so current_price cannot come from JSON-LD,
    # while the DOM stays fully rendered. The extractor must NOT substitute a DOM
    # price — it must leave current_price empty, flag it, and record (only) the
    # observed DOM value so a layout change is caught the day it happens.
    name = "813007_noslab_plain"
    url = URLS[name]
    html = load(name).replace('"price":"83"', '"price":null')
    product = MakroExtractor(url).extract_from_html(html, url)
    meta = product.extraction_metadata
    check(product.current_price is None,
          f"alarm current_price left empty: got {product.current_price!r}, want None")
    check(meta.get("price_source") == "missing",
          f"alarm price_source: got {meta.get('price_source')!r}, want 'missing'")
    check(meta.get("price_missing_dom_rendered") is True,
          f"alarm flag set: got {meta.get('price_missing_dom_rendered')!r}, want True")
    # Proves the observer handles the split-<p> structure ("<p>฿</p><p>83</p>").
    check(meta.get("dom_price_observed") == 83.0,
          f"alarm dom_price_observed: got {meta.get('dom_price_observed')!r}, want 83.0")

    print("step_prices fallback: DOM slab missing -> __NEXT_DATA__")
    # Simulate a render/scroll shortfall: neutralise the DOM slab tier attributes
    # so _dom_step_prices returns [], while __NEXT_DATA__ stays intact. The ladder
    # must be rebuilt from __NEXT_DATA__ slabPriceTiers and tagged 'next_data'.
    for name in ["868200_slab_discount", "120948_slab_nodiscount", "235457_kgslab"]:
        url = URLS[name]
        html = load(name).replace('data-test-id="unit_tier_', 'data-test-id="x_tier_')
        product = MakroExtractor(url).extract_from_html(html, url)
        got = [list(t) for t in (product.step_prices or [])]
        want = EXPECTED[name]["step_prices"]
        check(got == want,
              f"[{name}] fallback step_prices: got {got!r}, want {want!r}")
        check(product.extraction_metadata.get("step_prices_source") == "next_data",
              f"[{name}] step_prices_source: got "
              f"{product.extraction_metadata.get('step_prices_source')!r}, want 'next_data'")

    print("step_prices cross-check: DOM present, agrees with __NEXT_DATA__")
    # Unmodified slab fixture: DOM wins, source is dom_slab, and no conflict is
    # recorded because DOM and __NEXT_DATA__ agree.
    name = "868200_slab_discount"
    product = MakroExtractor(URLS[name]).extract_from_html(load(name), URLS[name])
    meta = product.extraction_metadata
    check(meta.get("step_prices_source") == "dom_slab",
          f"agree source: got {meta.get('step_prices_source')!r}, want 'dom_slab'")
    check("step_prices_dom_vs_nextdata" not in meta.get("conflicts", {}),
          f"no conflict when sources agree: got {meta.get('conflicts')!r}")

    print("step_prices cross-check: DOM present, DISAGREES with __NEXT_DATA__")
    # Corrupt only the __NEXT_DATA__ tier price (259 -> 255). DOM stays the source
    # of truth ([[1,299],[2,259]]) but the mismatch must be surfaced as a conflict.
    html = load(name).replace('"priceInVat":259', '"priceInVat":255')
    product = MakroExtractor(URLS[name]).extract_from_html(html, URLS[name])
    meta = product.extraction_metadata
    got = [list(t) for t in (product.step_prices or [])]
    check(got == EXPECTED[name]["step_prices"],
          f"DOM kept on conflict: got {got!r}, want {EXPECTED[name]['step_prices']!r}")
    check(meta.get("step_prices_source") == "dom_slab",
          f"conflict source stays dom_slab: got {meta.get('step_prices_source')!r}")
    conflict = meta.get("conflicts", {}).get("step_prices_dom_vs_nextdata")
    check(conflict is not None and conflict.get("next_data") == [[1, 299.0], [2, 255.0]],
          f"conflict recorded with both ladders: got {conflict!r}")

    print()
    if _failures:
        print(f"{len(_failures)} FAILED")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    run()
