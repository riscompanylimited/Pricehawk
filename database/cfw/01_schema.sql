-- CFW / Makro Food Comparison System Schema
-- Simple schema: users, products, price_history, retailers, categories, departments, classes

-- ============================================================================
-- 0. Users
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- 1. Retailers
-- ============================================================================
CREATE TABLE IF NOT EXISTS retailers (
    retailer_id VARCHAR(20) PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT
);

INSERT INTO retailers (retailer_id, name, domain) VALUES
    ('cfw', 'CFW (Central Food Wholesale)', 'cfw.co.th'),
    ('makro', 'Makro (Wholesale Supplier)', 'makro.co.th')
ON CONFLICT (retailer_id) DO NOTHING;

-- ============================================================================
-- 2. Categories (both CFW and Makro)
-- ============================================================================
CREATE TABLE IF NOT EXISTS categories (
    retailer_id VARCHAR(20) NOT NULL REFERENCES retailers(retailer_id),
    category_id TEXT NOT NULL,
    category_name TEXT NOT NULL,
    
    PRIMARY KEY (retailer_id, category_id)
);

-- ============================================================================
-- 3. Departments (CFW only - Dept → Sub-Dept)
-- ============================================================================
CREATE TABLE IF NOT EXISTS departments (
    dept_id TEXT NOT NULL,
    dept_name TEXT NOT NULL,
    sub_dept_id TEXT NOT NULL,  -- Use 'N/A' for missing values
    sub_dept_name TEXT NOT NULL,
    
    PRIMARY KEY (dept_id, sub_dept_id)
);

-- ============================================================================
-- 4. Classes (CFW only - Class → Sub-Class)
-- ============================================================================
CREATE TABLE IF NOT EXISTS classes (
    class_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    sub_class_id TEXT NOT NULL,  -- Use 'N/A' for missing values
    sub_class_name TEXT NOT NULL,
    
    PRIMARY KEY (class_id, sub_class_id)
);

-- ============================================================================
-- 5. Products
-- ============================================================================
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    retailer_id VARCHAR(20) NOT NULL REFERENCES retailers(retailer_id),
    
    sku TEXT NOT NULL,
    barcode TEXT,
    name TEXT NOT NULL,
    name_en TEXT,
    
    brand TEXT,
    
    -- Category reference (both retailers)
    category_id TEXT,
    
    CONSTRAINT fk_category FOREIGN KEY (retailer_id, category_id) REFERENCES categories(retailer_id, category_id),
    
    -- CFW-only hierarchical categories (nullable for Makro products)
    -- Must store both parts to match composite PRIMARY KEY
    dept_id TEXT,
    sub_dept_id TEXT,
    class_id TEXT,
    sub_class_id TEXT,
    
    current_price DECIMAL(10, 2),
    
    -- Step pricing as JSONB list
    -- Makro format: [[qty, price], ...] e.g. [[1, 540], [2, 480]]
    -- CFW format: [[min, max, discount], ...] e.g. [[2, 4, 11], [5, null, 16]]
    step_prices JSONB DEFAULT '[]',
    
    url TEXT,
    image_url TEXT,
    
    is_active BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT unique_retailer_sku UNIQUE (retailer_id, sku),
    CONSTRAINT fk_dept FOREIGN KEY (dept_id, sub_dept_id) REFERENCES departments(dept_id, sub_dept_id),
    CONSTRAINT fk_class FOREIGN KEY (class_id, sub_class_id) REFERENCES classes(class_id, sub_class_id)
);

-- ============================================================================
-- 6. Price History
-- ============================================================================
CREATE TABLE IF NOT EXISTS price_history (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price DECIMAL(10, 2) NOT NULL,
    step_prices JSONB,
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- Indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_products_retailer ON products(retailer_id);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(retailer_id, category_id);
CREATE INDEX IF NOT EXISTS idx_products_dept ON products(dept_id, sub_dept_id);
CREATE INDEX IF NOT EXISTS idx_products_class ON products(class_id, sub_class_id);
CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, recorded_at);

-- ============================================================================
-- Auto-update trigger
-- ============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_products_updated_at ON products;
CREATE TRIGGER update_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
