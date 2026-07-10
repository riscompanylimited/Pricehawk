-- Migration: Watchlist tables
-- Shared across all users (no user_id)

CREATE TABLE IF NOT EXISTS watchlists (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist_products (
    id SERIAL PRIMARY KEY,
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT NOW(),

    CONSTRAINT unique_watchlist_product UNIQUE (watchlist_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_products_watchlist ON watchlist_products(watchlist_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_products_product ON watchlist_products(product_id);

DROP TRIGGER IF EXISTS update_watchlists_updated_at ON watchlists;
CREATE TRIGGER update_watchlists_updated_at
    BEFORE UPDATE ON watchlists
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
