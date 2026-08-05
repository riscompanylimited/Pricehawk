-- ============================================================================
-- 10. Per-branch step_prices for makro_location_prices
-- ============================================================================
-- Purpose: store Makro's quantity-tier "buy more save more!" slab ladder PER
--          BRANCH, in the same shape as products.step_prices
--          ([[min_qty, per_unit_price], ...], leading tier [1, current_price]).
--          Lets the price-by-location updater keep branch-specific slab pricing
--          alongside the flat branch price.
--
-- Source note: the slab hydrates client-side and is absent from the JSON-LD, but
--          Makro still embeds it in the page's __NEXT_DATA__ script
--          (props.pageProps.product.slabPrices.slabPriceTiers), so the updater
--          parses it straight from the plain-HTTP HTML. NULL when a product has
--          no slab tiers at that branch.

ALTER TABLE makro_location_prices ADD COLUMN IF NOT EXISTS step_prices JSONB;

COMMENT ON COLUMN makro_location_prices.step_prices IS
    'Quantity-tier price ladder for this product at this branch, [[min_qty, per_unit_price], ...] (matches products.step_prices). NULL if the branch has no slab pricing.';
