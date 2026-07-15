#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pydantic",
#   "python-dotenv",
#   "click",
#   "rich",
#   "crawl4ai",
#   "psycopg2-binary",
# ]
# ///
"""
E-commerce Product Data Scraper

This AI Developer Workflow (ADW) script extracts structured product data from e-commerce websites
and outputs it in the specified JSON format with comprehensive product fields including pricing,
discount calculations, and physical attributes.

Usage:
    # Single product URL scraping
    ./adws/adw_ecommerce_product_scraper.py --url https://www.thaiwatsadu.com/th/sku/60363373

    # Batch scraping from file
    ./adws/adw_ecommerce_product_scraper.py --urls-file products.txt --output-file products.json

    # With custom configuration
    ./adws/adw_ecommerce_product_scraper.py --url https://example.com/product --max-concurrent 5 --delay 2.0

    # Test mode for validation
    ./adws/adw_ecommerce_product_scraper.py --url https://example.com/product --test
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich.progress import Progress, TaskID, BarColumn, TextColumn

# Add the parent directory to the path so we can import adw_modules as a package
sys.path.insert(0, os.path.dirname(__file__))

# Load environment variables
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(env_path)
except ImportError:
    pass


def _env_bool(key: str, default: bool = False) -> bool:
    """Get boolean value from environment variable."""
    value = os.environ.get(key, '').lower()
    if value in ('true', '1', 'yes', 'on'):
        return True
    elif value in ('false', '0', 'no', 'off'):
        return False
    return default


# Check if writing to agents folder is enabled
WRITE_AGENTS_FOLDER = _env_bool('SCRAPER_WRITE_AGENTS_FOLDER', True)

from adw_modules.crawl4ai_wrapper import (
    Crawl4AIWrapper,
    ScrapingConfig,
    ScrapingResult,
    create_simple_config,
)
from adw_modules.product_extractor import get_extractor
from adw_modules.product_schemas import ProductData, validate_product_data

def print_status_panel(
    console,
    action: str,
    adw_id: str,
    phase: str = None,
    status: str = "info",
    url: str = None
):
    """Print a status panel with timestamp and context."""
    timestamp = datetime.now().strftime("%H:%M:%S")

    # Choose color based on status
    if status == "success":
        border_style = "green"
        icon = "✅"
    elif status == "error":
        border_style = "red"
        icon = "❌"
    elif status == "warning":
        border_style = "yellow"
        icon = "⚠️"
    else:
        border_style = "cyan"
        icon = "🔄"

    # Build title with context
    title_parts = [f"[{timestamp}]", adw_id[:6]]
    if phase:
        title_parts.append(phase)
    if url and len(url) > 30:
        title_parts.append(url[:30] + "...")
    elif url:
        title_parts.append(url)
    title = " | ".join(title_parts)

    content = f"{icon} {action}"

    try:
        console.print(
            Panel(
                content,
                title=f"[bold {border_style}]{title}[/bold {border_style}]",
                border_style=border_style,
                padding=(0, 1),
            )
        )
    except Exception as e:
        # Fallback for Windows terminal rendering issues
        print(f"[{title}] {action}", flush=True)


def create_output_directory_structure(base_output_folder: str, adw_id: str, organization_type: str = "date") -> str:
    """Create organized output directory structure following ADW patterns.

    Args:
        base_output_folder: Base output folder path
        adw_id: ADW ID for tracking
        organization_type: How to organize subdirectories ("date" or "job-id")

    Returns:
        Path to the created output directory
    """
    try:
        # Validate base folder path
        base_path = Path(base_output_folder)

        # Create base directory if it doesn't exist
        base_path.mkdir(parents=True, exist_ok=True)

        # Check write permissions
        if not os.access(base_path, os.W_OK):
            raise click.ClickException(f"Base output folder is not writable: {base_output_folder}")

        # Create organized subdirectory
        if organization_type == "date":
            # Date-based organization: YYYY-MM-DD
            date_str = datetime.now().strftime("%Y-%m-%d")
            output_dir = base_path / date_str / adw_id
        else:
            # Job-ID based organization
            output_dir = base_path / adw_id

        # Create the full directory structure
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create standard subdirectories
        subdirs = ['raw', 'processed', 'logs', 'assets']
        for subdir in subdirs:
            (output_dir / subdir).mkdir(exist_ok=True)

        return str(output_dir)

    except Exception as e:
        raise click.ClickException(f"Failed to create output directory structure: {e}")


def load_urls_from_file(file_path: str) -> List[str]:
    """Load URLs from a text file."""
    try:
        if not os.path.exists(file_path):
            raise click.ClickException(f"File not found: {file_path}")

        if not os.access(file_path, os.R_OK):
            raise click.ClickException(f"File not readable: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            urls = []
            line_num = 0
            # Check if file is CSV
            is_csv = file_path.lower().endswith('.csv')
            
            for line in f:
                line_num += 1
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                    
                # Handle CSV format
                if is_csv:
                    # Skip header row if it contains 'url' or 'link'
                    if line_num == 1 and ('url' in line.lower() or 'link' in line.lower()):
                        continue
                        
                    # Split by comma and take the last part as URL (assuming format: name,url)
                    parts = line.split(',')
                    if len(parts) > 1:
                        url = parts[-1].strip()
                    else:
                        url = line
                else:
                    url = line

                if url:
                    if not (url.startswith('http://') or url.startswith('https://')):
                        # Only raise error if it's not a CSV header we missed
                        if not (is_csv and line_num == 1):
                            raise click.ClickException(f"Invalid URL at line {line_num} in {file_path}: {url}")
                        continue
                    urls.append(url)
        return urls
    except UnicodeDecodeError:
        raise click.ClickException(f"File encoding error in {file_path}. Please use UTF-8 encoding.")
    except Exception as e:
        raise click.ClickException(f"Failed to load URLs from {file_path}: {e}")


async def extract_product_data(url: str, wrapper: Crawl4AIWrapper, adw_id: str, console: Console, gbh_location: Optional[str] = None) -> Optional[ProductData]:
    """Extract product data from a single URL.

    Args:
        url: Product URL to scrape
        wrapper: Crawl4AI wrapper instance
        adw_id: ADW ID for tracking
        console: Rich console for output
        gbh_location: Optional location name for GlobalHouse price by location
    """
    # import sys  # Ensure sys is available for error logging
    try:
        # Determine specific wait conditions per retailer
        css_selector = None
        wait_for = None

        if 'boonthavorn.com' in url:
            # Boonthavorn is React SPA - wait for actual price content (not shimmer placeholders)
            # Must wait for price element AND no shimmer loading indicators
            wait_for = "() => { const hasPrice = document.body.innerText.includes('฿') || document.body.innerText.includes('บาท'); const noShimmer = !document.querySelector('[class*=\"shimmer\"]') || document.querySelectorAll('[class*=\"shimmer\"]').length < 3; return hasPrice && noShimmer; }"
        elif 'homepro.co.th' in url:
            # HomePro needs to wait for price element to load (React SPA)
            wait_for = "() => document.querySelector('[class*=\"price\"]') !== null && document.body.innerText.includes('฿')"
        elif 'globalhouse.co.th' in url and gbh_location:
            # GlobalHouse with location selection - wait for location selection JavaScript to complete
            # Check for SUCCESS or ERROR message in page content
            wait_for = "() => document.body.innerText.includes('SUCCESS: Location selection complete') || document.body.innerText.includes('[LOCATION ERROR]')"
        elif 'makro.pro' in url:
            # MakroExtractor reads the client-rendered slab (step-price) tiers
            # from the DOM. Those tier elements (data-test-id="unit_tier_N") mount
            # late; the wrapper's generic scroll/tab JS otherwise captures HTML
            # before they appear, yielding empty step_prices. Wait for the first
            # tier to mount (slab products settle in ~2s), capped by page time so
            # no-slab products (~70%, tier never appears) proceed instead of
            # hanging to page_timeout.
            wait_for = "() => document.querySelector('[data-test-id=\"unit_tier_0\"]') !== null || performance.now() > 7000"
        else:
            # Default wait condition for other retailers (Thai Watsadu, DoHome, MegaHome, Global House without location)
            # Ensure page has meaningful content before scraping
            wait_for = "() => document.readyState === 'complete' && document.body && document.body.innerText.length > 500"

        # Scrape the URL (with optional GlobalHouse location selection)
        result = await wrapper.scrape_url(url, css_selector=css_selector, wait_for=wait_for, gbh_location=gbh_location)
        
        # HomePro: Retry once if we get empty page (execution context destroyed)
        if 'homepro.co.th' in url and result.success and result.html and len(result.html) < 100:
            print(f"[SCRAPER] ⚠️ HomePro: Empty page detected ({len(result.html)} chars), retrying once...", flush=True, file=sys.stderr)
            print(f"[SCRAPER] This happens when page navigation destroys execution context during wait_for", flush=True, file=sys.stderr)
            
            import asyncio
            # Wait a bit before retry
            await asyncio.sleep(5)
            
            # Retry with longer delay
            print(f"[SCRAPER] HomePro RETRY: Adding 25s initial delay...", flush=True, file=sys.stderr)
            await asyncio.sleep(25)
            print(f"[SCRAPER] HomePro RETRY: Now attempting scrape...", flush=True, file=sys.stderr)

            result = await wrapper.scrape_url(url, css_selector=css_selector, wait_for=wait_for, gbh_location=gbh_location)
            
            if result.success and result.html and len(result.html) > 100:
                print(f"[SCRAPER] ✓ Retry succeeded! HTML length: {len(result.html)}", flush=True, file=sys.stderr)
            else:
                print(f"[SCRAPER] ✗ Retry still failed. HTML length: {len(result.html) if result.html else 0}", flush=True, file=sys.stderr)
        
        # HomePro: Validate captured page state
        if 'homepro.co.th' in url and result.success and result.html:
            import re
            # Check body tag to verify we captured the right state
            body_match = re.search(r'<body[^>]*>', result.html)
            if body_match:
                body_tag = body_match.group(0)
                print(f"[SCRAPER] HomePro: Captured page body tag: {body_tag}", flush=True, file=sys.stderr)
                if 'home-page' in body_tag:
                    print(f"[SCRAPER] ⚠️ WARNING: Page still in home-page state! This should not happen.", flush=True, file=sys.stderr)
                elif 'product-page' in body_tag or 'pdp-' in body_tag:
                    print(f"[SCRAPER] ✓ Page correctly in product-page state", flush=True, file=sys.stderr)
                else:
                    print(f"[SCRAPER] ⚠️ UNKNOWN page state (not home-page or product-page)", flush=True, file=sys.stderr)
        
        # DISABLED: Post-load wait and cleanup - they don't help if HTML is already empty
        # # HomePro: Give extra time for React to fully render (especially in slow networks)
        # if 'homepro.co.th' in url and result.success:
        #     import asyncio
        #     print(f"[SCRAPER] HomePro: Waiting 20 extra seconds for React rendering...", flush=True, file=sys.stderr)
        #     await asyncio.sleep(20)
        #     print(f"[SCRAPER] HomePro: Extra wait complete", flush=True, file=sys.stderr)
        #     
        #     # Force browser cleanup to prevent state pollution for next HomePro scrape
        #     print(f"[SCRAPER] HomePro: Cleaning browser state for next scrape...", flush=True, file=sys.stderr)
        #     await wrapper._cleanup_browser()
        #     print(f"[SCRAPER] HomePro: Browser cleanup complete", flush=True, file=sys.stderr)

        print(f"[SCRAPER] Scrape result - Success: {result.success}", flush=True, file=sys.stderr)
        if result.success:
            print(f"[SCRAPER] HTML length: {len(result.html) if result.html else 0}", flush=True, file=sys.stderr)
            print(f"[SCRAPER] Content length: {len(result.content) if result.content else 0}", flush=True, file=sys.stderr)
            
            # HomePro: Detect if page didn't actually load despite wait_for passing
            if 'homepro.co.th' in url and result.html and len(result.html) < 500:
                print(f"[SCRAPER] ⚠️  EMPTY PAGE: HomePro HTML only {len(result.html)} chars - wait_for passed but page is blank!", flush=True, file=sys.stderr)
                print(f"[SCRAPER] This suggests wait_for has a bug or page is being cleared", flush=True, file=sys.stderr)
                # Don't mark as failed yet - let extraction handle it
        
        if not result.success:
            print_status_panel(console, f"Failed to scrape: {result.error_message}", adw_id, "extraction", "error", url)
            return None

        html_length = len(result.html) if result.html else 0
        content_length = len(result.content) if result.content else 0
        print_status_panel(console, f"Extracted HTML: {html_length} chars, Content: {content_length} chars", adw_id, "extraction", "success", url)
        
        # DEBUG: For HomePro, check if key elements are present
        if 'homepro.co.th' in url and result.html:
            has_json_ld = 'application/ld+json' in result.html
            has_price_element = 'price' in result.html.lower()
            has_product_name = '<h1' in result.html
            print(f"[HomePro DEBUG] HTML analysis:")
            print(f"  Has JSON-LD: {has_json_ld}")
            print(f"  Has price element: {has_price_element}")
            print(f"  Has H1 tag: {has_product_name}")

        # Get appropriate extractor for the URL
        # import sys
        extractor = get_extractor(url)
        print(f"[SCRAPER] Using extractor: {type(extractor).__name__}", flush=True, file=sys.stderr)
        
        # Extract product data
        print(f"[SCRAPER] Starting extraction...", flush=True, file=sys.stderr)
        product = extractor.extract_from_html(result.html or result.content, url)
        print(f"[SCRAPER] Extraction complete. Product returned: {product is not None}", flush=True, file=sys.stderr)
        
        if product:
            print(f"[SCRAPER] ✓ Product extracted successfully:", flush=True, file=sys.stderr)
            print(f"    Name: {product.name}", flush=True, file=sys.stderr)
            print(f"    Retailer: {product.retailer}", flush=True, file=sys.stderr)
            print(f"    SKU: {product.sku}", flush=True, file=sys.stderr)
            print(f"    Price: {product.current_price}", flush=True, file=sys.stderr)
            print(f"    Brand: {product.brand}", flush=True, file=sys.stderr)
        else:
            print(f"[SCRAPER] ✗ Extraction returned None!", flush=True, file=sys.stderr)
        
        # Generic Retry: If price extraction failed, retry for ALL retailers
        extraction_attempt = 1
        max_extraction_retries = 3
        
        while extraction_attempt <= max_extraction_retries:
            # Check if price is missing (critical field)
            if not product or not product.current_price:
                print(f"[SCRAPER DEBUG] Extraction completed for: {url}", flush=True, file=sys.stderr)
                print(f"  Name: {product.name if product else None}", flush=True, file=sys.stderr)
                print(f"  Price: {product.current_price if product else None}", flush=True, file=sys.stderr)
                
                if extraction_attempt < max_extraction_retries:
                    print(f"[SCRAPER] ⚠️ Price not found (attempt {extraction_attempt}/{max_extraction_retries}), retrying...", flush=True, file=sys.stderr)
                    
                    import asyncio
                    # Wait before retry (5 seconds each attempt)
                    retry_delay = 5
                    print(f"[SCRAPER] RETRY: Waiting {retry_delay}s before re-scraping...", flush=True, file=sys.stderr)
                    await asyncio.sleep(retry_delay)
                    
                    # Re-scrape the page
                    print(f"[SCRAPER] RETRY: Re-scraping page (attempt {extraction_attempt + 1})...", flush=True, file=sys.stderr)
                    result = await wrapper.scrape_url(url, css_selector=css_selector, wait_for=wait_for, gbh_location=gbh_location)
                    
                    if result.success and result.html:
                        print(f"[SCRAPER] RETRY: Scrape successful, HTML length: {len(result.html)}", flush=True, file=sys.stderr)
                        
                        # Re-extract
                        print(f"[SCRAPER] RETRY: Re-extracting data...", flush=True, file=sys.stderr)
                        product = extractor.extract_from_html(result.html or result.content, url)
                        
                        if product and product.current_price:
                            print(f"[SCRAPER] ✓ RETRY SUCCEEDED! Got Price={product.current_price}", flush=True, file=sys.stderr)
                            break  # Success! Exit retry loop
                        else:
                            print(f"[SCRAPER] ✗ RETRY: Price still not found", flush=True, file=sys.stderr)
                    else:
                        print(f"[SCRAPER] ✗ RETRY: Scrape failed: {result.error_message if result else 'No result'}", flush=True, file=sys.stderr)
                    
                    extraction_attempt += 1
                else:
                    # Final attempt failed
                    print(f"[SCRAPER] ✗✗✗ All {max_extraction_retries} extraction attempts failed!", flush=True, file=sys.stderr)
                    break
            else:
                # Extraction succeeded, exit retry loop
                break
        
        # LLM Fallback Logic - TEMPORARILY DISABLED due to LLMConfig ForwardRef issue in crawl4ai
        # The primary extraction with JSON-LD and Quick Info parsing should be sufficient
        # TODO: Re-enable once LLMConfig import issue is resolved
        # if not product or not product.name or not product.current_price:
        #     api_key = "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"
        #     if api_key:
        #         print_status_panel(console, "Primary extraction incomplete. Attempting LLM fallback (OpenRouter)...", adw_id, "extraction", "warning", url)
        #         # ... (LLM fallback code commented out)

        if product:
            print_status_panel(console, f"Successfully extracted: {product.name[:50]}...", adw_id, "extraction", "success", url)
            print(f"[SCRAPER] Returning product object", flush=True, file=sys.stderr)
            return product
        else:
            # import sys
            print(f"[SCRAPER] ✗✗✗ EXTRACTION FAILED - Returning None for {url}", flush=True, file=sys.stderr)
            print_status_panel(console, "Failed to extract product data", adw_id, "extraction", "error", url)
            return None

    except Exception as e:
        print_status_panel(console, f"Extraction error: {str(e)}", adw_id, "extraction", "error", url)
        return None


def generate_summary_stats(products: List[ProductData]) -> Dict[str, Any]:
    """Generate summary statistics from extracted products."""
    total = len(products)
    if total == 0:
        return {"total_products": 0}

    # Pricing statistics
    products_with_current_price = [p for p in products if p.current_price is not None]
    products_with_original_price = [p for p in products if p.original_price is not None]
    products_with_discount = [p for p in products if p.has_discount]

    price_stats = {}
    if products_with_current_price:
        current_prices = [p.current_price for p in products_with_current_price]
        price_stats = {
            "min_price": min(current_prices),
            "max_price": max(current_prices),
            "avg_price": sum(current_prices) / len(current_prices),
            "products_with_pricing": len(products_with_current_price),
        }

    discount_stats = {}
    if products_with_discount:
        discount_amounts = [p.discount_amount for p in products_with_discount]
        discount_percents = [p.discount_percent for p in products_with_discount]
        discount_stats = {
            "products_with_discount": len(products_with_discount),
            "min_discount_amount": min(discount_amounts),
            "max_discount_amount": max(discount_amounts),
            "avg_discount_amount": sum(discount_amounts) / len(discount_amounts),
            "min_discount_percent": min(discount_percents),
            "max_discount_percent": max(discount_percents),
            "avg_discount_percent": sum(discount_percents) / len(discount_percents),
        }

    # Field completeness
    field_stats = {}
    fields = ['name', 'brand', 'model', 'sku', 'category', 'volume', 'dimensions', 'material', 'color', 'description']
    for field in fields:
        count = sum(1 for p in products if getattr(p, field) is not None and getattr(p, field) != '')
        field_stats[field] = count

    # Retailer distribution
    retailers = {}
    for product in products:
        retailer = product.retailer or "Unknown"
        retailers[retailer] = retailers.get(retailer, 0) + 1

    return {
        "total_products": total,
        "price_statistics": price_stats,
        "discount_statistics": discount_stats,
        "field_completeness": field_stats,
        "retailer_distribution": retailers,
        "processing_time": datetime.now().isoformat(),
    }


@click.command()
@click.option(
    "--url",
    help="Single product URL to scrape"
)
@click.option(
    "--urls-file",
    type=click.Path(exists=True),
    help="File containing list of product URLs to scrape (one per line)"
)
@click.option(
    "--output-file",
    default="products.json",
    help="Output file path for scraped product data"
)
@click.option(
    "--output-folder",
    type=click.Path(),
    help="Base output folder for organized results (creates date/job-ID subdirectories)"
)
@click.option(
    "--organization",
    type=click.Choice(["date", "job-id"]),
    default="date",
    help="How to organize output subdirectories when using --output-folder"
)
@click.option(
    "--adw-id",
    help="ADW ID for tracking (auto-generated if not provided)"
)
@click.option(
    "--max-concurrent",
    type=int,
    default=int(os.getenv('SCRAPER_MAX_CONCURRENT', '3')),
    help="Maximum concurrent requests (auto-reduced to 1 for HomePro)"
)
@click.option(
    "--delay",
    type=float,
    default=1.0,
    help="Delay between requests in seconds"
)
@click.option(
    "--timeout",
    type=int,
    default=30,
    help="Request timeout in seconds"
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Run browser in headless mode"
)
@click.option(
    "--verbose/--no-verbose",
    default=False,
    help="Enable verbose output"
)
@click.option(
    "--retry-attempts",
    type=int,
    default=3,
    help="Number of retry attempts for failed requests"
)
@click.option(
    "--retry-delay",
    type=float,
    default=2.0,
    help="Delay between retries in seconds"
)
@click.option(
    "--use-browser/--no-browser",
    default=True,
    help="Use browser for scraping (handles JavaScript)"
)
@click.option(
    "--test",
    is_flag=True,
    help="Run in test mode with minimal output"
)
@click.option(
    "--gbh-location",
    type=str,
    default=None,
    help="GlobalHouse location name for price by location (e.g., 'นครปฐม', 'ขอนแก่น')"
)
@click.option(
    "--output-db",
    is_flag=True,
    help="Upsert scraped products straight into the products table (cfw schema). "
         "Runs in addition to the JSON output. Requires DB_* creds in backend/.env."
)
def main(
    url: Optional[str],
    urls_file: Optional[str],
    output_file: str,
    output_folder: Optional[str],
    organization: str,
    adw_id: Optional[str],
    max_concurrent: int,
    delay: float,
    timeout: int,
    headless: bool,
    verbose: bool,
    retry_attempts: int,
    retry_delay: float,
    use_browser: bool,
    test: bool,
    gbh_location: Optional[str],
    output_db: bool,
):
    """E-commerce product data scraper."""

    console = Console()

    # Generate ADW ID if not provided
    if not adw_id:
        import uuid
        adw_id = str(uuid.uuid4())[:8]

    # Validate input arguments
    input_sources = [url, urls_file]
    active_sources = [source for source in input_sources if source is not None]

    if len(active_sources) == 0:
        raise click.ClickException("Either --url or --urls-file must be provided")

    if len(active_sources) > 1:
        raise click.ClickException("Cannot specify both --url and --urls-file")

    # Determine URLs to scrape
    if url:
        urls = [url]
        source_description = f"single URL: {url}"
    else:  # urls_file
        urls = load_urls_from_file(urls_file)
        source_description = f"URLs file: {urls_file}"

    if not urls:
        raise click.ClickException("No URLs found to scrape")

    # DEBUG: Log working directory and paths
    import sys as sys_module
    print(f"\n[DEBUG] Working directory: {os.getcwd()}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] Script location: {os.path.abspath(__file__)}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] Output file parameter: {output_file}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] Output folder parameter: {output_folder}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] WRITE_AGENTS_FOLDER: {WRITE_AGENTS_FOLDER}\n", flush=True, file=sys_module.stderr)

    # Handle output directory structure
    if output_folder:
        # Create organized output directory structure
        output_dir = create_output_directory_structure(output_folder, adw_id, organization)
        output_file_full_path = os.path.join(output_dir, output_file)
        base_output_folder = output_dir
    elif WRITE_AGENTS_FOLDER:
        # Use legacy ADW structure (only if enabled via env)
        output_dir = f"./agents/{adw_id}/ecommerce_scraper"
        os.makedirs(output_dir, exist_ok=True)
        output_file_full_path = os.path.join(output_dir, output_file)
        base_output_folder = output_dir
    else:
        # No folder writing - use current directory
        output_dir = "."
        output_file_full_path = output_file
        base_output_folder = "."

    # DEBUG: Show resolved paths
    print(f"[DEBUG] Resolved output_dir: {output_dir}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] Resolved output_file_full_path: {output_file_full_path}", flush=True, file=sys_module.stderr)
    print(f"[DEBUG] Resolved base_output_folder: {base_output_folder}\n", flush=True, file=sys_module.stderr)

    # Check if any URLs are HomePro and reduce max_concurrent automatically
    homepro_urls = [url for url in urls if 'homepro.co.th' in url.lower()]
    if homepro_urls and max_concurrent > 1:
        original_max_concurrent = max_concurrent
        max_concurrent = 1  # Force sequential scraping for HomePro stability
        # import sys
        print(f"\n[HOMEPRO AUTO-ADJUST] Detected {len(homepro_urls)} HomePro URLs out of {len(urls)} total", flush=True, file=sys.stderr)
        print(f"[HOMEPRO AUTO-ADJUST] Reducing max_concurrent: {original_max_concurrent} → 1", flush=True, file=sys.stderr)
        print(f"[HOMEPRO AUTO-ADJUST] HomePro React pages require sequential scraping for stability\n", flush=True, file=sys.stderr)

    # Create scraping configuration
    config = create_simple_config(
        max_concurrent=max_concurrent,
        delay_between_requests=delay,
        timeout=timeout,
        headless=headless,
        verbose=verbose,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        use_browser=use_browser,
    )

    # Display configuration
    config_table = Table(show_header=False, box=None, padding=(0, 1))
    config_table.add_column(style="bold cyan")
    config_table.add_column()

    config_table.add_row("ADW ID", adw_id)
    config_table.add_row("Products to scrape", str(len(urls)))
    config_table.add_row("Input source", source_description)
    config_table.add_row("Output file", output_file_full_path)

    if output_folder:
        config_table.add_row("Base output folder", output_folder)
        config_table.add_row("Organization", organization)

    config_table.add_row("Max concurrent", str(max_concurrent))
    config_table.add_row("Delay (seconds)", str(delay))
    config_table.add_row("Timeout (seconds)", str(timeout))
    config_table.add_row("Use browser", str(use_browser))
    config_table.add_row("Headless", str(headless))

    try:
        console.print(
            Panel(
                config_table,
                title=f"[bold blue]🛍️  E-commerce Product Scraper Configuration[/bold blue]",
                border_style="blue",
            )
        )
    except Exception as e:
        # Fallback for Windows terminal rendering issues
        print(f"E-commerce Product Scraper Configuration:", flush=True)
        print(f"  ADW ID: {adw_id}", flush=True)
        print(f"  Products to scrape: {len(urls)}", flush=True)
        print(f"  Input source: {source_description}", flush=True)
        print(f"  Output file: {output_file_full_path}", flush=True)
        if output_folder:
            print(f"  Base output folder: {output_folder}", flush=True)
        print(f"  Max concurrent: {max_concurrent}", flush=True)
        print(f"  Timeout (seconds): {timeout}", flush=True)
    
    try:
        console.print()
    except:
        pass

    # Prepare for scraping
    products = []
    summary_stats = {}
    error_message = None

    try:
        # Initialize the wrapper
        print_status_panel(console, "Initializing crawl4ai wrapper", adw_id, "init")

        wrapper = Crawl4AIWrapper(config)

        async def run_scraping():
            """Run the product scraping process."""
            async with wrapper:
                if len(urls) == 1:
                    # Single product scraping
                    print_status_panel(console, f"Scraping {urls[0]}", adw_id, "scraping", urls[0])
                    product = await extract_product_data(urls[0], wrapper, adw_id, console, gbh_location)
                    return [product] if product else []
                else:
                    # Batch scraping with progress indicator
                    console.print(f"[bold cyan]Scraping {len(urls)} products...[/bold cyan]")
                    console.print()

                    with Progress() as progress:
                        task_id = progress.add_task("Scraping products...", total=len(urls))

                        products = []
                        semaphore = asyncio.Semaphore(max_concurrent)

                        async def scrape_with_semaphore(url: str) -> Optional[ProductData]:
                            async with semaphore:
                                try:
                                    product = await extract_product_data(url, wrapper, adw_id, console, gbh_location)
                                    # Add delay between requests
                                    if delay > 0:
                                        await asyncio.sleep(delay)
                                    return product
                                finally:
                                    # Update progress regardless of success/failure
                                    progress.advance(task_id)

                        # Process all URLs concurrently
                        tasks = [scrape_with_semaphore(url) for url in urls]
                        
                        # Use as_completed to process results as they finish
                        for future in asyncio.as_completed(tasks):
                            try:
                                result = await future
                                if result:
                                    products.append(result)
                                    print(f"[DEBUG] Added product to list. Total products: {len(products)}", flush=True)
                                    print(f"[DEBUG] Product retailer: {result.retailer}", flush=True)
                                    
                                    # Incremental save - separate files per retailer
                                    try:
                                        # Group products by retailer
                                        from collections import defaultdict
                                        products_by_retailer = defaultdict(list)
                                        for p in products:
                                            retailer_name = p.retailer.lower().replace(' ', '_') if p.retailer else 'unknown'
                                            products_by_retailer[retailer_name].append(p.to_dict())
                                        
                                        # Save each retailer to a separate file
                                        output_dir = os.path.dirname(output_file_full_path)
                                        os.makedirs(output_dir, exist_ok=True)
                                        
                                        for retailer_name, retailer_products in products_by_retailer.items():
                                            retailer_file = os.path.join(output_dir, f"{retailer_name}.json")
                                            print(f"[DEBUG] Writing {len(retailer_products)} products to: {retailer_file}", flush=True)
                                            with open(retailer_file, 'w', encoding='utf-8') as f:
                                                json.dump(retailer_products, f, ensure_ascii=False, indent=2)
                                            print(f"[DEBUG] Successfully wrote file: {retailer_file}", flush=True)
                                    except Exception as e:
                                        print(f"[ERROR] Failed to save incremental results: {e}", flush=True)
                                        import traceback
                                        traceback.print_exc()
                                        console.print(f"[yellow]Warning: Failed to save incremental results: {e}[/yellow]")
                                else:
                                    print(f"[DEBUG] Result was None - extraction failed for URL", flush=True)
                                        
                                progress.advance(task_id)
                            except Exception as e:
                                console.print(f"[red]Error in scraping task: {str(e)}[/red]")
                                progress.advance(task_id)

                    return products

        # Run the scraping
        print_status_panel(console, "Starting product scraping process", adw_id, "scraping")
        products = asyncio.run(run_scraping())

        print_status_panel(console, "Completed product scraping process", adw_id, "scraping", "success")

        # Generate summary statistics
        summary_stats = generate_summary_stats(products)

        # Display results summary
        console.print()
        console.print(Rule("[bold yellow]Product Scraping Results Summary[/bold yellow]"))
        console.print()

        summary_table = Table()
        summary_table.add_column("Metric", style="bold cyan")
        summary_table.add_column("Value", style="bold")

        summary_table.add_row("Total Products", str(summary_stats["total_products"]))
        summary_table.add_row("Successfully Extracted", str(len(products)))

        if summary_stats.get("price_statistics"):
            price_stats = summary_stats["price_statistics"]
            summary_table.add_row("Products with Pricing", str(price_stats["products_with_pricing"]))
            if price_stats["products_with_pricing"] > 0:
                summary_table.add_row("Price Range", f"{price_stats['min_price']:.2f} - {price_stats['max_price']:.2f}")
                summary_table.add_row("Average Price", f"{price_stats['avg_price']:.2f}")

        if summary_stats.get("discount_statistics"):
            discount_stats = summary_stats["discount_statistics"]
            summary_table.add_row("Products with Discount", str(discount_stats["products_with_discount"]))
            if discount_stats["products_with_discount"] > 0:
                summary_table.add_row("Avg Discount", f"{discount_stats['avg_discount_percent']:.1f}%")

        console.print(summary_table)

        # Group products by retailer
        from collections import defaultdict
        products_by_retailer = defaultdict(list)
        for product in products:
            retailer_name = product.retailer.lower().replace(' ', '_') if product.retailer else 'unknown'
            products_by_retailer[retailer_name].append(product.to_dict())

        # Save results - separate file per retailer
        output_dir = os.path.dirname(output_file_full_path)
        # Safeguard: if output_dir is empty, use current directory
        if not output_dir:
            output_dir = "."
        os.makedirs(output_dir, exist_ok=True)
        
        saved_files = []
        for retailer_name, retailer_products in products_by_retailer.items():
            retailer_file = os.path.join(output_dir, f"{retailer_name}.json")
            print_status_panel(console, f"Saving {len(retailer_products)} {retailer_name} products to {retailer_file}", adw_id, "output")
            with open(retailer_file, 'w', encoding='utf-8') as f:
                json.dump(retailer_products, f, ensure_ascii=False, indent=2)
            saved_files.append((retailer_name, retailer_file, len(retailer_products)))
            # DEBUG: Confirm file was written
            full_path = os.path.abspath(retailer_file)
            file_exists = os.path.exists(retailer_file)
            file_size = os.path.getsize(retailer_file) if file_exists else 0
            print(f"[DEBUG] Saved {retailer_name}: {full_path} (exists={file_exists}, size={file_size})", flush=True)
        
        print_status_panel(console, f"Saved {len(saved_files)} retailer files to {output_dir}", adw_id, "output", "success")
        
        print_status_panel(console, f"Results saved to {output_file_full_path}", adw_id, "output", "success")

        # Display final output info
        console.print()
        if output_folder:
            # Display organized output structure
            output_info = (
                f"[bold cyan]Products Extracted:[/bold cyan] {len(products)}\n"
                f"[bold cyan]Base Output Folder:[/bold cyan] {output_folder}\n"
                f"[bold cyan]Organization:[/bold cyan] {organization}\n"
                f"[bold cyan]Job Directory:[/bold cyan] {base_output_folder}\n"
                f"[bold cyan]Results File:[/bold cyan] {output_file_full_path}\n"
                f"[bold cyan]Success Rate:[/bold cyan] {(len(products)/len(urls)*100):.1f}%"
            )
        else:
            # Display legacy structure
            output_info = (
                f"[bold cyan]Products Extracted:[/bold cyan] {len(products)}\n"
                f"[bold cyan]Output File:[/bold cyan] {output_file_full_path}\n"
                f"[bold cyan]ADW Directory:[/bold cyan] {output_dir}\n"
                f"[bold cyan]Success Rate:[/bold cyan] {(len(products)/len(urls)*100):.1f}%"
            )

        console.print(
            Panel(
                output_info,
                title="[bold green]✅ E-commerce Product Scraping Complete[/bold green]",
                border_style="green",
            )
        )

        # Optional: upsert straight into the products table (cfw schema).
        # Runs in addition to the JSON output; failures here do not lose the JSON.
        if output_db and products:
            print_status_panel(console, f"Writing {len(products)} products to DB", adw_id, "output-db")
            try:
                from adw_modules.db_writer import upsert_products
                db_summary = upsert_products(products)
                print_status_panel(
                    console,
                    f"DB upsert: {db_summary['inserted']} inserted, "
                    f"{db_summary['updated']} updated, "
                    f"{db_summary['skipped']} skipped, "
                    f"{db_summary['errors']} errors",
                    adw_id, "output-db",
                    "success" if db_summary["errors"] == 0 else "warning",
                )
            except Exception as e:
                print_status_panel(console, f"DB write failed: {e}", adw_id, "output-db", "error")
                print(f"[SCRAPER] DB write failed: {e}", flush=True, file=sys.stderr)

        # Exit with appropriate code
        # import sys
        print(f"\n[DEBUG] Final product count: {len(products)} out of {len(urls)} URLs", flush=True, file=sys.stderr)
        if len(products) == 0:
            print(f"[DEBUG] Exiting with code 1 (all extractions failed)", flush=True, file=sys.stderr)
            sys.exit(1)  # All extractions failed
        elif len(products) < len(urls):
            print(f"[DEBUG] Exiting with code 2 (some extractions failed)", flush=True, file=sys.stderr)
            sys.exit(2)  # Some extractions failed
        else:
            print(f"[DEBUG] Exiting with code 0 (all extractions succeeded)", flush=True, file=sys.stderr)
            sys.exit(0)  # All extractions succeeded

    except KeyboardInterrupt:
        print_status_panel(console, "Scraping interrupted by user", adw_id, "scraping", "warning")
        sys.exit(130)

    except Exception as e:
        error_message = str(e)
        print_status_panel(console, f"Scraping failed: {error_message}", adw_id, "scraping", "error")
        sys.exit(1)


if __name__ == "__main__":
    main()
