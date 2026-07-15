"""
Product data extraction module for e-commerce websites.

This module provides extraction strategies and utilities for extracting
product information from various e-commerce website structures.
"""

import re
import os
import sys
import json
from typing import Dict, List, Any, Optional, Union
from urllib.parse import urljoin, urlparse

from .product_schemas import ProductData, PriceParser, normalize_product_data


class ProductExtractor:
    """Extracts product data from e-commerce web pages."""

    def __init__(self, base_url: str = None):
        """Initialize the extractor with base URL for resolving relative URLs."""
        self.base_url = base_url

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data from HTML content.

        Args:
            html_content: HTML content of the product page
            url: URL of the product page (for context)

        Returns:
            ProductData object or None if extraction failed
        """
        if not html_content:
            return None

        # Set base URL if provided
        if url:
            self.base_url = url

        # Extract all product information
        raw_data = {}

        # Extract basic product information
        # Extract retailer from URL
        raw_data['retailer'] = self._extract_retailer_from_url(url)
        raw_data['name'] = self._extract_product_name(html_content)
        raw_data['description'] = self._extract_description(html_content)
        raw_data['brand'] = self._extract_brand(html_content)
        raw_data['model'] = self._extract_model(html_content)
        raw_data['sku'] = self._extract_sku(html_content)
        raw_data['category'] = self._extract_category(html_content)

        # Extract pricing information
        current_price, original_price = self._extract_prices(html_content)
        raw_data['current_price'] = current_price
        raw_data['original_price'] = original_price

        # Extract product specifications
        raw_data['volume'] = self._extract_volume(html_content)
        raw_data['dimensions'] = self._extract_dimensions(html_content)
        raw_data['material'] = self._extract_material(html_content)
        raw_data['color'] = self._extract_color(html_content)

        # Apply systematic field sanitization
        if raw_data['brand']:
            raw_data['brand'] = self._sanitize_brand_field(raw_data['brand'])

        if raw_data['model']:
            # Prevent HTML element names as model values
            clean_model = self._sanitize_text_field(raw_data['model'], max_length=50)
            # Reject common HTML element names
            html_elements = ['html', 'body', 'div', 'span', 'section', 'article', 'header', 'footer']
            if clean_model and clean_model.lower() not in html_elements:
                raw_data['model'] = clean_model
            else:
                raw_data['model'] = None

        if raw_data['sku']:
            raw_data['sku'] = self._sanitize_sku_field(raw_data['sku'])

        if raw_data['color']:
            raw_data['color'] = self._sanitize_color_field(raw_data['color'])

        if raw_data['dimensions']:
            raw_data['dimensions'] = self._sanitize_dimensions_field(raw_data['dimensions'])

        if raw_data['material']:
            raw_data['material'] = self._sanitize_material_field(raw_data['material'])

        if raw_data['volume']:
            raw_data['volume'] = self._sanitize_text_field(raw_data['volume'], max_length=50)

        if raw_data['category']:
            raw_data['category'] = self._sanitize_text_field(raw_data['category'], max_length=100)

        # Extract images
        raw_data['images'] = self._extract_images(html_content)

        # Add URL
        raw_data['url'] = url or self.base_url

        # Normalize and validate data
        normalized_data = normalize_product_data(raw_data)

        # Create ProductData object
        try:
            return ProductData(**normalized_data)
        except Exception as e:
            print(f"Error creating ProductData: {e}")
            return None

    def extract_from_json_ld(self, html_content: str) -> Dict[str, Any]:
        """Extract product data from JSON-LD structured data.

        Args:
            html_content: HTML content containing JSON-LD

        Returns:
            Dictionary with extracted product data
        """
        json_ld_data = {}

        # Find JSON-LD scripts
        json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(json_ld_pattern, html_content, re.DOTALL | re.IGNORECASE)

        for match in matches:
            try:
                data = json.loads(match.strip())
                if isinstance(data, dict):
                    # Handle single object
                    if data.get('@type') in ['Product', 'ProductModel']:
                        json_ld_data.update(self._parse_json_ld_product(data))
                    # Handle array of objects
                    elif isinstance(data.get('@graph'), list):
                        for item in data['@graph']:
                            if item.get('@type') in ['Product', 'ProductModel']:
                                json_ld_data.update(self._parse_json_ld_product(item))
            except json.JSONDecodeError:
                continue

        return json_ld_data

    def _parse_json_ld_product(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse product data from JSON-LD structure."""
        parsed = {}

        # Basic fields
        parsed['name'] = data.get('name')
        parsed['description'] = data.get('description')
        parsed['brand'] = data.get('brand', {}).get('name') if isinstance(data.get('brand'), dict) else data.get('brand')
        parsed['model'] = data.get('model')
        parsed['sku'] = data.get('sku')
        parsed['category'] = data.get('category', [None])[0] if isinstance(data.get('category'), list) else data.get('category')

        # Pricing
        offers = data.get('offers')
        if isinstance(offers, dict):
            parsed['current_price'] = offers.get('price')
            parsed['original_price'] = offers.get('highPrice') or offers.get('price') if offers.get('priceSpecification', {}).get('highPrice') else None
        elif isinstance(offers, list) and offers:
            offer = offers[0]  # Take first offer
            parsed['current_price'] = offer.get('price')
            parsed['original_price'] = offer.get('highPrice')

        # Images
        images = data.get('image')
        if isinstance(images, list):
            parsed['images'] = images
        elif isinstance(images, str):
            parsed['images'] = [images]

        return parsed

    def _extract_product_name(self, html_content: str) -> Optional[str]:
        """Extract product name from HTML."""
        # Common selectors for product name
        patterns = [
            r'<h1[^>]*class="[^"]*product[^"]*"[^>]*>(.*?)</h1>',
            r'<h1[^>]*>(.*?)</h1>',
            r'<title[^>]*>(.*?)</title>',
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
            r'<div[^>]*class="[^"]*product-title[^"]*"[^>]*>(.*?)</div>',
            r'<span[^>]*class="[^"]*product-name[^"]*"[^>]*>(.*?)</span>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                name = self._clean_text(val)

                # Filter out retailer names and site branding
                if name:
                    # Common retailer names and site branding to filter out
                    retailer_names = [
                        'megahome', 'mega home', 'homepro', 'home pro', 'boonthavorn', 'dohome', 'do home',
                        'global house', 'thai watsadu', 'watsadu', 'power buy', 'powerbuy',
                        'lazada', 'shopee', 'central', 'jaymart', 'Vivin', 'banner'
                    ]

                    # Check if the name is just a retailer name (case insensitive)
                    name_lower = name.lower().strip()
                    if name_lower in retailer_names:
                        continue  # Skip this pattern and try the next one

                    # Check if the name starts with a retailer name and clean it
                    for retailer in retailer_names:
                        if name_lower.startswith(retailer + ' '):
                            # Remove retailer prefix
                            name = name[len(retailer):].strip()
                            if not name or len(name) < 3:
                                continue  # Skip if name becomes too short after cleaning
                            break

                if name and len(name) > 3:  # Minimum length check
                    return name

        return None

    def _extract_description(self, html_content: str) -> Optional[str]:
        """Extract product description from HTML."""
        patterns = [
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
            r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*product-description[^"]*"[^>]*>(.*?)</div>',
            r'<p[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</p>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                desc = self._clean_text(val)
                if desc and len(desc) > 10:
                    return desc

        return None

    def _extract_brand(self, html_content: str) -> Optional[str]:
        """Extract product brand from HTML with enhanced patterns and JSON-LD support."""
        # First try JSON-LD extraction
        json_ld_data = self.extract_from_json_ld(html_content)
        if json_ld_data.get('brand'):
            brand_value = json_ld_data['brand']
            if isinstance(brand_value, dict):
                brand_value = brand_value.get('name')
            if brand_value:
                clean_brand = self._sanitize_brand_field(str(brand_value))
                if clean_brand:
                    return clean_brand

        # HTML patterns for brand extraction
        patterns = [
            r'<meta[^>]*property=["\']og:brand["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*name=["\']brand["\'][^>]*content=["\']([^"\']+)["\']',
            r'<span[^>]*class="[^"]*brand[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*brand[^"]*"[^>]*>(.*?)</div>',
            r'<a[^>]*class="[^"]*brand[^"]*"[^>]*>(.*?)</a>',
            r'ยี่ห้อ[:\s]*([^\n<]+)',
            r'แบรนด์[:\s]*([^\n<]+)',
            r'Brand[:\s]*([^\n<]+)',
            r'Manufacturer[:\s]*([^\n<]+)',
            # Thai variations
            r'ผู้ผลิต[:\s]*([^\n<]+)',
            r'เครื่องหมาย[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                brand = self._clean_text(val)
                if brand and len(brand) > 1:
                    # Additional sanitization to prevent contamination
                    clean_brand = self._sanitize_brand_field(brand)
                    if clean_brand:
                        return clean_brand

        # Try to extract from title (first word is often brand)
        title_patterns = [
            r'<title[^>]*>(.*?)</title>',
            r'<h1[^>]*class="[^"]*product-title[^"]*"[^>]*>(.*?)</h1>',
            r'<h1[^>]*>(.*?)</h1>',
        ]

        for pattern in title_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                title_text = self._clean_text(match.group(1))
                if title_text:
                    # First word or phrase might be brand
                    words = title_text.split()
                    if len(words) >= 2:
                        potential_brand = ' '.join(words[:2])  # Try first two words
                        clean_brand = self._sanitize_brand_field(potential_brand)
                        if clean_brand and len(clean_brand) >= 3:
                            # Validate it looks like a brand (starts with capital)
                            if clean_brand[0].isupper():
                                return clean_brand

        return None

    def _extract_model(self, html_content: str) -> Optional[str]:
        """Extract product model from HTML with enhanced patterns and contamination prevention."""
        # First try JSON-LD extraction
        json_ld_data = self.extract_from_json_ld(html_content)
        if json_ld_data.get('model'):
            model_value = json_ld_data['model']
            if model_value:
                clean_model = self._sanitize_text_field(str(model_value), max_length=200)
                if clean_model:
                    return clean_model

        # HTML patterns for model extraction
        patterns = [
            r'<span[^>]*class="[^"]*model[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*model[^"]*"[^>]*>(.*?)</div>',
            r'<meta[^>]*property=["\']product:model["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*name=["\']model["\'][^>]*content=["\']([^"\']+)["\']',
            r'รุ่น[:\s]*([^\n<]+)',
            r'โมเดล[:\s]*([^\n<]+)',
            r'Model[:\s]*([^\n<]+)',
            r'Model No[:\s]*([^\n<]+)',
            r'Model Number[:\s]*([^\n<]+)',
            r'Type[:\s]*([^\n<]+)',
            r'แบบ[:\s]*([^\n<]+)',
            r'รหัสแบบ[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                model = self._clean_text(val)
                if model and len(model) > 1:
                    # Additional sanitization to prevent contamination
                    clean_model = self._sanitize_text_field(model, max_length=200)
                    if clean_model:
                        return clean_model

        # Try to extract model from title and description using regex patterns
        text_sources = []

        # Add title to sources
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.IGNORECASE)
        if title_match:
            text_sources.append(self._clean_text(title_match.group(1)))

        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.IGNORECASE)
        if h1_match:
            text_sources.append(self._clean_text(h1_match.group(1)))

        # Add description to sources
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if desc_match:
            text_sources.append(self._clean_text(desc_match.group(1)))

        # Model extraction patterns from text
        model_patterns = [
            r'รุ่น\s+([A-Za-z0-9\-\_]+)',
            r'โมเดล\s+([A-Za-z0-9\-\_]+)',
            r'Model[:\s]+([A-Za-z0-9\-\_]+)',
            r'Type[:\s]+([A-Za-z0-9\-\_]+)',
            r'([A-Z]{2,4}-?\d{3,6})',  # Common model pattern like "ABC-1234"
            r'([A-Z][a-z]*-\d+[A-Za-z]*)',  # Pattern like "Product-123X"
        ]

        for text_source in text_sources:
            if text_source:
                for pattern in model_patterns:
                    match = re.search(pattern, text_source, re.IGNORECASE)
                    if match:
                        potential_model = match.group(1).strip()
                        clean_model = self._sanitize_text_field(potential_model, max_length=200)
                        if clean_model and len(clean_model) >= 2:
                            return clean_model

        return None

    def _extract_sku(self, html_content: str) -> Optional[str]:
        """Extract product SKU from HTML with enhanced validation to prevent URL contamination."""
        patterns = [
            r'<span[^>]*class="[^"]*sku[^"]*"[^>]*>(.*?)</span>',
            r'<meta[^>]*property=["\']product:retailer_item_id["\'][^>]*content=["\']([^"\']+)["\']',
            r'รหัสสินค้า[:\s]*([^\n<]+)',
            r'SKU[:\s]*([^\n<]+)',
            r'Article No[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                sku = self._clean_text(val)
                if sku and len(sku) > 1:
                    # Apply enhanced sanitization
                    clean_sku = self._sanitize_sku_field(sku)
                    if clean_sku:
                        return clean_sku

        # Try to extract from URL (enhanced to extract meaningful product codes)
        if self.base_url:
            url_patterns = [
                r'/product/([^/]+?)(?:/|$)',
                r'/item/([^/]+?)(?:/|$)',
                r'/p/([^/]+?)(?:/|$)',
                r'sku[=/]([^/&]+)',
                r'/(\d{6,})',  # Extract numeric product codes
                r'-([A-Z0-9]{4,})',  # Extract alphanumeric codes at end
            ]
            for pattern in url_patterns:
                match = re.search(pattern, self.base_url, re.IGNORECASE)
                if match:
                    potential_sku = match.group(1).strip()
                    # Validate the extracted URL component is a meaningful SKU
                    if self._is_valid_sku(potential_sku):
                        return potential_sku

        return None

    def _sanitize_sku_field(self, sku: str) -> Optional[str]:
        """Specialized sanitization for SKU field to prevent URL contamination."""
        if not sku:
            return None

        # Apply general sanitization first
        sku = self._sanitize_text_field(sku, max_length=50)
        if not sku:
            return None

        # Additional SKU-specific validation
        if self._is_valid_sku(sku):
            return sku

        return None

    def _is_valid_sku(self, sku: str) -> bool:
        """Validate that SKU is actually a SKU and not a URL or other invalid data."""
        if not sku:
            return False

        # Must be alphanumeric with reasonable length
        if len(sku) < 2 or len(sku) > 50:
            return False

        # Must not contain URLs or domains (more specific patterns)
        sku_lower = sku.lower()
        if (sku_lower.startswith(('http', 'https', 'www')) or
            any(domain in sku_lower for domain in ['.com', '.co.th', '.net', '.org']) or
            '/product/' in sku_lower or
            '/item/' in sku_lower or
            '/category/' in sku_lower or
            '/search/' in sku_lower or
            '/page/' in sku_lower):
            return False

        # Must not contain path separators (except valid SKU patterns with hyphens)
        if '/' in sku or '\\' in sku:
            return False

        # Must not be just numeric dates
        if re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}$', sku):
            return False

        # Allow alphanumeric, hyphens, and underscores
        if re.match(r'^[A-Za-z0-9\-_]+$', sku):
            return True

        return False

    def _extract_category(self, html_content: str) -> Optional[str]:
        """Extract product category from HTML."""
        patterns = [
            r'<nav[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>.*?</nav>',
            r'<div[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>.*?</div>',
            r'หมวดหมู่[:\s]*([^\n<]+)',
            r'Category[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                # Use group 1 if available, otherwise group 0
                val = match.group(1) if match.re.groups > 0 else match.group(0)
                category = self._clean_text(val)
                if category and len(category) > 1:
                    # If it's breadcrumb, take the last part
                    if '>' in category:
                        parts = [p.strip() for p in category.split('>')]
                        category = parts[-1] if parts else category
                    return category

        return None

    def _extract_prices(self, html_content: str) -> tuple[Optional[float], Optional[float]]:
        """Extract current and original prices from HTML."""
        # Extract price information
        price_patterns = [
            r'<span[^>]*class="[^"]*price[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*price[^"]*"[^>]*>(.*?)</div>',
            r'<meta[^>]*property=["\']product:price:amount["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([^"\']+)["\']',
        ]

        all_price_text = []
        for pattern in price_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            all_price_text.extend(matches)

        # Also look for prices in text content
        price_text_patterns = [
            r'ราคา[:\s]*([฿$]?[\d,]+\.?\d*)',
            r'Price[:\s]*([฿$]?[\d,]+\.?\d*)',
            r'([฿$]?[\d,]+\.?\d*)\s*บาท',
        ]

        for pattern in price_text_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            all_price_text.extend(matches)

        # Original price patterns
        original_price_patterns = [
            r'<span[^>]*class="[^"]*original[^"]*price[^"]*"[^>]*>(.*?)</span>',
            r'<span[^>]*class="[^"]*was[^"]*"[^>]*>(.*?)</span>',
            r'<div[^>]*class="[^"]*original-price[^"]*"[^>]*>(.*?)</div>',
            r'ราคาปกติ[:\s]*([฿$]?[\d,]+\.?\d*)',
            r'ปกติ[:\s]*([฿$]?[\d,]+\.?\d*)',
        ]

        original_prices = []
        for pattern in original_price_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            original_prices.extend(matches)

        # Parse all found prices
        all_prices = [PriceParser.parse_price(price) for price in all_price_text]
        all_prices = [p for p in all_prices if p is not None]

        orig_prices = [PriceParser.parse_price(price) for price in original_prices]
        orig_prices = [p for p in orig_prices if p is not None]

        current_price = None
        original_price = None

        if orig_prices:
            original_price = max(orig_prices)  # Highest price is likely original price
            # Current price is the lowest price that's not the original
            other_prices = [p for p in all_prices if p != original_price]
            current_price = min(other_prices) if other_prices else None
        elif all_prices:
            # If no clear original price, use min/max logic
            if len(all_prices) >= 2:
                original_price = max(all_prices)
                current_price = min(all_prices)
            else:
                current_price = all_prices[0]

        return current_price, original_price

    def _extract_volume(self, html_content: str) -> Optional[str]:
        """Extract product volume/capacity from HTML."""
        patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:ลิตร|L|l)(?:\s*ตัน)?',
            r'(\d+(?:\.\d+)?)\s*(?:มล|ml|ML)',
            r'(\d+(?:\.\d+)?)\s*(?:แกลลอน|gallon)',
            r'ความจุ[:\s]*([^\n<]+)',
            r'Volume[:\s]*([^\n<]+)',
            r'Capacity[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                volume = self._clean_text(match.group(1) if match.groups() else match.group(0))
                if volume:
                    return volume

        return None

    def _extract_dimensions(self, html_content: str) -> Optional[str]:
        """Extract product dimensions from HTML with enhanced sanitization."""
        patterns = [
            r'(\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?)\s*(?:ซม|cm|mm|m)',
            r'ขนาด[:\s]*([^\n<]+)',
            r'Dimension[:\s]*([^\n<]+)',
            r'Size[:\s]*([^\n<]+)',
            r'(\d+(?:\.\d+)?)\s*ซม',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                dimensions = self._clean_text(match.group(1) if match.groups() else match.group(0))
                if dimensions:
                    # Apply specialized dimensions sanitization
                    clean_dimensions = self._sanitize_dimensions_field(dimensions)
                    if clean_dimensions:
                        return clean_dimensions

        return None

    def _extract_material(self, html_content: str) -> Optional[str]:
        """Extract product material from HTML with enhanced sanitization."""
        patterns = [
            r'วัสดุ[:\s]*([^\n<]+)',
            r'Material[:\s]*([^\n<]+)',
            r'ผลิตจาก[:\s]*([^\n<]+)',
            r'เนื้อวัสดุ[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                material = self._clean_text(match.group(1))
                if material and len(material) > 1:
                    # Apply specialized material sanitization
                    clean_material = self._sanitize_material_field(material)
                    if clean_material:
                        return clean_material

        return None

    def _extract_color(self, html_content: str) -> Optional[str]:
        """Extract product color from HTML with enhanced sanitization."""
        patterns = [
            r'สี[:\s]*([^\n<]+)',
            r'Color[:\s]*([^\n<]+)',
            r'สีแบบ[:\s]*([^\n<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                color = self._clean_text(match.group(1))
                if color and len(color) > 1:
                    # Apply specialized color sanitization
                    clean_color = self._sanitize_color_field(color)
                    if clean_color:
                        return clean_color

        return None

    def _extract_images(self, html_content: str) -> List[str]:
        """Extract product image URLs from HTML."""
        images = []

        # Product image patterns
        patterns = [
            r'<img[^>]*class="[^"]*product[^"]*image[^"]*"[^>]*src=["\']([^"\']+)["\']',
            r'<img[^>]*class="[^"]*product-image[^"]*"[^>]*src=["\']([^"\']+)["\']',
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*property=["\']product:image["\'][^>]*content=["\']([^"\']+)["\']',
            r'<img[^>]*src=["\']([^"\']*product[^"\']*)["\']',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                img_url = self._resolve_url(match.strip())
                if img_url and img_url not in images:
                    images.append(img_url)

        # Also check JSON-LD for images
        json_ld_data = self.extract_from_json_ld(html_content)
        if 'images' in json_ld_data:
            for img_url in json_ld_data['images']:
                resolved_url = self._resolve_url(img_url)
                if resolved_url and resolved_url not in images:
                    images.append(resolved_url)

        return images[:10]  # Limit to 10 images

    def _resolve_url(self, url: str) -> Optional[str]:
        """Resolve relative URL to absolute URL."""
        if not url:
            return None

        # Skip data URLs and invalid URLs
        if url.startswith(('data:', 'mailto:', 'tel:', 'javascript:')):
            return None

        # If already absolute, return as is
        if url.startswith(('http://', 'https://')):
            return url

        # Resolve relative URL
        if self.base_url:
            try:
                return urljoin(self.base_url, url)
            except Exception:
                pass

        return url

    def _extract_retailer_from_url(self, url: str) -> str:
        """Extract retailer name from URL domain."""
        if not url:
            return "Unknown Retailer"

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove www. prefix
            domain = domain.replace('www.', '')

            # Known retailer mappings
            retailer_mappings = {
                'advice.co.th': 'Advice',
                'banana-it': 'Banana IT',
                'dohome.co.th': 'DoHome',
                'globalhouse.co.th': 'Global House',
                'homepro.co.th': 'HomePro',
                'jaymart.co.th': 'Jaymart',
                'lazada.co.th': 'Lazada',
                'megahome.co.th': 'Mega Home',
                'powerbuy.co.th': 'Power Buy',
                'shopee.co.th': 'Shopee',
                'thaiwatsadu.com': 'Thai Watsadu',
            }

            for domain_pattern, retailer in retailer_mappings.items():
                if domain_pattern in domain:
                    return retailer

            # Extract domain name as fallback
            domain_parts = domain.split('.')
            if len(domain_parts) >= 2:
                return domain_parts[-2].title()

            return domain.title()

        except Exception:
            return "Unknown Retailer"

    def _clean_text(self, text: str) -> str:
        """Clean extracted text by removing HTML tags and extra whitespace."""
        if not text:
            return ""

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)

        # Replace HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')

        # Clean whitespace
        text = ' '.join(text.split())

        return text.strip()

    def _sanitize_text_field(self, text: str, max_length: int = 100) -> Optional[str]:
        """Enhanced field sanitization to prevent HTML/CSS/JSON contamination and ensure clean data."""
        if not text:
            return None

        # Remove HTML/CSS class names and attributes patterns
        css_patterns = [
            r'class="[^"]*"',
            r'quickInfo-infoLabel-[^"\s]*',
            r'quickInfo-infoValue-[^"\s]*',
            r'style="[^"]*"',
            r'id="[^"]*"',
            r'<label[^>]*>',
            r'</label>',
            r'<span[^>]*>',
            r'</span>',
            r'<div[^>]*>',
            r'</div>',
        ]

        for pattern in css_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Remove URLs and domain names from text fields
        url_patterns = [
            r'https?://[^\s<>"\']+',
            r'www\.[^\s<>"\']+',
            r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s<>"\']*)?',
        ]

        for pattern in url_patterns:
            text = re.sub(pattern, '', text)

        # Remove JSON-like structures and objects
        text = re.sub(r'\{[^}]*\}', '', text)
        text = re.sub(r'\[[^\]]*\]', '', text)

        # Remove common JSON keys and values
        json_patterns = [
            r'"name"\s*:\s*"[^"]*"',
            r'"type"\s*:\s*"[^"]*"',
            r'"@[^"]*"\s*:\s*"[^"]*"',
            r'"[^"]*"\s*:\s*"[^"]*"',
            r'true|false|null',
            r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}',  # ISO dates
        ]

        for pattern in json_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Remove excessive punctuation and special characters
        text = re.sub(r'[{}[\]"\',:;\\<>]', '', text)

        # Handle trailing comma truncation by removing trailing commas
        text = text.rstrip(',;')

        # Clean whitespace again
        text = ' '.join(text.split())

        # Validate the cleaned text
        if (text and
            len(text) > 1 and
            len(text) <= max_length and
            not text.lower().startswith(('http', 'www', 'data:', 'class=', 'style=')) and
            not any(char in text for char in ['{', '}', '[', ']', '"', "'", '\\', '<', '>', '='])):
            return text.strip()

        return None

    def _sanitize_dimensions_field(self, dimensions: str) -> Optional[str]:
        """Specialized sanitization for dimensions field."""
        if not dimensions:
            return None

        # Remove CSS variables first
        css_variable_patterns = [
            r'var\([^)]+\)',
        ]

        for pattern in css_variable_patterns:
            dimensions = re.sub(pattern, '', dimensions, flags=re.IGNORECASE)

        # Extract dimension patterns more precisely before general sanitization
        dim_patterns = [
            r'(\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)',
        ]

        # First try to extract dimension pattern
        for pattern in dim_patterns:
            match = re.search(pattern, dimensions)
            if match:
                clean_dim = match.group(1).strip()
                if clean_dim and len(clean_dim) <= 200:
                    return clean_dim

        # If no pattern found, apply general sanitization
        dimensions = self._sanitize_text_field(dimensions, max_length=200)
        if dimensions and len(dimensions) <= 200:
            return dimensions

        return None

    def _sanitize_color_field(self, color: str) -> Optional[str]:
        """Specialized sanitization for color field to prevent CSS color codes."""
        if not color:
            return None

        # Remove CSS color codes and patterns first
        css_color_patterns = [
            r'#[0-9a-fA-F]{3,6}',
            r'rgb\([^)]+\)',
            r'rgba\([^)]+\)',
            r'hsl\([^)]+\)',
            r'hsla\([^)]+\)',
            r'color:\s*[^;\\]+',
            r'background:\s*[^;\\]+',
            r'var\([^)]+\)',
        ]

        for pattern in css_color_patterns:
            color = re.sub(pattern, '', color, flags=re.IGNORECASE)

        # Final cleanup and validation
        color = ' '.join(color.split()).strip()

        # Apply general sanitization
        color = self._sanitize_text_field(color, max_length=50)
        if not color:
            return None

        # Validate color is not a code
        if (color and
            len(color) >= 2 and
            len(color) <= 50 and
            not color.startswith(('#', 'rgb', 'hsl')) and
            not re.match(r'^[0-9a-fA-F]{3,6}$', color)):
            return color

        return None

    def _sanitize_material_field(self, material: str) -> Optional[str]:
        """Specialized sanitization for material field."""
        if not material:
            return None

        # Remove common prefixes first
        material = re.sub(r'วัสดุ\s*[:\s]*|Material\s*[:\s]*|ผลิตจาก\s*[:\s]*|เนื้อวัสดุ\s*[:\s]*', '', material, flags=re.IGNORECASE)
        material = ' '.join(material.split()).strip()

        # Apply general sanitization
        material = self._sanitize_text_field(material, max_length=100)
        if not material:
            return None

        if material and len(material) >= 2 and len(material) <= 100:
            return material

        return None

    def _sanitize_brand_field(self, brand: str) -> Optional[str]:
        """Sanitize brand field to prevent JSON contamination and ensure clean data."""
        return self._sanitize_text_field(brand, max_length=100)


# Retailer-specific extractors
class ThaiWatsaduExtractor(ProductExtractor):
    """Specialized extractor for Thai Watsadu website with enhanced sanitization."""

    # Thai Watsadu specific strings to filter out
    THAIWATSADU_FILTER_STRINGS = [
        'ไทวัสดุ',
        'thaiwatsadu',
        'ครบเรื่องบ้าน ถูกและดี',
        'ครบเรื่องบ้าน',
        'ถูกและดี',
    ]

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from Thai Watsadu with enhanced sanitization."""
        product = ProductData(url=url)

        # 1. Extract SKU from URL first (most reliable for Thai Watsadu)
        # URL pattern: /product/...-60272160 or /th/sku/60272160
        if url:
            # Try pattern: -SKU at end of URL
            sku_match = re.search(r'-(\d{8})(?:\?|$)', url)
            if sku_match:
                product.sku = sku_match.group(1)
            else:
                # Try pattern: /sku/SKU
                sku_match = re.search(r'/sku/(\d+)', url)
                if sku_match:
                    product.sku = sku_match.group(1)

        # 2. Try JSON-LD extraction
        json_ld_data = self._extract_json_ld(html_content)

        if json_ld_data:
            product.name = self._clean_thaiwatsadu_text(json_ld_data.get('name'))
            product.description = self._clean_thaiwatsadu_text(json_ld_data.get('description'), strip_html=True)

            brand_value = json_ld_data.get('brand')
            if brand_value:
                if isinstance(brand_value, dict):
                    brand_value = brand_value.get('name')
                if brand_value:
                    product.brand = self._clean_thaiwatsadu_text(str(brand_value))

            # Don't use SKU from JSON-LD as it's often contaminated
            # We already extracted from URL above

            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                price = offers.get('price')
                if price:
                    try:
                        product.current_price = float(price)
                        product.extraction_metadata['price_pattern'] = 'twd_json_ld'
                    except (ValueError, TypeError):
                        pass

            image = json_ld_data.get('image')
            if image:
                if isinstance(image, list):
                    product.images = image
                elif isinstance(image, str):
                    product.images = [image]

        # 2b. Extract price from rendered HTML (Thai Watsadu)
        # Extract from the actual displayed price elements in the DOM
        # IMPORTANT: Always try HTML extraction for Thai Watsadu as it's more reliable than JSON-LD
        # We'll override JSON-LD price if HTML price is found
        html_current_price = None
        html_original_price = None

        # CASE 1: Pack/Multiple pricing - Find "1 ชิ้น" (1 piece) price
        # Look for the container with "1 ชิ้น" text and extract adjacent price
        # The 1-piece card uses text-[40px] (large/highlighted), pack cards use text-[24px] (smaller)
        # Both are captured here since we anchor on the "1 ชิ้น" label
        pack_price_pattern = r'<div[^>]*class="[^"]*whitespace-nowrap[^"]*font-semibold[^"]*"[^>]*>1\s*(?:<!--|&nbsp;|<!--\s*-->)\s*ชิ้น</div>(?:(?!</div>).)*?<div[^>]*class="[^"]*text-primary[^"]*text-\[(?:24|40)px\][^"]*font-price[^"]*"[^>]*>([\d,]+(?:\.\d+)?)</div>'
        pack_match = re.search(pack_price_pattern, html_content, re.DOTALL | re.IGNORECASE)
        if pack_match:
            try:
                price_str = pack_match.group(1).replace(',', '')
                html_current_price = float(price_str)
                product.extraction_metadata['price_pattern'] = 'twd_pack_pricing'
            except (ValueError, TypeError):
                pass

        # CASE 2 & 3: Normal and Coupon case - Red price with text-redPrice class
        # This covers both discount and coupon scenarios
        if not html_current_price:
            # Look for: <span class="text-redPrice ...">฿</span><span class="... font-price ... text-redPrice ...">363.75</span>
            # Anchor on the ฿ symbol in a <span> immediately before the price span.
            # Main product price area always has <span ...>฿</span> before the price span.
            # Suggested product cards use <div ...>฿</div> + <div ... font-price ...> — excluded by this pattern.
            red_price_pattern = r'<span[^>]*class="[^"]*text-redPrice[^"]*"[^>]*>฿</span>\s*<span[^>]*class="[^"]*font-price[^"]*text-redPrice[^"]*"[^>]*>([\d,]+(?:\.\d+)?)</span>'
            red_match = re.search(red_price_pattern, html_content, re.IGNORECASE)
            if red_match:
                try:
                    price_str = red_match.group(1).replace(',', '')
                    html_current_price = float(price_str)
                    product.extraction_metadata['price_pattern'] = 'twd_red_price'
                except (ValueError, TypeError):
                    pass

        # Extract original price (ราคาเดิม with line-through)
        # Look for: <div class="text-grayDark line-through text-lg">ราคาเดิม<!-- --> <!-- -->2,180.00</div>
        original_price_pattern = r'<div[^>]*class="[^"]*text-grayDark[^"]*line-through[^"]*"[^>]*>ราคาเดิม(?:<!--|&nbsp;|<!--\s*-->|\s)*(?:<!--|&nbsp;|<!--\s*-->|\s)*([\d,]+(?:\.\d{2})?)</div>'
        original_match = re.search(original_price_pattern, html_content, re.IGNORECASE)
        if original_match:
            try:
                price_str = original_match.group(1).replace(',', '')
                html_original_price = float(price_str)
            except (ValueError, TypeError):
                pass

        # Check for additional "Buy now get discount" badge: "ซื้อตอนนี้ลดเพิ่ม 500"
        # If found, subtract the discount amount from the current price
        if html_current_price:
            additional_discount_pattern = r'ซื้อตอนนี้ลดเพิ่ม\s*([\d,]+)'
            discount_match = re.search(additional_discount_pattern, html_content, re.IGNORECASE)
            if discount_match:
                try:
                    discount_amount = float(discount_match.group(1).replace(',', ''))
                    html_current_price -= discount_amount
                except (ValueError, TypeError):
                    pass

        # Override with HTML prices if found (HTML is more reliable than JSON-LD)
        if html_current_price:
            product.current_price = html_current_price
        if html_original_price:
            product.original_price = html_original_price

        # Fallback: Try __NEXT_DATA__ JSON if HTML extraction completely failed
        if not product.current_price:
            next_data_pattern = r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'
            next_data_match = re.search(next_data_pattern, html_content, re.DOTALL)
            if next_data_match:
                try:
                    next_data_str = next_data_match.group(1)
                    individual_price_pattern = r'"price"\s*:\s*"(\d+)"[^}]*"prUname"\s*:\s*"EACH'
                    individual_match = re.search(individual_price_pattern, next_data_str)
                    if individual_match:
                        product.current_price = float(individual_match.group(1))
                        product.extraction_metadata['price_pattern'] = 'twd_next_data_individual'
                except Exception:
                    pass

        # Fallback: Try __NEXT_DATA__ JSON for original price
        if not product.original_price:
            next_data_pattern = r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'
            next_data_match = re.search(next_data_pattern, html_content, re.DOTALL)
            if next_data_match:
                try:
                    next_data_str = next_data_match.group(1)
                    disc_pattern = r'"disc"\s*:\s*"([\d.]+)"'
                    disc_match = re.search(disc_pattern, next_data_str)
                    if disc_match:
                        disc_price = float(disc_match.group(1))
                        if disc_price > 0 and (not product.current_price or disc_price > product.current_price):
                            product.original_price = disc_price
                except Exception:
                    pass

        # 3. Extract category from breadcrumb
        product.category = self._extract_thaiwatsadu_category(html_content)

        # 3.5. Extract product specifications from "ข้อมูลเฉพาะสินค้า" section
        specs = self._extract_thaiwatsadu_specs(html_content)
        if specs:
            if specs.get('dimensions'):
                product.dimensions = specs['dimensions']
            if specs.get('material'):
                product.material = specs['material']
            if specs.get('brand') and not product.brand:
                product.brand = specs['brand']
            if specs.get('color') and not product.color:
                product.color = specs['color']
            if specs.get('model') and not product.model:
                product.model = specs['model']
            if specs.get('weight'):
                # Store weight in dimensions if no dimensions found
                if not product.dimensions:
                    product.dimensions = f"น้ำหนัก: {specs['weight']} กก."

        # 4. Extract product name if not found
        if not product.name:
            name_patterns = [
                r'<h1[^>]*class="[^"]*product[^"]*name[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*>(.*?)</h1>',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    name = self._clean_text(match.group(1))
                    name = self._clean_thaiwatsadu_text(name)
                    if name and len(name) > 3:
                        product.name = name
                        break

        # 5. Extract model from product name (pattern: รุ่น XXX)
        if product.name:
            # Model can include dots like NO.888
            model_match = re.search(r'รุ่น\s+([A-Za-z0-9\-_.]+)', product.name)
            if model_match:
                product.model = model_match.group(1).strip()

        # 5b. Extract brand from HTML brand link (most reliable for Thai Watsadu)
        if not product.brand:
            # Thai Watsadu shows brand as: <a href="/th/brand/WAVE">WAVE</a>
            # Use the anchor text (group 2) not href (group 1) to avoid URL-encoding
            brand_link_match = re.search(r'href="/th/brand/([^"]+)"[^>]*>([^<]+)</a>', html_content)
            if brand_link_match:
                product.brand = brand_link_match.group(2).strip()

        # 5c. Extract brand from product name if not found
        if not product.brand and product.name:
            # Common brand patterns: MAKITA รุ่น, BOSCH รุ่น, etc.
            brand_match = re.search(r'([A-Z][A-Z0-9]+)\s+รุ่น', product.name)
            if brand_match:
                product.brand = brand_match.group(1).strip()

        # 6. Extract color from product name or HTML
        color = self._extract_color(html_content)
        if color:
            product.color = self._clean_thaiwatsadu_text(color)
        else:
            # Try to extract from product name (pattern: สีXXX)
            if product.name:
                color_match = re.search(r'สี([ก-๙a-zA-Z]+)', product.name)
                if color_match:
                    product.color = color_match.group(1).strip()

        # 7. Extract dimensions (fallback if not found in specs)
        # Only use fallback if it looks like actual dimensions (contains x or multiple numbers)
        if not product.dimensions:
            dimensions = self._extract_dimensions(html_content)
            if dimensions:
                dimensions = self._clean_thaiwatsadu_text(dimensions)
                # Only accept if it looks like dimensions (has x or multiple parts)
                if dimensions and ('x' in dimensions.lower() or len(re.findall(r'\d+', dimensions)) > 1):
                    product.dimensions = dimensions

        # 8. Extract material (fallback if not found in specs)
        if not product.material:
            material = self._extract_material(html_content)
            if material:
                material = self._clean_thaiwatsadu_text(material)
                # Filter out invalid values - company names, slogans, categories, etc.
                invalid_material_patterns = [
                    'ครบเรื่องบ้าน', 'ถูกและดี', 'ไทวัสดุ', 'thaiwatsadu',
                    'บริษัท', 'จำกัด', 'มหาชน', 'corporation', 'company',
                    'เซ็นทรัล', 'central', 'retail', 'รีเทล',
                    'http', 'www', '.com', '.co.th',
                    # Category-related words that get falsely extracted as material
                    'ตกแต่ง', 'วัสดุตกแต่ง', 'อุปกรณ์', 'ประตู', 'หน้าต่าง',
                    'บันได', 'รั้ว', 'สินค้า', 'หมวดหมู่'
                ]
                if material and not any(s.lower() in material.lower() for s in invalid_material_patterns):
                    product.material = material

        # 9. Extract volume
        if not product.volume:
            product.volume = self._extract_volume(html_content)

        # 10. Extract images - Thai Watsadu uses Next.js image format
        if not product.images:
            product.images = self._extract_thaiwatsadu_images(html_content, product.sku)

        # 11. Fallback: use base extraction for missing fields
        if not product.name or not product.current_price:
            base_product = super().extract_from_html(html_content, url)
            if base_product:
                if not product.name:
                    product.name = self._clean_thaiwatsadu_text(base_product.name)
                if not product.current_price:
                    product.current_price = base_product.current_price
                if not product.original_price:
                    product.original_price = base_product.original_price
                if not product.brand:
                    product.brand = self._clean_thaiwatsadu_text(base_product.brand)
                if not product.images:
                    product.images = base_product.images

        product.retailer = "Thai Watsadu"
        return product

    def _clean_thaiwatsadu_text(self, text: str, strip_html: bool = False) -> Optional[str]:
        """Remove Thai Watsadu specific contamination from text."""
        if not text:
            return None

        # Strip HTML tags if requested
        if strip_html:
            text = re.sub(r'<[^>]+>', '', text)

        # Remove Thai Watsadu branding and slogan
        for filter_str in self.THAIWATSADU_FILTER_STRINGS:
            text = re.sub(re.escape(filter_str), '', text, flags=re.IGNORECASE)

        # Remove patterns like "- ไทวัสดุ" or "| ไทวัสดุ"
        text = re.sub(r'[-|]\s*$', '', text)

        # Clean whitespace
        text = ' '.join(text.split()).strip()

        # Remove trailing punctuation
        text = text.rstrip('-|,;:')

        return text if text else None

    def _extract_thaiwatsadu_images(self, html_content: str, sku: str = None) -> List[str]:
        """Extract product images from Thai Watsadu Next.js image format.

        Thai Watsadu uses Next.js images like:
        /_next/image?url=https://pim.thaiwatsadu.com/TWDPIM/web/Thumbnail/Image/0204/60265581r.jpg&w=1920&q=75

        Images can be in srcset or src attributes. Prioritize srcset for product images.
        Filter out badge/promotional images (e.g., /images/badge/).
        """
        from urllib.parse import unquote
        images = []

        # Pattern 1: Find Next.js images in srcset attributes (main product images)
        srcset_pattern = r'srcset="([^"]*/_next/image\?url=https%3A%2F%2Fpim\.thaiwatsadu\.com[^"]*)"'
        srcset_matches = re.findall(srcset_pattern, html_content, re.IGNORECASE)
        for srcset_value in srcset_matches:
            # srcset can have multiple URLs like "url1 1x, url2 2x"
            # Extract all URLs from srcset
            url_matches = re.findall(r'/_next/image\?url=(https%3A%2F%2Fpim\.thaiwatsadu\.com[^&\s]+)', srcset_value)
            for url_encoded in url_matches:
                actual_url = unquote(url_encoded)
                # Filter out badge images
                if '/badge/' not in actual_url.lower() and actual_url not in images:
                    images.append(actual_url)

        # Pattern 2: Find Next.js images with pim.thaiwatsadu URLs containing the SKU in src
        if sku and not images:
            sku_pattern = rf'src="(/_next/image\?url=[^"]*{sku}[^"]*)"'
            matches = re.findall(sku_pattern, html_content, re.IGNORECASE)
            for match in matches:
                # Extract the actual URL from the Next.js wrapper
                url_match = re.search(r'url=([^&]+)', match)
                if url_match:
                    actual_url = unquote(url_match.group(1))
                    # Filter out badge images
                    if actual_url and '/badge/' not in actual_url.lower() and actual_url not in images:
                        images.append(actual_url)

        # Pattern 3: Find all pim.thaiwatsadu.com images in src attributes
        if not images:
            pim_pattern = r'src="/_next/image\?url=(https%3A%2F%2Fpim\.thaiwatsadu\.com[^&"]+)'
            matches = re.findall(pim_pattern, html_content, re.IGNORECASE)
            for match in matches[:10]:
                actual_url = unquote(match)
                # Filter out badge images
                if actual_url and '/badge/' not in actual_url.lower() and actual_url not in images:
                    images.append(actual_url)

        # Pattern 4: Direct pim.thaiwatsadu.com URLs (if not Next.js wrapped)
        if not images:
            direct_pattern = r'src="(https://pim\.thaiwatsadu\.com[^"]+)"'
            matches = re.findall(direct_pattern, html_content, re.IGNORECASE)
            for match in matches[:10]:
                # Filter out badge images
                if '/badge/' not in match.lower() and match not in images:
                    images.append(match)

        return images[:10]  # Limit to 10 images

    def _extract_thaiwatsadu_category(self, html_content: str) -> Optional[str]:
        """Extract category from Thai Watsadu breadcrumb or JSON-LD."""
        # 1. Try Thai Watsadu specific categoryBar pattern first (most reliable)
        # Look for all links with categoryBar_journeyNavText class
        categorybar_links = re.findall(
            r'<a[^>]*class="[^"]*categoryBar_journeyNavText[^"]*"[^>]*>([^<]+)</a>',
            html_content,
            re.IGNORECASE
        )
        if categorybar_links:
            # Return the last category (most specific/smallest)
            return categorybar_links[-1].strip()

        # 2. Try JSON-LD BreadcrumbList
        try:
            breadcrumb_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
            matches = re.findall(breadcrumb_pattern, html_content, re.DOTALL | re.IGNORECASE)

            for match in matches:
                try:
                    data = json.loads(match)
                    # Check for BreadcrumbList
                    if isinstance(data, dict) and data.get('@type') == 'BreadcrumbList':
                        items = data.get('itemListElement', [])
                        categories = []
                        for item in items:
                            name = item.get('name') or item.get('item', {}).get('name')
                            if name:
                                categories.append(name)

                        skip_categories = ['หน้าแรก', 'home', 'สินค้า', 'products', 'ทั้งหมด', 'all', 'thaiwatsadu', 'ไทวัสดุ']
                        for cat in reversed(categories[:-1]):
                            cat_clean = cat.strip()
                            if cat_clean and cat_clean.lower() not in [s.lower() for s in skip_categories]:
                                return cat_clean

                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'BreadcrumbList':
                                items = item.get('itemListElement', [])
                                categories = []
                                for it in items:
                                    name = it.get('name') or it.get('item', {}).get('name')
                                    if name:
                                        categories.append(name)

                                skip_categories = ['หน้าแรก', 'home', 'สินค้า', 'products', 'ทั้งหมด', 'all', 'thaiwatsadu', 'ไทวัสดุ']
                                for cat in reversed(categories[:-1]):
                                    cat_clean = cat.strip()
                                    if cat_clean and cat_clean.lower() not in [s.lower() for s in skip_categories]:
                                        return cat_clean
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        # 3. Try standard HTML breadcrumb patterns
        breadcrumb_patterns = [
            r'<nav[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</nav>',
            r'<div[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</div>',
            r'<ol[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</ol>',
            r'<ul[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</ul>',
        ]

        for pattern in breadcrumb_patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                breadcrumb_content = match.group(1)
                category_matches = re.findall(r'<a[^>]*>([^<]+)</a>', breadcrumb_content)
                if not category_matches:
                    category_matches = re.findall(r'<span[^>]*>([^<]+)</span>', breadcrumb_content)

                skip_categories = ['หน้าแรก', 'home', 'สินค้า', 'products', 'ทั้งหมด', 'all']
                for cat in reversed(category_matches):
                    cat_clean = self._clean_text(cat)
                    if cat_clean and cat_clean.lower() not in skip_categories:
                        return cat_clean

        # 4. Try Product JSON-LD category field
        try:
            product_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
            matches = re.findall(product_pattern, html_content, re.DOTALL | re.IGNORECASE)

            for match in matches:
                try:
                    data = json.loads(match)
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        category = data.get('category')
                        if category:
                            if isinstance(category, list):
                                for cat in reversed(category):
                                    if cat and cat.lower() not in ['สินค้า', 'products']:
                                        return cat
                            elif isinstance(category, str):
                                return category
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        return None

    def _extract_thaiwatsadu_specs(self, html_content: str) -> Dict[str, str]:
        """Extract product specifications from Thai Watsadu's 'ข้อมูลเฉพาะสินค้า' section.

        Parses the flex-based specification table and returns a dictionary of extracted values.
        """
        specs = {}

        # Map Thai labels to our field names (order matters - more specific first)
        label_mapping = [
            ('ขนาด (กxลxส)(ซม.)', 'dimensions'),
            ('ขนาด(กxลxส)(ซม.)', 'dimensions'),
            ('ขนาดสินค้า (ซม.)', 'dimensions'),
            ('วัสดุหลัก', 'material'),
            ('วัสดุด้ามจับ', 'handle_material'),
            ('วัสดุ', 'material'),
            ('แบรนด์', 'brand'),
            ('ยี่ห้อ', 'brand'),
            ('สี', 'color'),
            ('รุ่น', 'model'),
            ('น้ำหนัก (กก.)', 'weight'),
            ('น้ำหนัก', 'weight'),
            ('ประเภท', 'type'),
            ('ฟังก์ชัน', 'function'),
            ('ขนาด', 'size'),  # Generic size - must be last
        ]

        try:
            # Method 1: Direct pattern matching for Thai Watsadu spec table
            # HTML: <div class="w-1/2..."><div>LABEL</div></div><div class="w-1/2"><div>VALUE</div></div>
            for thai_label, field_name in label_mapping:
                escaped_label = re.escape(thai_label)
                # Pattern: label div -> close div -> close parent div -> value div container -> value div
                patterns = [
                    # Most specific: exact structure with w-1/2 class
                    rf'<div>{escaped_label}</div></div><div class="w-1/2"><div>([^<]+)</div>',
                    # With whitespace
                    rf'<div>{escaped_label}</div>\s*</div>\s*<div\s+class="w-1/2">\s*<div>([^<]+)</div>',
                    # More flexible class matching
                    rf'<div>{escaped_label}</div></div><div[^>]*class="[^"]*w-1/2[^"]*"[^>]*><div>([^<]+)</div>',
                ]
                for pattern in patterns:
                    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                    if match:
                        value = match.group(1).strip()
                        if value:
                            clean_value = self._clean_thaiwatsadu_text(value)
                            if clean_value:
                                if field_name == 'dimensions':
                                    specs['dimensions'] = clean_value
                                elif field_name == 'size' and 'dimensions' not in specs:
                                    specs['size'] = clean_value
                                elif field_name not in specs:
                                    specs[field_name] = clean_value
                                break
                if field_name in specs or (field_name == 'dimensions' and 'dimensions' in specs):
                    continue

            # Method 2: Direct search for dimension value format (18 x 2 x 13)
            if 'dimensions' not in specs:
                # Look for X x Y x Z pattern wrapped in div tags
                dim_match = re.search(r'<div>(\d+\s*x\s*\d+\s*x\s*\d+)</div>', html_content, re.IGNORECASE)
                if dim_match:
                    specs['dimensions'] = dim_match.group(1).strip()

            # Method 2b: Extract from delivery size format (ก) 35 x (ย) 67 x (ส) 50
            if 'dimensions' not in specs:
                # Pattern for Thai Watsadu delivery size: (ก)35 x (ย)67 x (ส)50
                # Also handles decimals like 12.5 x 3.5 x 19
                # HTML: (<!-- -->ก<!-- -->)<!-- -->35<!-- --> x (<!-- -->ย<!-- -->)<!-- -->67<!-- --> x (<!-- -->ส<!-- -->)<!-- -->50
                delivery_pattern = r'\((?:<!--[^>]*-->)*ก(?:<!--[^>]*-->)*\)(?:<!--[^>]*-->)*([\d.]+)(?:<!--[^>]*-->)*\s*x\s*\((?:<!--[^>]*-->)*ย(?:<!--[^>]*-->)*\)(?:<!--[^>]*-->)*([\d.]+)(?:<!--[^>]*-->)*\s*x\s*\((?:<!--[^>]*-->)*ส(?:<!--[^>]*-->)*\)(?:<!--[^>]*-->)*([\d.]+)'
                delivery_match = re.search(delivery_pattern, html_content, re.IGNORECASE)
                if delivery_match:
                    w, d, h = delivery_match.group(1), delivery_match.group(2), delivery_match.group(3)
                    specs['dimensions'] = f"{w} x {d} x {h}"

            # Method 3: Look for material value "เหล็ก CR-V" pattern
            if 'material' not in specs:
                material_patterns = [
                    r'<div>วัสดุหลัก</div></div><div class="w-1/2"><div>([^<]+)</div>',
                    r'<div>วัสดุหลัก</div>\s*</div>\s*<div[^>]*class="[^"]*w-1/2[^"]*"[^>]*>\s*<div>([^<]+)</div>',
                    r'>วัสดุหลัก</div></div><div[^>]*><div>([^<]+)</div>',
                ]
                for pattern in material_patterns:
                    match = re.search(pattern, html_content, re.IGNORECASE)
                    if match:
                        value = match.group(1).strip()
                        if value:
                            specs['material'] = self._clean_thaiwatsadu_text(value)
                            break

            # If we have size but not dimensions, use size as dimensions
            if 'size' in specs and 'dimensions' not in specs:
                specs['dimensions'] = specs['size']

        except Exception:
            pass

        return specs

    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON-LD data from HTML."""
        try:
            pattern = r'<script type="application/ld\+json">(.*?)</script>'
            matches = re.finditer(pattern, html_content, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        return data
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None


class HomeProExtractor(ProductExtractor):
    """Specialized extractor for HomePro website with enhanced JSON-LD and field sanitization."""

    # HomePro specific strings to filter out
    HOMEPRO_FILTER_STRINGS = [
        'homepro', 'home pro', 'โฮมโปร',
        'กรุณากดยืนยันเพื่อออกจากระบบ',  # Logout confirmation message
        'ศูนย์การตั้งค่าความเป็นส่วนตัว',  # Privacy settings
    ]

    # Invalid model values to filter
    INVALID_MODEL_VALUES = [
        'อื่น', 'อื่นๆ', 'other', 'others', '-', 'n/a', 'na', 'none',
    ]

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from HomePro using JSON-LD as primary source."""
        import sys
        print(f"\n{'='*60}", flush=True, file=sys.stderr)
        print(f"[HomePro EXTRACT] Starting extraction for: {url}", flush=True, file=sys.stderr)
        print(f"[HomePro EXTRACT] HTML length: {len(html_content) if html_content else 0} chars", flush=True, file=sys.stderr)
        
        if not html_content:
            print(f"[HomePro EXTRACT] ERROR: HTML content is None or empty!", flush=True, file=sys.stderr)
            return None
        
        # Show first 200 chars to verify it's valid HTML
        preview = html_content[:500].replace('\n', ' ')[:200]
        print(f"[HomePro EXTRACT] HTML preview: {preview}...", flush=True, file=sys.stderr)
        
        # DEBUG: Save HTML to file for inspection
        import os
        import hashlib
        sku_from_url = re.search(r'/p/(\d+)', url)
        sku_str = sku_from_url.group(1) if sku_from_url else 'unknown'
        debug_file = f"results/debug_homepro_{sku_str}.html"
        try:
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"[HomePro DEBUG] Saved HTML to: {debug_file}", flush=True, file=sys.stderr)
        except Exception as e:
            print(f"[HomePro DEBUG] Could not save HTML: {e}", flush=True, file=sys.stderr)
        
        product = ProductData(url=url)

        # 1. Extract SKU from URL first (most reliable for HomePro)
        # URL pattern: /p/246513
        if url:
            sku_match = re.search(r'/p/(\d+)', url)
            if sku_match:
                product.sku = sku_match.group(1)
                print(f"[HomePro EXTRACT] Extracted SKU from URL: {product.sku}", flush=True, file=sys.stderr)
            else:
                print(f"[HomePro EXTRACT] WARNING: Could not extract SKU from URL", flush=True, file=sys.stderr)

        # 2. Try JSON-LD extraction (primary source for HomePro - most accurate)
        json_ld_data = self._extract_json_ld(html_content)

        import sys
        print(f"[HomePro DEBUG] JSON-LD extraction for {url}", flush=True, file=sys.stderr)
        if json_ld_data:
            print(f"  JSON-LD keys: {list(json_ld_data.keys())}", flush=True, file=sys.stderr)
            print(f"  name: {json_ld_data.get('name')}", flush=True, file=sys.stderr)
            print(f"  sku: {json_ld_data.get('sku')}", flush=True, file=sys.stderr)
            print(f"  brand: {json_ld_data.get('brand')}", flush=True, file=sys.stderr)
            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                print(f"  price: {offers.get('price')}", flush=True, file=sys.stderr)
        else:
            print(f"  JSON-LD not found - will use HTML extraction", flush=True, file=sys.stderr)

        if json_ld_data:
            # Name from JSON-LD
            product.name = self._clean_homepro_text(json_ld_data.get('name'))

            # Description from JSON-LD
            desc = json_ld_data.get('description')
            if desc:
                product.description = self._clean_homepro_text(desc, strip_html=True)

            # Brand from JSON-LD
            brand_value = json_ld_data.get('brand')
            if brand_value:
                if isinstance(brand_value, dict):
                    brand_value = brand_value.get('name')
                if brand_value:
                    product.brand = self._clean_homepro_text(str(brand_value))

            # SKU from JSON-LD (backup)
            if not product.sku:
                sku_value = json_ld_data.get('sku')
                if sku_value:
                    product.sku = str(sku_value).strip()

            # Price from JSON-LD offers (most reliable)
            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                price = offers.get('price')
                if price:
                    try:
                        product.current_price = float(price)
                        product.extraction_metadata['price_pattern'] = 'hp_json_ld'
                    except (ValueError, TypeError):
                        pass

            # Images from JSON-LD
            image = json_ld_data.get('image')
            if image:
                if isinstance(image, list):
                    # Filter to only product images (cdn.homepro.co.th or ecatalog-media.homepro.co.th)
                    product.images = [img for img in image if ('cdn.homepro.co.th' in img or 'ecatalog-media.homepro.co.th' in img) and 'ART_IMAGE' in img]
                elif isinstance(image, str):
                    if 'cdn.homepro.co.th' in image or 'ecatalog-media.homepro.co.th' in image:
                        product.images = [image]

        # 2b. If JSON-LD price not found, try HomePro-specific HTML price patterns
        print(f"[HomePro EXTRACT] After JSON-LD - Price: {product.current_price}, Name: {product.name}", flush=True, file=sys.stderr)
        if not product.current_price:
            print(f"[HomePro EXTRACT] Attempting HTML price extraction...", flush=True, file=sys.stderr)

            # FIRST: SKU-specific gtm price — anchored to this product's SKU, can never match a related product
            # If gtmIsNetPrice=true, use gtmNetPrice (actual payable price after online discount)
            # e.g. shelf price gtmPrice=659, but gtmNetPrice=649 after ฿10 "buy online extra discount"
            if product.sku:
                is_net = re.search(
                    rf'gtmIsNetPrice-{re.escape(product.sku)}[^>]*value=["\']true["\']',
                    html_content, re.IGNORECASE
                )
                if is_net:
                    net_matches = re.findall(
                        rf'<input[^>]*id=["\']gtmNetPrice-{re.escape(product.sku)}["\'][^>]*value=["\']([\d.]+)["\']',
                        html_content, re.IGNORECASE
                    )
                    if net_matches:
                        try:
                            price = float(net_matches[0])
                            if 1 <= price <= 500000:
                                product.current_price = price
                                product.extraction_metadata['price_pattern'] = 'hp_gtm_net_price'
                                print(f"[HomePro EXTRACT] gtmNetPrice-{product.sku}: {price}", flush=True, file=sys.stderr)
                        except ValueError:
                            pass

                if not product.current_price:
                    sku_matches = re.findall(
                        rf'<input[^>]*id=["\']gtmPrice-{re.escape(product.sku)}["\'][^>]*value=["\']([\d.]+)["\']',
                        html_content, re.IGNORECASE
                    )
                    if sku_matches:
                        try:
                            price = float(sku_matches[0])
                            if 1 <= price <= 500000:
                                product.current_price = price
                                product.extraction_metadata['price_pattern'] = 'hp_gtm_price'
                                print(f"[HomePro EXTRACT] gtmPrice-{product.sku}: {price}", flush=True, file=sys.stderr)
                        except ValueError:
                            pass

            # SECOND: SKU-anchored JS analytics price (Facebook Pixel / GTM event)
            # Pattern: contents:[{id:"271155",...,item_price:"2399",...}]
            if not product.current_price and product.sku:
                js_price_pattern = rf'id\s*:\s*["\']?{re.escape(product.sku)}["\']?.{{0,300}}?item_price\s*:\s*["\'](\d+)["\']'
                js_match = re.search(js_price_pattern, html_content, re.IGNORECASE | re.DOTALL)
                if js_match:
                    try:
                        price = float(js_match.group(1))
                        if 1 <= price <= 500000:
                            product.current_price = price
                            product.extraction_metadata['price_pattern'] = 'hp_js_analytics'
                            print(f"[HomePro EXTRACT] JS item_price for {product.sku}: {price}", flush=True, file=sys.stderr)
                    except ValueError:
                        pass

            # THIRD: Generic HTML patterns (only if both SKU-anchored methods failed)
            # These can match related/comparison product prices — use as last resort only
            # These can match related/comparison product prices — use as last resort only
            homepro_price_patterns = [
                # 1. discount-price container — the actual displayed sale/online price
                r'<div[^>]*class="[^"]*discount-price[^"]*"[^>]*>.*?<span[^>]*class="[^"]*amount[^"]*"[^>]*>([\d,]+)</span>',
                # 2. obcon-price-info container
                r'obcon-price-info[^>]*>.*?<span[^>]*class="[^"]*amount[^"]*"[^>]*>([\d,]+)</span>',
                # 3. Price div with ฿ (can match comparison/related product prices — only used if SKU-anchored sources fail)
                r'<div[^>]*class="[^"]*price[^"]*"[^>]*>\s*(?:<[^>]*>)*\s*฿\s*([\d,]+)</div>',
                # 4. Price meta tag
                r'<meta[^>]*property=["\']product:price:amount["\'][^>]*content=["\']([\d.]+)["\']',
                # NOTE: currency+amount span pattern removed — proven to match installation fees / service prices
            ]

            if not product.current_price:
                print(f"[HomePro EXTRACT] gtmPrice not found, trying {len(homepro_price_patterns)} HTML patterns...", flush=True, file=sys.stderr)
                pattern_names = ['hp_discount_price_html', 'hp_obcon_price', 'hp_price_div', 'hp_meta_tag']
                pattern_num = 0
                for pattern in homepro_price_patterns:
                    pattern_num += 1
                    matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
                    print(f"[HomePro EXTRACT] Pattern #{pattern_num}: found {len(matches)} matches", flush=True, file=sys.stderr)
                    if matches:
                        for price_str in matches:
                            try:
                                price = float(price_str.replace(',', ''))
                                print(f"[HomePro EXTRACT]   Candidate price: {price}", flush=True, file=sys.stderr)
                                if 50 <= price <= 500000:
                                    product.current_price = price
                                    product.extraction_metadata['price_pattern'] = pattern_names[pattern_num - 1]
                                    print(f"[HomePro EXTRACT]   ✓ Price accepted: {price}", flush=True, file=sys.stderr)
                                    break
                                else:
                                    print(f"[HomePro EXTRACT]   ✗ Price out of range", flush=True, file=sys.stderr)
                            except ValueError:
                                print(f"[HomePro EXTRACT]   ✗ Price parse error: {price_str}", flush=True, file=sys.stderr)
                                continue
                    if product.current_price:
                        break

            # Kept for compatibility — now handled above as first attempt
            if not product.current_price and product.sku:
                sku_gtm_pattern = rf'<input[^>]*id=["\']gtmPrice-{re.escape(product.sku)}["\'][^>]*value=["\']([\d.]+)["\']'
                sku_matches = re.findall(sku_gtm_pattern, html_content, re.IGNORECASE)
                if sku_matches:
                    try:
                        price = float(sku_matches[0])
                        if 1 <= price <= 500000:
                            product.current_price = price
                            print(f"[HomePro EXTRACT] GTM fallback price: {price}", flush=True, file=sys.stderr)
                    except ValueError:
                        pass

        # 3. Extract original price from HTML (for discount calculation)
        # HomePro HTML: <div class="original-price">...<span class="amount">235</span>
        if not product.original_price:
            orig_price_patterns = [
                # HomePro original-price div with amount span
                r'<div[^>]*class=["\']original-price["\'][^>]*>.*?<span[^>]*class=["\']amount["\'][^>]*>([\d,]+)</span>',
                # Original price div with direct number
                r'<div[^>]*class=["\']original-price["\'][^>]*>\s*([\d,]+)',
                # Line-through style
                r'<span[^>]*class="[^"]*line-through[^"]*"[^>]*>.*?฿?\s*([\d,]+)',
                r'<del[^>]*>.*?฿?\s*([\d,]+)',
                r'ราคาปกติ[:\s]*฿?\s*([\d,]+)',
            ]
            for pattern in orig_price_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    price_str = match.group(1).replace(',', '')
                    try:
                        orig_price = float(price_str)
                        if orig_price > 0 and (not product.current_price or orig_price > product.current_price):
                            product.original_price = orig_price
                            break
                    except ValueError:
                        pass

        # 4. Extract category from breadcrumb
        product.category = self._extract_homepro_category(html_content)

        # 5. Extract dimensions, volume, and other specs from product specifications
        specs = self._extract_homepro_specs(html_content)
        if specs:
            if specs.get('dimensions'):
                product.dimensions = specs['dimensions']
            if specs.get('volume') and not product.volume:
                product.volume = specs['volume']
            if specs.get('color') and not product.color:
                product.color = specs['color']
            if specs.get('brand') and not product.brand:
                product.brand = specs['brand']
            if specs.get('model') and not product.model:
                # Filter invalid model values
                model = specs['model']
                if model.lower() not in [v.lower() for v in self.INVALID_MODEL_VALUES]:
                    product.model = model

        # 6. Extract product name from HTML if not found in JSON-LD
        if not product.name:
            import sys
            print(f"[HomePro EXTRACT] Name not found in JSON-LD, trying HTML patterns...", flush=True, file=sys.stderr)
            name_patterns = [
                # Try h1 with product-specific classes first
                r'<h1[^>]*class="[^"]*product[^"]*name[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*class="[^"]*pdp[^"]*"[^>]*>(.*?)</h1>',
                # Try h1 with data-testid
                r'<h1[^>]*data-testid="[^"]*product[^"]*name[^"]*"[^>]*>(.*?)</h1>',
                # Try any h1 that's not in a nav or footer
                r'<h1[^>]*>(.*?)</h1>',
                # Try page title as last resort
                r'<title[^>]*>([^<]+)</title>',
            ]
            pattern_num = 0
            for pattern in name_patterns:
                pattern_num += 1
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    raw_name = match.group(1)
                    print(f"[HomePro EXTRACT] Pattern #{pattern_num} matched! Raw: {raw_name[:100]}", flush=True, file=sys.stderr)
                    name = self._clean_text(raw_name)
                    name = self._clean_homepro_text(name)
                    print(f"[HomePro EXTRACT] After cleaning: {name}", flush=True, file=sys.stderr)
                    if name and len(name) > 3:
                        product.name = name
                        print(f"[HomePro EXTRACT] ✓ Name accepted: {name}", flush=True, file=sys.stderr)
                        break
                    else:
                        print(f"[HomePro EXTRACT] ✗ Name rejected (too short or empty)", flush=True, file=sys.stderr)
                else:
                    print(f"[HomePro EXTRACT] Pattern #{pattern_num} no match", flush=True, file=sys.stderr)
            
            # Additional fallback: Try og:title meta tag
            if not product.name:
                og_title_match = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', html_content, re.IGNORECASE)
                if og_title_match:
                    name = self._clean_text(og_title_match.group(1))
                    name = self._clean_homepro_text(name)
                    if name and len(name) > 3:
                        product.name = name
                        print(f"[HomePro DEBUG] Found name from og:title")

        # 7. Extract images from HTML if not found (fallback)
        if not product.images:
            # HomePro product images pattern (cdn.homepro.co.th or ecatalog-media.homepro.co.th)
            img_patterns = [
                r'<img[^>]*src="(https://(?:cdn|ecatalog-media)\.homepro\.co\.th/[^"]*ART_IMAGE[^"]+)"',
                r'"(https://(?:cdn|ecatalog-media)\.homepro\.co\.th/[^"]*ART_IMAGE[^"]+)"',
                r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']*)"',
            ]
            images = []
            for pattern in img_patterns:
                matches = re.findall(pattern, html_content)
                for img in matches:
                    # Only include HomePro product images
                    if 'homepro.co.th' in img and img not in images:
                        images.append(img)
            if images:
                product.images = images[:10]

        # 8. Clean and validate color field (prevent CSS contamination)
        if product.color:
            product.color = self._sanitize_homepro_color(product.color)

        # 9. Clean model field (filter invalid values)
        if product.model:
            if product.model.lower() in [v.lower() for v in self.INVALID_MODEL_VALUES]:
                product.model = None

        # 10. Extract brand from product name if not found elsewhere
        # Pattern: "Product Name BRAND 500ml" - brand is often uppercase word before size
        if not product.brand and product.name:
            # Common Thai hardware store brands
            known_brands = [
                'HG', 'KARCHER', 'BOSCH', 'MAKITA', 'DEWALT', 'MILWAUKEE', 'STANLEY',
                'BLACK+DECKER', 'PHILIPS', 'PANASONIC', 'TOSHIBA', 'LG', 'SAMSUNG',
                'ELECTROLUX', 'MITSUBISHI', 'DAIKIN', 'HITACHI', 'SHARP', 'HAIER',
                'TOA', 'BEGER', 'NIPPON', 'JOTUN', 'DULUX',
                'COTTO', 'AMERICAN STANDARD', 'KOHLER', 'GROHE', 'TOTO',
                'YALE', 'HAFELE', 'SCHLAGE', 'SCG', '3M', 'SCOTCH-BRITE',
            ]
            name_upper = product.name.upper()
            for brand in known_brands:
                if brand in name_upper:
                    product.brand = brand
                    break

            # Try to find uppercase word that looks like a brand
            if not product.brand:
                brand_match = re.search(r'\b([A-Z][A-Z0-9+\-]{1,15})\b', product.name)
                if brand_match:
                    potential_brand = brand_match.group(1)
                    # Filter out size indicators
                    if potential_brand not in ['ML', 'CM', 'MM', 'KG', 'G', 'L', 'M', 'W', 'V', 'HP']:
                        product.brand = potential_brand

        # 11. Fallback: use base extraction for missing fields (name only)
        # NOTE: We do NOT use base extraction for price because HomePro's HTML structure
        # has related products markers appearing BEFORE the main product price in the DOM
        if not product.name:
            base_product = super().extract_from_html(html_content, url)
            if base_product:
                if not product.name:
                    product.name = self._clean_homepro_text(base_product.name)
                # NOTE: We intentionally DO NOT use base extraction for price
                # HomePro's HTML structure causes base extraction to pick up related product prices

        product.retailer = "HomePro"
        
        # DEBUG: Log extraction results
        import sys
        print(f"\n[HomePro DEBUG] Extraction completed for: {url}", flush=True, file=sys.stderr)
        print(f"  Name: {product.name}", flush=True, file=sys.stderr)
        print(f"  Price: {product.current_price}", flush=True, file=sys.stderr)
        print(f"  Brand: {product.brand}", flush=True, file=sys.stderr)
        print(f"  SKU: {product.sku}", flush=True, file=sys.stderr)
        print(f"  Images: {len(product.images) if product.images else 0}", flush=True, file=sys.stderr)
        
        # Validation: HomePro products must have at minimum name OR sku
        if not product.name and not product.sku:
            print(f"[HomePro ERROR] Failed to extract name or SKU - extraction failed!", flush=True, file=sys.stderr)
            return None
        
        return product

    def _clean_homepro_text(self, text: str, strip_html: bool = False) -> Optional[str]:
        """Remove HomePro specific contamination from text."""
        if not text:
            return None

        # Strip HTML tags if requested
        if strip_html:
            text = re.sub(r'<[^>]+>', '', text)

        # Remove HomePro branding and invalid messages
        for filter_str in self.HOMEPRO_FILTER_STRINGS:
            text = re.sub(re.escape(filter_str), '', text, flags=re.IGNORECASE)

        # Remove patterns like "- HomePro" or "| HomePro"
        text = re.sub(r'[-|]\s*$', '', text)

        # Clean whitespace
        text = ' '.join(text.split()).strip()

        # Remove trailing punctuation
        text = text.rstrip('-|,;:')

        return text if text else None

    def _sanitize_homepro_color(self, color: str) -> Optional[str]:
        """Sanitize color field to prevent CSS contamination."""
        if not color:
            return None

        # Check for CSS contamination patterns
        css_contamination_patterns = [
            r'margin', r'padding', r'px', r'rem', r'em',
            r'font', r'color:', r'background', r'border',
            r'display', r'position', r'width', r'height',
            r'ศูนย์การตั้งค่า', r'ความเป็นส่วนตัว',
        ]

        color_lower = color.lower()
        for pattern in css_contamination_patterns:
            if re.search(pattern, color_lower):
                return None

        # Apply general color sanitization
        clean_color = self._sanitize_color_field(color)
        return clean_color

    def _extract_homepro_category(self, html_content: str) -> Optional[str]:
        """Extract category from HomePro breadcrumb."""
        # Try breadcrumb patterns
        breadcrumb_patterns = [
            r'<nav[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</nav>',
            r'<div[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</div>',
            r'<ol[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>(.*?)</ol>',
        ]

        for pattern in breadcrumb_patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                breadcrumb_content = match.group(1)
                # Extract category names from links
                category_matches = re.findall(r'<a[^>]*>([^<]+)</a>', breadcrumb_content)
                if not category_matches:
                    category_matches = re.findall(r'<span[^>]*>([^<]+)</span>', breadcrumb_content)

                skip_categories = ['หน้าแรก', 'home', 'homepro', 'โฮมโปร', 'สินค้า', 'products']
                for cat in reversed(category_matches[:-1] if len(category_matches) > 1 else category_matches):
                    cat_clean = self._clean_text(cat)
                    if cat_clean and cat_clean.lower() not in skip_categories:
                        return cat_clean

        return None

    def _extract_homepro_specs(self, html_content: str) -> Dict[str, str]:
        """Extract product specifications from HomePro product page.

        HomePro specs are in table format:
        <tr class="pdp-HO_HEIGHT">
            <td>ความสูง (ซม.)</td>
            <td>25</td>
        </tr>
        """
        specs = {}

        # HomePro table-based spec patterns - label in first td, value in second td
        # Pattern: <td>label</td>...<td>value</td>
        table_spec_patterns = [
            (r'<td[^>]*>ความสูง[^<]*</td>\s*<td[^>]*>([\d.]+)</td>', 'height'),
            (r'<td[^>]*>ความกว้าง[^<]*</td>\s*<td[^>]*>([\d.]+)</td>', 'width'),
            (r'<td[^>]*>ความลึก[^<]*</td>\s*<td[^>]*>([\d.]+)</td>', 'depth'),
            (r'<td[^>]*>น้ำหนัก[^<]*</td>\s*<td[^>]*>([\d.]+)</td>', 'weight'),
            (r'<td[^>]*>ขนาดสินค้า</td>\s*<td[^>]*>([^<]+)</td>', 'size'),
            (r'<td[^>]*>สี</td>\s*<td[^>]*>([^<]+)</td>', 'color'),
            (r'<td[^>]*>ยี่ห้อ</td>\s*<td[^>]*>([^<]+)</td>', 'brand'),
            (r'<td[^>]*>รุ่น</td>\s*<td[^>]*>([^<]+)</td>', 'model'),
        ]

        for pattern, field in table_spec_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if match:
                value = self._clean_text(match.group(1))
                if value and len(value) < 100:
                    specs[field] = value

        # Build dimensions from height/width/depth
        if 'height' in specs or 'width' in specs or 'depth' in specs:
            dim_parts = []
            for key in ['width', 'depth', 'height']:
                if key in specs:
                    # Extract numeric value - require at least one digit
                    num_match = re.search(r'(\d+(?:\.\d+)?)', specs[key])
                    if num_match:
                        dim_parts.append(num_match.group(1))
            # Only build dimensions if we have at least 2 valid numeric parts
            if len(dim_parts) >= 2:
                specs['dimensions'] = ' x '.join(dim_parts) + ' cm'

        # Extract volume from size field (e.g., "500ML")
        if 'size' in specs and not specs.get('volume'):
            size_val = specs['size'].upper()
            vol_match = re.search(r'(\d+)\s*(ML|L|ลิตร|มล)', size_val, re.IGNORECASE)
            if vol_match:
                specs['volume'] = specs['size']

        return specs

    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON-LD data from HTML."""
        try:
            pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
            matches = re.finditer(pattern, html_content, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        return data
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None


class BoonthavornExtractor(ProductExtractor):
    """Specialized extractor for Boonthavorn website using JSON-LD with enhanced sanitization."""

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from Boonthavorn using JSON-LD and HTML parsing with enhanced sanitization."""
        product = ProductData(url=url)

        # 1. Try to extract from JSON-LD (Structured Data) - Most reliable for basic info
        json_ld_data = self._extract_json_ld(html_content)

        if json_ld_data:
            product.name = json_ld_data.get('name')
            product.description = json_ld_data.get('description')

            # Enhanced brand extraction with sanitization
            brand_value = json_ld_data.get('brand')
            if brand_value:
                if isinstance(brand_value, dict):
                    brand_value = brand_value.get('name')
                if brand_value:
                    clean_brand = self._sanitize_brand_field(str(brand_value))
                    product.brand = clean_brand

            # Enhanced SKU extraction with validation
            sku_value = json_ld_data.get('sku')
            if sku_value:
                clean_sku = self._sanitize_sku_field(str(sku_value))
                product.sku = clean_sku

            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                price = offers.get('price')
                if price:
                    product.current_price = float(price)
                    product.currency = offers.get('priceCurrency', 'THB')
                    product.extraction_metadata['price_pattern'] = 'btv_json_ld'

            image = json_ld_data.get('image')
            if image:
                if isinstance(image, list):
                    product.images = image
                elif isinstance(image, str):
                    product.images = [image]

        # 2. Extract attributes from "Quick Info" section (HTML) with enhanced sanitization
        # Pattern: <label class="quickInfo-infoLabel-WkG">Label</label><label class="quickInfo-infoValue-NpP">Value</label>
        quick_info_pattern = r'class="quickInfo-infoLabel-[^"]+">([^<]+)</label><label class="quickInfo-infoValue-[^"]+">([^<]+)</label>'
        attributes = dict(re.findall(quick_info_pattern, html_content))

        if attributes:
            # Enhanced color extraction with CSS prevention
            if 'สี' in attributes and not product.color:
                color_value = attributes['สี'].strip()
                clean_color = self._sanitize_color_field(color_value)
                product.color = clean_color

            # Enhanced dimensions extraction
            if 'ขนาดสินค้า' in attributes:
                dimensions_value = attributes['ขนาดสินค้า'].strip()
                clean_dimensions = self._sanitize_dimensions_field(dimensions_value)
                product.dimensions = clean_dimensions

            # Enhanced volume extraction - get weight (น้ำหนัก) instead of unit count
            if 'น้ำหนัก' in attributes:
                weight_value = attributes['น้ำหนัก'].strip()
                clean_weight = self._sanitize_text_field(weight_value, max_length=50)
                product.volume = clean_weight

        # 2.3. Fallback: Extract weight from specifications tab or other patterns
        if not product.volume:
            # Try to find weight in various formats
            weight_patterns = [
                # Boonthavorn productAttributes pattern: <span class="productAttributes-name">น้ำหนัก</span>...<div class="richContent-root">2.2 KG</div>
                r'productAttributes-name[^>]*>น้ำหนัก</span>.*?richContent-root[^>]*>([^<]+)</div>',
                # Pattern: น้ำหนัก followed by number and unit
                r'น้ำหนัก[:\s]*([0-9.]+\s*(?:KG|kg|Kg|กก\.|กิโลกรัม))',
                # Pattern in table/list format
                r'>น้ำหนัก<[^>]*>[^<]*<[^>]*>([^<]+(?:KG|kg|กก))',
                # General weight pattern
                r'(?:Weight|weight)[:\s]*([0-9.]+\s*(?:KG|kg))',
            ]
            for pattern in weight_patterns:
                weight_match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
                if weight_match:
                    product.volume = weight_match.group(1).strip()
                    break

        if attributes:
            # Enhanced brand extraction
            if 'ยี่ห้อ' in attributes and not product.brand:
                brand_value = attributes['ยี่ห้อ'].strip()
                clean_brand = self._sanitize_brand_field(brand_value)
                if clean_brand:
                    product.brand = clean_brand

            # Enhanced SKU extraction with validation
            if 'รหัสสินค้า' in attributes and (not product.sku or product.sku == 'None'):
                sku_value = attributes['รหัสสินค้า'].strip()
                clean_sku = self._sanitize_sku_field(sku_value)
                if clean_sku:
                    product.sku = clean_sku

        # 2.5. Extract category from breadcrumbs - get the last category link (parent of product)
        # Pattern: <a class="breadcrumbs-link-mHX" href="...">CATEGORY</a>
        breadcrumb_links = re.findall(r'<a[^>]*class="breadcrumbs-link-[^"]*"[^>]*>([^<]+)</a>', html_content)
        if breadcrumb_links and len(breadcrumb_links) > 0:
            # The last link is the immediate parent category (before product name)
            category = breadcrumb_links[-1].strip()
            if category:
                product.category = category

        # 3. Extract Original Price (if exists) with enhanced cleaning
        if not product.original_price:
            old_price_match = re.search(r'productPrice-oldPrice.*?price-currency-[^>]+>บาท</span>((?:<span>[^<]+</span>)+)', html_content)
            if old_price_match:
                raw_price = old_price_match.group(1)
                # Remove tags and commas with enhanced cleaning
                clean_price = re.sub(r'<[^>]+>|,', '', raw_price)
                clean_price = ' '.join(clean_price.split()).strip()
                try:
                    product.original_price = float(clean_price)
                except ValueError:
                    pass

        # 4. Fallback/Supplement with base HTML extraction
        html_product = super().extract_from_html(html_content, url)
        if html_product:
            if not product.name: product.name = html_product.name
            if not product.description: product.description = html_product.description
            if not product.images: product.images = html_product.images

            # Use base extraction for material if not found yet
            if html_product.material and not product.material:
                product.material = html_product.material

        # 5. Extract Model from Name or Description with enhanced pattern matching
        if product.name and 'รุ่น' in product.name:
            model_match = re.search(r'รุ่น\s+([A-Za-z0-9\-_\s]+)', product.name)
            if model_match:
                model_value = model_match.group(1).strip()
                clean_model = self._sanitize_text_field(model_value, max_length=200)
                if clean_model:
                    product.model = clean_model
        elif product.description and 'รุ่น' in product.description:
            model_match = re.search(r'รุ่น\s+([A-Za-z0-9\-_\s]+)', product.description)
            if model_match:
                model_value = model_match.group(1).strip()
                clean_model = self._sanitize_text_field(model_value, max_length=200)
                if clean_model:
                    product.model = clean_model

        # 6. Enhanced URL-based SKU fallback with validation
        if url and (not product.sku or product.sku == 'None'):
            url_patterns = [
                r'-(\d+)$',
                r'/product/([^/]+)',
                r'/item/([^/]+)'
            ]
            for pattern in url_patterns:
                match = re.search(pattern, url)
                if match:
                    potential_sku = match.group(1).strip()
                    if self._is_valid_sku(potential_sku):
                        product.sku = potential_sku
                        break

        product.retailer = "Boonthavorn"
        return product

    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON-LD data from HTML."""
        try:
            # Regex to find the script tag content
            pattern = r'<script type="application/ld\+json">(.*?)</script>'
            matches = re.finditer(pattern, html_content, re.DOTALL)
            
            for match in matches:
                try:
                    data = json.loads(match.group(1))
                    # We are looking for @type Product
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        return data
                    # Sometimes it's a list of objects
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None


class MegaHomeExtractor(ProductExtractor):
    """Specialized extractor for Mega Home website with specific HTML patterns."""

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from Mega Home using its HTML patterns."""
        product = ProductData(url=url)

        # 1. Extract product name from prd-name h1
        name_match = re.search(r'<div class="prd-name">\s*<h1>([^<]+)</h1>', html_content)
        if name_match:
            product.name = name_match.group(1).strip()

        # 2. Extract brand from prd-brand
        brand_match = re.search(r'<div class="prd-brand">\s*<a[^>]*>([^<]+)</a>', html_content)
        if brand_match:
            product.brand = brand_match.group(1).strip()

        # 3. Extract price from discount-price, scale-price, or gtmPrice hidden input
        # Pattern 1: Normal discount price
        price_match = re.search(r'<div class="discount-price">.*?<span class="amount">([0-9,.]+)</span>', html_content, re.DOTALL)
        if price_match:
            try:
                product.current_price = float(price_match.group(1).replace(',', ''))
                product.extraction_metadata['price_pattern'] = 'mgh_discount_price'
            except ValueError:
                pass

        # Pattern 2: Scale price (bulk pricing) - must get the single-unit (1 ea) price, not the bulk price
        # Case A: Swiper carousel with per-quantity slides — find the "1 ea" slide
        # Format: <div class="swiper-slide" onclick="setItemScaling('1');">...1 ea...<span class="amount">50</span>
        # Case B: Simple scale-price range — get the LAST amount (highest = single unit price)
        # Format: <span class="scale-price">...<span class="amount">47</span> - <span class="amount">50</span>
        if not product.current_price:
            # Case A: Try swiper carousel first - find slide with onclick="setItemScaling('1')"
            # Note: quotes in HTML are encoded as &#39; not '
            swiper_1ea = re.search(
                r'onclick="setItemScaling\((?:\'|&#39;)1(?:\'|&#39;)\);".*?<span class="amount">([0-9,.]+)</span>',
                html_content, re.DOTALL
            )
            if swiper_1ea:
                try:
                    product.current_price = float(swiper_1ea.group(1).replace(',', ''))
                    product.extraction_metadata['price_pattern'] = 'mgh_swiper_1ea'
                except ValueError:
                    pass

            # Case B: Fallback - scale-price range, take the LAST amount (single unit = highest price)
            if not product.current_price:
                scale_price_match = re.search(r'<span class="scale-price">(.*?)</span>\s*</div>', html_content, re.DOTALL)
                if scale_price_match:
                    amounts = re.findall(r'<span class="amount">([0-9,.]+)</span>', scale_price_match.group(1))
                    if amounts:
                        try:
                            # Last amount is the single-unit price (highest in range)
                            product.current_price = float(amounts[-1].replace(',', ''))
                            product.extraction_metadata['price_pattern'] = 'mgh_scale_price_range'
                        except ValueError:
                            pass

        # Fallback to hidden gtmPrice input
        if not product.current_price:
            gtm_price = re.search(r'<input[^>]*id="gtmPrice-\d+"[^>]*value="([0-9.]+)"', html_content)
            if gtm_price:
                try:
                    product.current_price = float(gtm_price.group(1))
                    product.extraction_metadata['price_pattern'] = 'mgh_gtm_price'
                except ValueError:
                    pass

        # 4. Extract original price
        orig_price_match = re.search(r'<div class="original-price">.*?<span class="amount">([0-9,.]+)</span>', html_content, re.DOTALL)
        if orig_price_match:
            try:
                product.original_price = float(orig_price_match.group(1).replace(',', ''))
            except ValueError:
                pass

        # 5. Extract SKU from URL or hidden input
        if url:
            url_match = re.search(r'/p/(\d+)', url)
            if url_match:
                product.sku = url_match.group(1)

        # 6. Extract specification table values
        # MegaHome uses category prefixes: pdp-HT_ (Home Tools), pdp-LT_ (Lighting), etc.
        # Use generic pattern pdp-[A-Z]+_ to match any category

        # Material: pdp-*_MATERIAL
        material_match = re.search(r'class="pdp-[A-Z]+_MATERIAL"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)
        if material_match:
            product.material = material_match.group(1).strip()

        # Color: pdp-*_COLOR
        color_match = re.search(r'class="pdp-[A-Z]+_COLOR"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)
        if color_match:
            product.color = color_match.group(1).strip()

        # Dimensions: width x depth x height (skip label in first td, capture value in second td)
        width_match = re.search(r'class="pdp-[A-Z]+_WIDTH"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)
        depth_match = re.search(r'class="pdp-[A-Z]+_DEPTH"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)
        height_match = re.search(r'class="pdp-[A-Z]+_HEIGHT"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)

        dims = []
        if width_match:
            dims.append(width_match.group(1).strip())
        if depth_match:
            dims.append(depth_match.group(1).strip())
        if height_match:
            dims.append(height_match.group(1).strip())
        if dims:
            product.dimensions = ' x '.join(dims) + ' cm'

        # Weight: pdp-*_WEIGHT
        weight_match = re.search(r'class="pdp-[A-Z]+_WEIGHT"[^>]*>.*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', html_content, re.DOTALL)
        if weight_match:
            weight_val = weight_match.group(1).strip()
            if weight_val:
                product.volume = weight_val + ' kg'

        # 7. Extract images from image-index elements
        images = []
        img_matches = re.findall(r'<img[^>]*id="image-index-\d+"[^>]*src="([^"]+)"', html_content)
        for img_url in img_matches:
            if img_url and img_url not in images:
                images.append(img_url)
        product.images = images

        # 8. Extract category from breadcrumb (last item before product)
        breadcrumb_match = re.search(r'<div class="active section">([^<]+)</div>', html_content)
        # Get the breadcrumb links instead
        breadcrumb_links = re.findall(r'<a class="section"[^>]*>([^<]+)</a>', html_content)
        if breadcrumb_links:
            product.category = breadcrumb_links[-1].strip()

        # 9. Extract color from product name if not found in spec table (สีเงิน, สีดำ, etc.)
        if not product.color and product.name:
            color_match = re.search(r'สี(\S+)', product.name)
            if color_match:
                product.color = 'สี' + color_match.group(1)

        # 10. Extract model from product name (NO.2000, รุ่น XXX, etc.)
        if product.name:
            model_match = re.search(r'(?:NO\.|รุ่น\s*)([A-Za-z0-9\-_.]+)', product.name)
            if model_match:
                product.model = model_match.group(1).strip()

        product.retailer = "Mega Home"
        return product


class DoHomeExtractor(ProductExtractor):
    """Specialized extractor for DoHome website."""

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from DoHome."""
        product = ProductData(url=url)

        # 1. Try JSON-LD extraction first
        json_ld_data = self._extract_json_ld(html_content)

        if json_ld_data:
            product.name = json_ld_data.get('name')
            product.description = json_ld_data.get('description')

            brand_value = json_ld_data.get('brand')
            if brand_value:
                if isinstance(brand_value, dict):
                    brand_value = brand_value.get('name')
                if brand_value:
                    product.brand = self._sanitize_brand_field(str(brand_value))

            sku_value = json_ld_data.get('sku')
            if sku_value:
                product.sku = self._sanitize_sku_field(str(sku_value))

            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                price = offers.get('price')
                if price:
                    try:
                        product.current_price = float(price)
                        product.extraction_metadata['price_pattern'] = 'dh_json_ld'
                    except (ValueError, TypeError):
                        pass

            image = json_ld_data.get('image')
            if image:
                if isinstance(image, list):
                    product.images = image
                elif isinstance(image, str):
                    product.images = [image]

        # 2. Extract from HTML patterns specific to DoHome
        # Product name from h1 or specific DoHome classes
        if not product.name:
            name_patterns = [
                r'<h1[^>]*class="[^"]*product[^"]*name[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*class="[^"]*pdp[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*>(.*?)</h1>',
                r'<div[^>]*class="[^"]*product-name[^"]*"[^>]*>(.*?)</div>',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    name = self._clean_text(match.group(1))
                    if name and len(name) > 3 and 'dohome' not in name.lower():
                        product.name = name
                        break

        # 3. Extract price from DoHome-specific patterns
        if not product.current_price:
            # DoHome uses Next.js/React with Tailwind CSS classes
            price_patterns = [
                # DoHome main price: <span class="text-3xl font-semibold text-[#343A40]">฿1,090.00</span>
                r'<span[^>]*class="[^"]*text-3xl[^"]*font-semibold[^"]*"[^>]*>฿?([\d,]+(?:\.\d{2})?)</span>',
                # JSON data in page: "marketPrice":"฿1,090.00"
                r'"marketPrice"\s*:\s*"฿?([\d,]+(?:\.\d{2})?)"',
                # Sale price in JSON: "salePrice":"฿999.00"
                r'"salePrice"\s*:\s*"฿?([\d,]+(?:\.\d{2})?)"',
                # Generic price with ฿ symbol
                r'>฿([\d,]+(?:\.\d{2})?)<',
                # Legacy patterns
                r'<span[^>]*class="[^"]*price[^"]*"[^>]*>(.*?)</span>',
                r'ราคา[:\s]*([฿]?[\d,]+\.?\d*)',
            ]
            pattern_names = ['dh_text_3xl_semibold', 'dh_json_market_price', 'dh_json_sale_price', 'dh_generic_baht', 'dh_price_span', 'dh_thai_price_text']
            for idx, pattern in enumerate(price_patterns):
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    price_text = self._clean_text(match.group(1))
                    price = PriceParser.parse_price(price_text)
                    if price and price > 0:
                        product.current_price = price
                        product.extraction_metadata['price_pattern'] = pattern_names[idx]
                        break

        # 4. Extract original price
        if not product.original_price:
            orig_patterns = [
                # DoHome 2024 pattern - strikethrough <s> tag for original price
                # Format: <s class="text-[16px] text-[#343A40] font-light">฿937.00</s>
                r'<s[^>]*>฿?([\d,]+(?:\.\d{2})?)</s>',
                r'<span[^>]*class="[^"]*old-price[^"]*"[^>]*>(.*?)</span>',
                r'<span[^>]*class="[^"]*regular-price[^"]*"[^>]*>(.*?)</span>',
                r'ราคาปกติ[:\s]*([฿]?[\d,]+\.?\d*)',
            ]
            for pattern in orig_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    price_text = self._clean_text(match.group(1))
                    price = PriceParser.parse_price(price_text)
                    if price and price > 0:
                        product.original_price = price
                        break

        # 5. Extract SKU from URL pattern: /product/product-name-SKU
        if url and not product.sku:
            # DoHome URL pattern: /product/product-name-10026550
            sku_match = re.search(r'-(\d{6,})(?:\?|$)', url)
            if sku_match:
                potential_sku = sku_match.group(1)
                if self._is_valid_sku(potential_sku):
                    product.sku = potential_sku

        # 6. Extract brand from HTML - DoHome specific patterns
        if not product.brand:
            brand_patterns = [
                # DoHome brand link: <a href="/brand/nippon">NIPPON</a>
                r'<a[^>]*href="/brand/[^"]*"[^>]*>([^<]+)</a>',
                # Fallback patterns
                r'<span[^>]*class="[^"]*brand[^"]*"[^>]*>([^<]+)</span>',
            ]
            for pattern in brand_patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    brand = self._clean_text(match.group(1))
                    # Validate brand - must be short and not contain garbage
                    if brand and len(brand) < 50 and not any(x in brand.lower() for x in ['attribute', 'product', 'sku', 'stock', 'link']):
                        product.brand = brand.strip()
                        break

        # 7. Extract category from DoHome breadcrumb
        if not product.category:
            # DoHome category in breadcrumb: <a href="/category/...">Category Name</a>
            cat_patterns = [
                r'<a[^>]*href="/category/[^"]*"[^>]*>([^<]+)</a>',
                r'"categoryName"\s*:\s*"([^"]+)"',
            ]
            for pattern in cat_patterns:
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                for cat in matches:
                    cat = self._clean_text(cat)
                    if cat and len(cat) > 2 and cat.lower() not in ['หน้าแรก', 'home', 'สินค้า', 'products', 'dohome']:
                        product.category = cat
                        break
                if product.category:
                    break

        # 8. Extract specifications from DoHome JSON data in page
        # DoHome embeds specs in Next.js script tags with escaped quotes: \"dimension\":{\"width\":29.6,...}
        # Extract dimension values directly using regex (handles both escaped and normal quotes)
        dimension_match = re.search(
            r'\\?"dimension\\?"\s*:\s*\{[^}]*\\?"width\\?"\s*:\s*([\d.]+)[^}]*\\?"long\\?"\s*:\s*([\d.]+)[^}]*\\?"high\\?"\s*:\s*([\d.]+)[^}]*\\?"weight\\?"\s*:\s*([\d.]+)',
            html_content
        )
        if dimension_match:
            width = dimension_match.group(1)
            length = dimension_match.group(2)
            height = dimension_match.group(3)
            weight = dimension_match.group(4)

            # Build dimensions string
            if width and length and height:
                product.dimensions = f"{width} x {length} x {height} cm"

            # Store weight
            if weight and not product.volume:
                product.volume = f"{weight} kg"

        # Extract model from specifications
        model_match = re.search(r'\\?"productModel\\?"\s*:\s*\\?"([^"\\]+)\\?"', html_content)
        if model_match and not product.model:
            product.model = model_match.group(1)

        # 9. Extract images if not found
        if not product.images:
            product.images = self._extract_images(html_content)

        product.retailer = "DoHome"
        return product

    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON-LD data from HTML."""
        try:
            pattern = r'<script type="application/ld\+json">(.*?)</script>'
            matches = re.finditer(pattern, html_content, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        return data
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None


class GlobalHouseExtractor(ProductExtractor):
    """Specialized extractor for Global House website."""

    def _extract_next_data(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract __NEXT_DATA__ from Next.js pages."""
        try:
            match = re.search(r'__NEXT_DATA__[^>]*type="application/json">(.+?)</script>', html_content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except (json.JSONDecodeError, Exception):
            pass
        return None

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        """Extract product data specifically from Global House."""
        product = ProductData(url=url)

        # 1. Extract SKU from URL first (most reliable for GlobalHouse)
        # Global House URL pattern: /product/MAZUMA-...-i.8852163012022
        if url:
            sku_match = re.search(r'-i\.(\d+)(?:\?|$)', url)
            if sku_match:
                potential_sku = sku_match.group(1)
                if self._is_valid_sku(potential_sku):
                    product.sku = potential_sku

        # 2. Extract SKU from HTML if not found in URL
        # GlobalHouse displays: <div class="text-xs text-gray-400">รหัสสินค้า : 8852163012022</div>
        if not product.sku:
            sku_html_match = re.search(r'รหัสสินค้า\s*:\s*(\d+)', html_content)
            if sku_html_match:
                potential_sku = sku_html_match.group(1)
                if self._is_valid_sku(potential_sku):
                    product.sku = potential_sku

        # 3. Try __NEXT_DATA__ extraction (Next.js specific)
        next_data = self._extract_next_data(html_content)
        if next_data:
            ast_data = next_data.get('props', {}).get('pageProps', {}).get('ast', {}).get('data', {})
            if ast_data:
                # Extract from attributes
                attrs = ast_data.get('attributes', [])
                width_val = depth_val = height_val = None
                for attr in attrs:
                    title = attr.get('title', '')
                    detail = attr.get('detail', '')
                    if title == 'รุ่น' and detail:
                        product.model = detail
                    elif 'กว้าง' in title and detail:
                        width = re.search(r'([\d.]+)', detail)
                        if width:
                            width_val = width.group(1)
                    elif 'ยาว' in title and detail:
                        depth = re.search(r'([\d.]+)', detail)
                        if depth:
                            depth_val = depth.group(1)
                    elif 'สูง' in title and detail:
                        height = re.search(r'([\d.]+)', detail)
                        if height:
                            height_val = height.group(1)

                # Build dimensions from extracted values
                dims = [d for d in [width_val, depth_val, height_val] if d]
                if dims:
                    product.dimensions = ' x '.join(dims) + ' cm'

                # Get description from htmlContent
                html_contents = ast_data.get('htmlContent', [])
                for hc in html_contents:
                    if hc.get('title') == 'คุณสมบัติเด่น' and hc.get('detail'):
                        # Strip HTML tags
                        desc = re.sub(r'<[^>]+>', ' ', hc.get('detail', ''))
                        desc = re.sub(r'\s+', ' ', desc).strip()
                        if desc and len(desc) > 10:
                            product.description = desc[:500]  # Limit length
                            break

        # 2. Try JSON-LD extraction
        json_ld_data = self._extract_json_ld(html_content)

        if json_ld_data:
            if not product.name:
                product.name = json_ld_data.get('name')
            if not product.description:
                product.description = json_ld_data.get('description')

            brand_value = json_ld_data.get('brand')
            if brand_value:
                if isinstance(brand_value, dict):
                    brand_value = brand_value.get('name')
                if brand_value:
                    product.brand = self._sanitize_brand_field(str(brand_value))

            sku_value = json_ld_data.get('sku')
            if sku_value:
                product.sku = self._sanitize_sku_field(str(sku_value))

            offers = json_ld_data.get('offers', {})
            if isinstance(offers, dict):
                price = offers.get('price')
                if price:
                    try:
                        product.current_price = float(price)
                        product.extraction_metadata['price_pattern'] = 'gbh_json_ld'
                    except (ValueError, TypeError):
                        pass

            image = json_ld_data.get('image')
            if image:
                if isinstance(image, list):
                    product.images = image
                elif isinstance(image, str):
                    product.images = [image]

        # 2. Extract product name from Global House specific patterns
        if not product.name:
            name_patterns = [
                r'<h1[^>]*class="[^"]*product[^"]*title[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*class="[^"]*pdp-title[^"]*"[^>]*>(.*?)</h1>',
                r'<div[^>]*class="[^"]*product-title[^"]*"[^>]*>(.*?)</div>',
                r'<h1[^>]*>(.*?)</h1>',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    name = self._clean_text(match.group(1))
                    if name and len(name) > 3 and 'global house' not in name.lower():
                        product.name = name
                        break

        # 3. Extract price from Global House specific patterns
        if not product.current_price:
            price_patterns = [
                # GlobalHouse 2024 patterns - sale price in red text-3xl (support decimals)
                r'<span[^>]*class="[^"]*text-3xl[^"]*text-red[^"]*"[^>]*>฿?([\d,]+(?:\.\d+)?)</span>',
                r'<span[^>]*class="[^"]*text-red[^"]*text-3xl[^"]*"[^>]*>฿?([\d,]+(?:\.\d+)?)</span>',
                # Font-bold price pattern (support decimals)
                r'<span[^>]*class="[^"]*font-bold[^"]*text-3xl[^"]*"[^>]*>฿?([\d,]+(?:\.\d+)?)</span>',
                # Generic large price display (support decimals)
                r'<span[^>]*class="[^"]*text-(?:2|3)xl[^"]*"[^>]*>฿?([\d,]+(?:\.\d+)?)</span>',
                # Legacy patterns
                r'<span[^>]*class="[^"]*price[^"]*final[^"]*"[^>]*>(.*?)</span>',
                r'<span[^>]*class="[^"]*selling-price[^"]*"[^>]*>(.*?)</span>',
                r'<div[^>]*class="[^"]*product-price[^"]*"[^>]*>.*?([฿\d,\.]+).*?</div>',
                r'ราคา[:\s]*([฿]?[\d,]+\.?\d*)',
            ]
            pattern_names = [
                'gbh_text_3xl_red', 'gbh_text_red_3xl', 'gbh_font_bold_3xl', 'gbh_text_large',
                'gbh_price_final', 'gbh_selling_price', 'gbh_product_price_div', 'gbh_thai_price_text'
            ]
            for idx, pattern in enumerate(price_patterns):
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    price_text = self._clean_text(match.group(1))
                    price = PriceParser.parse_price(price_text)
                    if price and price > 0:
                        product.current_price = price
                        product.extraction_metadata['price_pattern'] = pattern_names[idx]
                        break

        # 4. Extract original price (only if "ราคาเดิม" is present)
        if not product.original_price:
            orig_patterns = [
                # GlobalHouse 2024 pattern - ราคาเดิม followed by line-through price
                # Format: <span>ราคาเดิม</span><span class="line-through">฿35,990</span>
                r'ราคาเดิม</span>\s*<span[^>]*class="[^"]*line-through[^"]*"[^>]*>฿?([\d,]+)</span>',
                # Thai word for original price with nearby number
                r'ราคาเดิม.*?฿([\d,]+)',
                # Legacy patterns
                r'<span[^>]*class="[^"]*price[^"]*original[^"]*"[^>]*>(.*?)</span>',
                r'<span[^>]*class="[^"]*was-price[^"]*"[^>]*>(.*?)</span>',
                r'ราคาปกติ[:\s]*([฿]?[\d,]+\.?\d*)',
            ]
            for pattern in orig_patterns:
                match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    price_text = self._clean_text(match.group(1))
                    price = PriceParser.parse_price(price_text)
                    if price and price > 0:
                        product.original_price = price
                        break

        # 5b. Extract specs from table-cell pairs (available after clicking "ข้อมูลจำเพาะ" tab)
        # Pattern: <td data-slot="table-cell" ...>KEY</td><td data-slot="table-cell" ...>VALUE</td>
        spec_pairs = re.findall(
            r'data-slot="table-cell"[^>]*>([^<]+)</td>\s*<td[^>]*data-slot="table-cell"[^>]*>(.*?)</td>',
            html_content,
            re.DOTALL
        )
        if spec_pairs:
            width_val = depth_val = height_val = None
            for key, val in spec_pairs:
                key = key.strip()
                val = self._clean_text(val)
                if not val:
                    continue
                if key == 'รุ่น' and not product.model:
                    product.model = val
                elif key == 'แบรนด์' and not product.brand:
                    product.brand = self._sanitize_brand_field(val)
                elif 'กว้าง' in key:
                    m = re.search(r'([\d.]+)', val)
                    if m:
                        width_val = m.group(1)
                elif 'ยาว' in key:
                    m = re.search(r'([\d.]+)', val)
                    if m:
                        depth_val = m.group(1)
                elif 'สูง' in key:
                    m = re.search(r'([\d.]+)', val)
                    if m:
                        height_val = m.group(1)
            if not product.dimensions:
                dims = [d for d in [width_val, depth_val, height_val] if d]
                if dims:
                    product.dimensions = ' x '.join(dims) + ' cm'

        # 6. Extract brand from HTML or product name if not found
        if not product.brand:
            # Try GlobalHouse header brand pattern first: สินค้าแบรนด์ : <a>BRAND</a>
            brand_header = re.search(
                r'สินค้าแบรนด์[^<]*</span>\s*<a[^>]*>([^<]+)</a>',
                html_content, re.IGNORECASE
            )
            if brand_header:
                product.brand = self._sanitize_brand_field(brand_header.group(1).strip())

        if not product.brand:
            # Try HTML patterns
            brand_patterns = [
                r'<span[^>]*class="[^"]*brand[^"]*"[^>]*>(.*?)</span>',
                r'<a[^>]*class="[^"]*brand[^"]*"[^>]*>(.*?)</a>',
                r'ยี่ห้อ[:\s]*([^\n<]+)',
                r'แบรนด์[:\s]*([^\n<]+)',
            ]
            for pattern in brand_patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    brand = self._clean_text(match.group(1))
                    if brand:
                        product.brand = self._sanitize_brand_field(brand)
                        break

            # Try extracting from URL (first word is usually brand)
            if not product.brand and url:
                url_brand_match = re.search(r'/product/([A-Za-z0-9]+)-', url)
                if url_brand_match:
                    potential_brand = url_brand_match.group(1)
                    if len(potential_brand) >= 2:
                        product.brand = potential_brand

        # 7. Extract category from breadcrumb (GlobalHouse specific)
        # Look for the last breadcrumb-link before the product title
        breadcrumb_matches = re.findall(r'<a[^>]*data-slot="breadcrumb-link"[^>]*title="([^"]+)"', html_content)
        if breadcrumb_matches:
            # Get the last category (most specific), but not if it's the home or generic pages
            for cat in reversed(breadcrumb_matches):
                if cat and cat not in ['หน้าแรก', 'หมวดหมู่', 'สินค้า'] and len(cat) > 2:
                    product.category = cat
                    break

        # 8. Extract color from product name (สีขาว, สีดำ, etc.)
        if not product.color and product.name:
            color_match = re.search(r'สี(\S+)', product.name)
            if color_match:
                product.color = 'สี' + color_match.group(1)

        # 9. Extract images from Next.js image srcset
        if not product.images:
            # Look for image URLs from the image-gbh.com CDN
            img_matches = re.findall(r'https://www\.image-gbh\.com/uploads/[^"&\s]+\.(?:jpg|jpeg|png)', html_content)
            if img_matches:
                # Remove duplicates and limit
                seen = set()
                unique_images = []
                for img in img_matches:
                    if img not in seen:
                        seen.add(img)
                        unique_images.append(img)
                        if len(unique_images) >= 10:
                            break
                product.images = unique_images

        # 10. Don't use base class volume/dimensions extraction - it gives garbage for GlobalHouse
        # GlobalHouse product specs are loaded dynamically via JavaScript
        # Clear any incorrectly extracted values
        if product.volume and len(str(product.volume)) < 3:
            product.volume = None
        if product.dimensions and len(str(product.dimensions)) < 5:
            product.dimensions = None

        product.retailer = "Global House"
        return product

    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON-LD data from HTML."""
        try:
            pattern = r'<script type="application/ld\+json">(.*?)</script>'
            matches = re.finditer(pattern, html_content, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        return data
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None


class MakroExtractor(ProductExtractor):
    """Makro PRO extractor — JSON-LD + rendered-DOM (+ __NEXT_DATA__ as a narrow
    step_prices fallback only).

    Motivation (see ai_sum/sessions/2026-07-07_makro-extractor-v2-design.md):
    An earlier version read prices from the __NEXT_DATA__ SSR blob. This extractor
    deliberately avoids it for the PRIMARY read and reads from two
    framework-independent sources instead:
      - JSON-LD Product block  -> name, description, image, current_price (base,
                                  i.e. the 1-unit price used for cross-retailer
                                  comparison).
      - the rendered DOM        -> brand, original_price, step_prices, volume,
                                  anchored on STABLE data-test-id attributes
                                  (never the build-hashed MUI css-* classes).

    step_prices come from the DOM's "Buy more save more!" slab section, which is
    client-rendered — so this extractor REQUIRES a browser render with scroll
    (crawl4ai in browser mode). A plain HTTP fetch shows only one tier. As a
    SINGLE, narrow exception to the "no __NEXT_DATA__" rule, when that DOM ladder
    comes back empty (a render/scroll shortfall) step_prices fall back to the
    __NEXT_DATA__ slabPriceTiers, which live in the initial server HTML and so
    survive that specific failure (see _next_data_step_prices). The DOM stays
    authoritative for every field; __NEXT_DATA__ never overrides a present value.

    It is self-contained (subclasses the generic ProductExtractor). The small
    JSON-LD helpers below
    (_extract_json_ld, _dig, _to_number, _norm_text, _resolve_images,
    _calc_unit_price) are Makro-shaped and intentionally local to this class.
    """

    def extract_from_html(self, html_content: str, url: str = None) -> Optional[ProductData]:
        product = ProductData(url=url)

        json_ld = self._extract_json_ld(html_content) or {}

        # Descriptive fields straight from JSON-LD (framework-independent SEO data).
        product.name = self._norm_text(json_ld.get('name')) or ''
        product.description = self._norm_text(json_ld.get('description'))
        product.images = self._resolve_images(json_ld.get('image'), None)

        # current_price = base (1-unit) price from JSON-LD offers.
        product.current_price = self._to_number(self._dig(json_ld, 'offers.price'))

        # sku — prefer the DOM code element ("Code : 31915"): it is the clean
        # article code Makro shows to users, unlike the URL slug which can be a
        # long product-name string (e.g. "KOBE-YA Unagi Kabayaki - 11522 - 3P …").
        product.sku = self._dom_sku(html_content)
        if not product.sku and url:
            # Fallback: derive from the URL slug. The path segment after /p/ is
            # "<article>-<shopifyId>", so strip the trailing "-<shopifyId>". The
            # article code may be numeric (835081), alphanumeric (brJTHNO) or
            # contain dashes (KOBE-YA, M-02-12-01). The "product-<shopifyId>"
            # placeholder form has no real article code -> use the Shopify id.
            m = re.search(r'/p/([^/?#]+)', url)
            if m:
                seg = m.group(1)
                if seg.startswith('product-'):
                    product.sku = seg[len('product-'):]
                else:
                    product.sku = re.sub(r'-\d+$', '', seg) or seg

        # step_prices — the "Buy more save more!" slab ladder, from the DOM only.
        product.step_prices = self._dom_step_prices(html_content)

        # volume (pack size string) from the DOM, and unit_price derived from it.
        product.volume = self._dom_volume(html_content)
        product.unit_price = self._calc_unit_price(product.current_price, product.volume or '')

        # brand — JSON-LD carries none for Makro, so read the DOM brand_title.
        product.brand = self._dom_brand(html_content)

        # original_price — the strike-through price, keyed by the product's shopify
        # gid so we ignore related-product cards. Only kept when it is a REAL
        # discount (strictly greater than current_price); Makro renders the element
        # even at parity, which would otherwise fabricate a 0% discount.
        shop_id = self._shopify_id(url, json_ld)
        orig = self._dom_original_price(html_content, shop_id)
        if (orig is not None and product.current_price is not None
                and orig > product.current_price):
            product.original_price = orig

        # Recompute discount flags now that prices are set (ProductData ran this at
        # construction, before prices existed).
        product._calculate_discounts()

        # Observability: record where prices came from and flag a DOM that looks
        # un-rendered (step_prices/brand/original_price are DOM-only, so a plain
        # HTTP fetch would silently drop them — surface that instead of hiding it).
        meta = product.extraction_metadata
        meta['price_source'] = 'json_ld' if product.current_price is not None else 'missing'

        # step_prices: the DOM slab is authoritative. Fall back to the
        # __NEXT_DATA__ ladder (server-rendered, no scroll) ONLY when the DOM
        # ladder is empty — i.e. when a render/scroll shortfall dropped it. When
        # both exist but disagree, keep DOM and surface the conflict (never
        # silently reconcile). See ai_sum/past-implement/2026-07-12_*.
        nd_steps = self._next_data_step_prices(
            html_content, product.sku, product.current_price)
        if not product.step_prices and nd_steps:
            product.step_prices = nd_steps
            meta['step_prices_source'] = 'next_data'
        elif product.step_prices:
            meta['step_prices_source'] = 'dom_slab'
            if nd_steps and [list(t) for t in product.step_prices] != nd_steps:
                meta.setdefault('conflicts', {})['step_prices_dom_vs_nextdata'] = {
                    'dom_slab': [list(t) for t in product.step_prices],
                    'next_data': nd_steps}
            # tier_0 is the base rung and must equal the JSON-LD base price.
            if (product.current_price is not None and product.step_prices[0][0] == 1
                    and product.step_prices[0][1] != product.current_price):
                meta.setdefault('conflicts', {})['step_tier0_vs_current'] = {
                    'json_ld': product.current_price,
                    'dom_tier0': product.step_prices[0][1]}
        if 'data-test-id' not in html_content:
            meta['dom_rendered'] = False
            print(f"[MakroExtractor] WARNING: no data-test-id in HTML for {url} "
                  "— DOM likely not browser-rendered; brand/step_prices/"
                  "original_price may be missing.", file=sys.stderr)
        elif product.current_price is None:
            # ALARM: the DOM *is* rendered, yet JSON-LD carried no offers.price.
            # We have never observed this in the wild (every fixture has it), so
            # if it ever fires the page layout likely changed and prices will
            # silently stop flowing. Deliberately do NOT substitute a DOM value:
            # the _price structure of such an unseen page is untested, and a
            # wrong price entering price comparison is worse than a missing one.
            # Record what the DOM shows and shout, so it's caught the day it
            # happens (and we get a real sample to build the fixture + fallback).
            meta['price_missing_dom_rendered'] = True
            observed = self._dom_current_price_observed(html_content, shop_id)
            if observed is not None:
                meta['dom_price_observed'] = observed
            print(f"[MakroExtractor] ALARM: JSON-LD had no offers.price but the "
                  f"DOM is rendered for {url} — site layout likely changed. "
                  f"current_price left empty (NOT substituted from DOM). "
                  f"DOM price element shows: {observed}.", file=sys.stderr)

        product.retailer = "Makro"
        return product

    # ------------------------------------------------------------------
    # JSON-LD helpers (Makro-shaped; local to keep this class self-contained)
    # ------------------------------------------------------------------
    def _extract_json_ld(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Extract and parse the JSON-LD Product block from HTML."""
        try:
            pattern = r'<script type="application/ld\+json">(.*?)</script>'
            for match in re.finditer(pattern, html_content, re.DOTALL):
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get('@type') == 'Product':
                    return data
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get('@type') == 'Product':
                            return item
        except Exception:
            pass
        return None

    @staticmethod
    def _dig(obj: Any, path: str) -> Any:
        """Walk a dot-path (e.g. 'offers.price') through nested dicts.

        Makro's JSON-LD `offers` is normally a dict, but per schema.org it may be
        a list; in that case we read the first offer. Returns None if any hop is
        missing.
        """
        cur = obj
        for key in path.split('.'):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    @staticmethod
    def _to_number(value: Any) -> Optional[float]:
        """Normalize a price-ish value to a rounded float, or None if not numeric."""
        if value is None:
            return None
        try:
            return round(float(str(value).replace(',', '').strip()), 2)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _norm_text(value: Any) -> Optional[str]:
        """Normalize a text value to a trimmed string, or None if empty."""
        if value is None:
            return None
        text = ' '.join(str(value).split()).strip()
        return text or None

    def _resolve_images(self, jl_images: Any, _unused: Any = None) -> List[str]:
        """JSON-LD `image` may be a single URL string or a list; normalize to list."""
        if not jl_images:
            return []
        if isinstance(jl_images, str):
            return [jl_images]
        if isinstance(jl_images, list):
            return [img for img in jl_images if img]
        return []

    def _calc_unit_price(self, current_price: Optional[float],
                         size: str) -> Optional[float]:
        """Divide the pack price by the pack quantity parsed from the size string.

        "20 หน่วย" -> qty 20 -> unit_price = current_price / 20. If no leading
        quantity is found, unit_price equals current_price (a pack of one).
        """
        if current_price is None:
            return None
        if size:
            m = re.match(r'^\s*(\d+(?:\.\d+)?)', size)
            if m:
                qty = float(m.group(1))
                if qty > 1:
                    return round(current_price / qty, 2)
        return current_price

    # ------------------------------------------------------------------
    # Rendered-DOM helpers (anchored on stable data-test-id attributes)
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_tags(text: str) -> str:
        """Drop tags/entities from a captured DOM fragment and collapse spaces."""
        text = re.sub(r'<[^>]+>', ' ', text or '')
        text = text.replace('&nbsp;', ' ')
        return ' '.join(text.split()).strip()

    def _dom_brand(self, html_content: str) -> Optional[str]:
        """Brand from the DOM brand_title element (JSON-LD has no brand for Makro).

        Renders as: <... data-test-id="brand_title"><div ...>ARO GOLD</div>...
        """
        m = re.search(r'data-test-id="brand_title"[^>]*>\s*<[^>]+>([^<]+)<',
                      html_content, re.DOTALL)
        if not m:
            return None
        brand = self._strip_tags(m.group(1))
        return brand or None

    def _dom_sku(self, html_content: str) -> Optional[str]:
        """SKU (article code) from the DOM code element — the clean value shown to
        users, e.g. `31915` (not the messy URL slug).

        Renders as: <... data-test-id="makro_code_title">Code : 31915</div>
        The label is i18n ("Code : {{sku}}" / "รหัส : {{sku}}"), so take the part
        after the ":" separator.
        """
        m = re.search(r'data-test-id="makro_code_title"[^>]*>([^<]+)<',
                      html_content, re.DOTALL)
        if not m:
            return None
        text = self._strip_tags(m.group(1)).strip()
        if ':' in text:
            # Drop only the label prefix ("Code :" / "รหัส :") — split on the FIRST
            # colon so a sku that itself contains ":" is preserved intact.
            text = text.split(':', 1)[1]
        return text.strip() or None

    @staticmethod
    def _shopify_id(url: Optional[str], json_ld: Dict[str, Any]) -> Optional[str]:
        """The Shopify numeric id used to key the main product's DOM price elements.

        Prefer the URL's trailing id (…/p/835081-6974707695811 -> 6974707695811,
        …/p/product-487900393685730 -> 487900393685730); fall back to JSON-LD
        productID / @id (gid://shopify/Product/<id>). The article prefix may be
        alphanumeric or contain dashes (…/p/KOBE-YA-8697… , …/p/M-02-12-01-779…),
        so match the trailing numeric run rather than assuming a numeric prefix.
        """
        if url:
            m = re.search(r'/p/[^/?#]*?(\d+)(?:[/?#]|$)', url)
            if m:
                return m.group(1)
        pid = json_ld.get('productID') or json_ld.get('@id') or ''
        m = re.search(r'(\d+)\s*$', str(pid))
        return m.group(1) if m else None

    def _dom_original_price(self, html_content: str,
                            shop_id: Optional[str]) -> Optional[float]:
        """The strike-through price from the gid-keyed _original_price element."""
        if not shop_id:
            return None
        m = re.search(
            r'data-test-id="gid://shopify/Product/%s_original_price"[^>]*>(.*?)</'
            % re.escape(shop_id), html_content, re.DOTALL)
        if not m:
            return None
        return self._to_number(self._strip_tags(m.group(1)).replace('฿', ''))

    def _dom_current_price_observed(self, html_content: str,
                                    shop_id: Optional[str]) -> Optional[float]:
        """Read the gid-keyed current-price element from the DOM — for the ALARM
        below (observability) ONLY, never to substitute current_price.

        The main-product price renders with the baht sign and the number in
        SEPARATE <p> tags ("<p>฿</p><p>83</p>"), unlike _original_price
        ("<p>฿ 48</p>"). So we cannot reuse the non-greedy ">(.*?)</" capture —
        it stops at the first "</p>" and would grab only "฿". Instead grab a
        bounded window after the attribute, strip tags, and pull the first baht
        number out. Best-effort: a None here just means the alarm reports no
        observed value, which is fine.
        """
        if not shop_id:
            return None
        m = re.search(
            r'data-test-id="gid://shopify/Product/%s_price"[^>]*>(.{0,160})'
            % re.escape(shop_id), html_content, re.DOTALL)
        if not m:
            return None
        text = self._strip_tags(m.group(1)).replace('฿', ' ')
        num = re.search(r'\d[\d,]*(?:\.\d+)?', text)
        return self._to_number(num.group(0)) if num else None

    def _dom_volume(self, html_content: str) -> Optional[str]:
        """Pack size string from the DOM unit_type_title (e.g. '1 unit(s)').

        Absent for weighted (kg) products, which have no unit_type_title element.
        """
        m = re.search(r'data-test-id="unit_type_title"[^>]*>([^<]+)</',
                      html_content, re.DOTALL)
        if not m:
            return None
        return self._strip_tags(m.group(1)) or None

    def _dom_step_prices(self, html_content: str) -> List[list]:
        """Build [[min_qty, unit_price], ...] from the slab tier elements.

        Each rung renders as a pair of stable attributes:
            data-test-id="unit_tier_{N}"   ->  "1 - 3 units" / "8+ units" / "10+ kg"
            data-test-id="price_tier_{N}"   ->  "฿ 38"   (unit) / "฿ 78/kg"  (weighed)
        tier_0 is the base rung, so this list already starts at [1, base_price].
        Returns [] for products with no slab section (~70% of products).
        min_qty is the first integer in the unit label; price is parsed as float.

        Weighed (kg) products render a per-unit suffix on the price
        ("฿ 78/kg"), so we pull the baht number out with a regex rather than
        stripping the "฿" and feeding the whole string to _to_number — the
        latter chokes on the "/kg" tail and silently drops the whole ladder.
        """
        steps: List[list] = []
        n = 0
        while True:
            um = re.search(r'data-test-id="unit_tier_%d"[^>]*>(.*?)</div>' % n,
                           html_content, re.DOTALL)
            pm = re.search(r'data-test-id="price_tier_%d"[^>]*>(.*?)</div>' % n,
                           html_content, re.DOTALL)
            if not um or not pm:
                break
            qty_match = re.search(r'\d+', self._strip_tags(um.group(1)))
            price_match = re.search(r'฿\s*([\d,]+(?:\.\d+)?)',
                                    self._strip_tags(pm.group(1)))
            price = self._to_number(price_match.group(1)) if price_match else None
            if qty_match and price is not None:
                steps.append([int(qty_match.group()), price])
            n += 1
        return steps

    def _next_data_step_prices(self, html_content: str, sku: Optional[str],
                               current_price: Optional[float]) -> List[list]:
        """Fallback slab ladder from __NEXT_DATA__, for when the DOM slab is empty.

        The DOM "Buy more save more!" section is client-rendered and needs a
        SCROLLED browser render, so it silently vanishes if the render/scroll
        fell short. __NEXT_DATA__ carries the same tiers in the INITIAL server
        HTML at the stable path props.pageProps.product.slabPrices.slabPriceTiers
        (no scroll needed) — an uncorrelated source for exactly that failure.

        Returns [[min_qty, unit_price], ...] starting at [1, current_price]
        (slabPriceTiers omits the base rung), or [] when unavailable — a whole
        ladder or nothing, never partial. Guards:
          - current_price must exist: the base rung [1, current_price] is
            required, so without it we cannot build a valid ladder.
          - the node's own `sku` must match the extracted sku, so we never read
            a wrong/related product node from the blob.
        """
        if current_price is None:
            return []
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      html_content, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
            node = data['props']['pageProps']['product']
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
        if not isinstance(node, dict):
            return []
        # Safety: only trust the node if its sku matches the one we extracted
        # (mirrors the gid-keying used for the DOM original_price).
        node_sku = node.get('sku')
        if sku and node_sku is not None and str(node_sku) != str(sku):
            return []
        tiers = (node.get('slabPrices') or {}).get('slabPriceTiers') or []
        if not isinstance(tiers, list):
            return []
        ladder = [[1, current_price]]
        for t in tiers:
            if not isinstance(t, dict):
                continue
            qty = t.get('quantity')
            price = self._to_number(t.get('priceInVat'))
            if qty is None or price is None:
                continue
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                continue
            ladder.append([qty, price])
        if len(ladder) == 1:  # only the base rung -> no real slab data
            return []
        ladder.sort(key=lambda r: r[0])
        return ladder


def get_extractor(url: str) -> ProductExtractor:
    """Get the appropriate extractor for the given URL."""
    domain = urlparse(url).netloc.lower()

    if 'thaiwatsadu.com' in domain:
        return ThaiWatsaduExtractor(url)
    elif 'homepro.co.th' in domain:
        return HomeProExtractor(url)
    elif 'boonthavorn.com' in domain:
        return BoonthavornExtractor(url)
    elif 'dohome.co.th' in domain:
        return DoHomeExtractor(url)
    elif 'megahome.co.th' in domain:
        return MegaHomeExtractor(url)
    elif 'globalhouse.co.th' in domain:
        return GlobalHouseExtractor(url)
    elif 'makro.pro' in domain:
        # JSON-LD + rendered DOM (+ __NEXT_DATA__ step_prices fallback).
        return MakroExtractor(url)
    else:
        return ProductExtractor(url)

