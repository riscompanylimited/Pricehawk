-- Makro multi-branch price tracking
-- A single SKU can be listed in 300+ collections and priced differently per
-- branch (confirmed: raw chicken varied ~29% across 10 sampled branches;
-- packaged goods mostly uniform with occasional branch-specific deviation).
-- See makro_er_diagram.mmd / makro_pipeline_diagram.mmd in the makro/
-- project for the full field-by-field evidence behind this schema.

-- 1. Makro Products - static PRODUCT-LEVEL fields only (same regardless of
--    which storeCodes are queried - see makro_schema_map.py classification)
CREATE TABLE IF NOT EXISTS makro_products (
    product_id VARCHAR(30) PRIMARY KEY,      -- ShopifyProduct.id - TEXT not BIGINT:
                                              -- some ids seen are 20 digits,
                                              -- exceeding BIGINT's ~19-digit range
    sku VARCHAR(30),                         -- makroId / providerSku
    sku_code VARCHAR(50),                    -- e.g. 'fresh'
    barcode VARCHAR(30),
    title_th TEXT,
    title_en TEXT,
    brand_th VARCHAR(100),
    brand_en VARCHAR(100),
    size VARCHAR(50),
    price_unit VARCHAR(10),                  -- e.g. 'THB'
    vat NUMERIC(5, 2),
    vat_code VARCHAR(5),
    main_image TEXT,
    mirakl_product_id VARCHAR(50),
    category_l1 VARCHAR(100),
    category_l2 VARCHAR(100),
    category_l3 VARCHAR(100),
    unit_length VARCHAR(20),
    unit_width VARCHAR(20),
    unit_height VARCHAR(20),
    packaging_weight NUMERIC(10, 2),
    product_option_id INTEGER,
    options_length INTEGER,
    vendor VARCHAR(20),
    installment_type VARCHAR(10),
    mbs_item_type VARCHAR(50),
    is_bulky BOOLEAN,
    is_alcohol BOOLEAN,
    is_makro_pro BOOLEAN,
    is_ultra_fresh BOOLEAN,
    is_weighted_fresh BOOLEAN,
    date_created TIMESTAMPTZ,                -- ShopifyProduct.dateCreated
    date_modified TIMESTAMPTZ,               -- ShopifyProduct.dateModified
    last_synced_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Makro Stores - branch master (built from sellerDetails.shopName/shopId
--    via GraphQL aliasing - see fetch_store_names() in makro_schema_map.py)
CREATE TABLE IF NOT EXISTS makro_stores (
    store_code VARCHAR(5) PRIMARY KEY,       -- e.g. '01', '02', '804'
    name VARCHAR(150),                       -- parsed from "Makro (ST{code}) {Name}"
    shop_id INTEGER UNIQUE,                  -- sellerDetails.shopId;
                                              -- 2002 = generic vendor context,
                                              -- NOT a real branch (confirmed on
                                              -- codes 04 and 804)
    province VARCHAR(100),                   -- manual enrichment
    postcode VARCHAR(10),                    -- manual enrichment - NO API/site
                                              -- source found yet as of this
                                              -- migration (checked GraphQL schema
                                              -- + corporate makro.co.th/th/about-branch,
                                              -- neither exposes it)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Current price snapshot per (product, store) - UPSERTED every scrape run.
--    This is the hot read path for the branch-comparison feature.
CREATE TABLE IF NOT EXISTS makro_store_prices (
    product_id VARCHAR(30) NOT NULL REFERENCES makro_products(product_id) ON DELETE CASCADE,
    store_code VARCHAR(5) NOT NULL REFERENCES makro_stores(store_code) ON DELETE CASCADE,
    mirakl_offer_id VARCHAR(30),             -- variants[].miraklOfferId - confirmed
                                              -- distinct per branch (separate listing)
    display_price NUMERIC(12, 2),            -- variants[].price
    compare_at_price NUMERIC(12, 2),         -- variants[].compareAtPrice - equals the
                                              -- ambiguous top-level originPrice for
                                              -- that store; always read it from here
    stored_price NUMERIC(12, 2),            -- variants[].storedPrice - PER-KG price,
                                              -- different unit than display_price
    stored_compare_at_price NUMERIC(12, 2),
    cost_price NUMERIC(12, 4),               -- variants[].costPrice - wholesale cost;
                                              -- confirmed to vary slightly per branch;
                                              -- internal margin analysis only, never
                                              -- expose in the customer-facing feature
    inventory_qty INTEGER,
    status VARCHAR(20),                      -- confirmed store-dependent, not constant
    moq NUMERIC(10, 2),                      -- confirmed store-dependent, not constant
    variant_date_created TIMESTAMPTZ,        -- per-branch listing creation date
    variant_date_modified TIMESTAMPTZ,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (product_id, store_code)
);

-- 4. Price change log - INSERT ONLY WHEN PRICE ACTUALLY CHANGED, not one row
--    per scrape run. Justified empirically: 21/30 sampled SKUs had zero price
--    variance across 10 branches, so blind append-per-scrape would be mostly
--    duplicate rows. The scraper must diff against the current
--    makro_store_prices row before writing here.
CREATE TABLE IF NOT EXISTS makro_store_price_history (
    id BIGSERIAL PRIMARY KEY,
    product_id VARCHAR(30) NOT NULL REFERENCES makro_products(product_id) ON DELETE CASCADE,
    store_code VARCHAR(5) NOT NULL REFERENCES makro_stores(store_code) ON DELETE CASCADE,
    display_price NUMERIC(12, 2),
    compare_at_price NUMERIC(12, 2),
    scraped_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Collections (a SKU can belong to 300+; confirmed on real data)
CREATE TABLE IF NOT EXISTS makro_collections (
    collection_id VARCHAR(30) PRIMARY KEY,
    title TEXT,
    handle VARCHAR(200)
);

-- 6. Product <-> Collection (many-to-many)
CREATE TABLE IF NOT EXISTS makro_product_collections (
    product_id VARCHAR(30) NOT NULL REFERENCES makro_products(product_id) ON DELETE CASCADE,
    collection_id VARCHAR(30) NOT NULL REFERENCES makro_collections(collection_id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, collection_id)
);

-- 7. Slab (bulk-quantity) price tiers - AMBIGUOUS at product level in the API,
--    must be fetched per store, one storeCode at a time (same technique as
--    store-name aliasing) rather than trusted from a multi-store batch call.
CREATE TABLE IF NOT EXISTS makro_slab_tiers (
    id BIGSERIAL PRIMARY KEY,
    product_id VARCHAR(30) NOT NULL REFERENCES makro_products(product_id) ON DELETE CASCADE,
    store_code VARCHAR(5) NOT NULL REFERENCES makro_stores(store_code) ON DELETE CASCADE,
    tier INTEGER,
    quantity_breakpoint NUMERIC(10, 2),
    discount NUMERIC(10, 2),
    price_in_vat NUMERIC(12, 2),
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (product_id, store_code, tier)
);

-- Indexes for the branch-comparison feature's hot query path
CREATE INDEX IF NOT EXISTS idx_makro_store_prices_product ON makro_store_prices(product_id);
CREATE INDEX IF NOT EXISTS idx_makro_store_prices_store ON makro_store_prices(store_code);
CREATE INDEX IF NOT EXISTS idx_makro_store_price_history_lookup ON makro_store_price_history(product_id, store_code, scraped_at);
CREATE INDEX IF NOT EXISTS idx_makro_product_collections_collection ON makro_product_collections(collection_id);
CREATE INDEX IF NOT EXISTS idx_makro_slab_tiers_product_store ON makro_slab_tiers(product_id, store_code);
