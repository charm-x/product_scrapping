#!/usr/bin/env python3
"""
Bol.com Product Tracker - Easy to Use Version
Track specific products and their ranking positions
"""

import requests
from bs4 import BeautifulSoup
import re
import sqlite3
from datetime import datetime
import time
import random
from urllib.parse import quote
from datetime import timedelta

# Optional Selenium fallback (used only if installed)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import WebDriverException
    HAS_SELENIUM = True
except Exception:
    HAS_SELENIUM = False

class ProductTracker:
    def __init__(self):
        self.session = requests.Session()
        self.init_db()
        
    # --- Utility ---
    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _parse_dt(self, dt_str: str) -> datetime:
        # Accept common formats used in this file
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt_str, fmt)
            except Exception:
                continue
        # Fallback to now if unparsable
        return datetime.now()
    
    def init_db(self):
        """Initialize the database"""
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        
        # Create tracking history table
        c.execute("""
            CREATE TABLE IF NOT EXISTS product_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT,
                product_id TEXT,
                product_name TEXT,
                position INTEGER,
                page_product INTEGER,
                date TEXT
            )
        """)

        # Create tracked products table (for scheduler + UI)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tracked_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product_url TEXT NOT NULL,
                product_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                stop_after_days INTEGER,
                daily_scheduler INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Check if history table has the right columns
        c.execute("PRAGMA table_info(product_tracking)")
        columns = [column[1] for column in c.fetchall()]
        
        if 'product_name' not in columns:
            # Add the product_name column if it doesn't exist
            c.execute("ALTER TABLE product_tracking ADD COLUMN product_name TEXT")
        
        if 'page_product' not in columns:
            # Add the page_product column if it doesn't exist
            c.execute("ALTER TABLE product_tracking ADD COLUMN page_product INTEGER")
        
        # Check if tracked_products table has the daily_scheduler column
        c.execute("PRAGMA table_info(tracked_products)")
        tracked_columns = [column[1] for column in c.fetchall()]
        
        if 'daily_scheduler' not in tracked_columns:
            # Add the daily_scheduler column if it doesn't exist
            c.execute("ALTER TABLE tracked_products ADD COLUMN daily_scheduler INTEGER NOT NULL DEFAULT 0")
        
        conn.commit()
        conn.close()

    # --- Tracked products CRUD helpers ---
    def add_tracked_product(self, keyword: str, product_url: str, stop_after_days: int | None = None) -> dict:
        product_id = self.get_product_id(product_url)
        
        # Extract product name from URL as primary method
        product_name = self._extract_name_from_url(product_url)
        print(f"🔍 Extracted name from URL: {product_name}")
        
        # Try to get better name from HTML scraping as secondary method
        try:
            print(f"🔍 Attempting HTML extraction for {product_id}...")
            result = self.find_product_ranking(keyword, product_id, max_pages=1)  # Only check first page for speed
            if result.get("product") and result["product"].get("name") and result["product"]["name"] != "Unknown Product":
                product_name = result["product"]["name"]
                print(f"✅ Found better name from HTML: {product_name}")
            else:
                print(f"ℹ️ Using URL-extracted name: {product_name}")
        except Exception as e:
            print(f"⚠️ HTML extraction failed, using URL name: {e}")
        
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO tracked_products (keyword, product_id, product_url, product_name, active, created_at, stop_after_days)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (keyword, product_id, product_url, product_name, self._now_str(), stop_after_days),
        )
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return {"id": new_id, "keyword": keyword, "product_id": product_id, "product_url": product_url}

    def remove_tracked_product(self, tracked_id: int) -> None:
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        c.execute("UPDATE tracked_products SET active = 0 WHERE id = ?", (tracked_id,))
        conn.commit()
        conn.close()
    
    def toggle_daily_scheduler(self, tracked_id: int) -> bool:
        """Toggle daily scheduler for a specific product. Returns new state."""
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        
        # Get current state
        c.execute("SELECT daily_scheduler FROM tracked_products WHERE id = ?", (tracked_id,))
        current_state = c.fetchone()
        if not current_state:
            conn.close()
            raise ValueError("Product not found")
        
        # Toggle the state
        new_state = 1 if current_state[0] == 0 else 0
        c.execute("UPDATE tracked_products SET daily_scheduler = ? WHERE id = ?", (new_state, tracked_id))
        conn.commit()
        conn.close()
        
        return bool(new_state)

    def list_tracked_products(self, include_inactive: bool = False) -> list[tuple]:
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        if include_inactive:
            c.execute("SELECT id, keyword, product_id, product_url, product_name, active, created_at, stop_after_days, daily_scheduler FROM tracked_products ORDER BY id DESC")
        else:
            c.execute("SELECT id, keyword, product_id, product_url, product_name, active, created_at, stop_after_days, daily_scheduler FROM tracked_products WHERE active = 1 ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()
        return rows
    
    def get_tracked_product(self, tracked_id: int):
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        c.execute("SELECT id, keyword, product_id, product_url, product_name, active, created_at, stop_after_days, daily_scheduler FROM tracked_products WHERE id = ?", (tracked_id,))
        row = c.fetchone()
        conn.close()
        return row
    
    def get_product_id(self, product_url: str) -> str:
        """Extract product ID from URL"""
        match = re.search(r"/(\d{10,})/", product_url)
        if match:
            return match.group(1)
        raise ValueError("Could not extract product ID from URL")
    
    def _extract_name_from_url(self, product_url: str) -> str:
        """Extract product name from URL structure"""
        try:
            # Bol.com URL structure: /p/product-name-slug/product-id/
            # Example: /p/lenor-geurbooster-voor-je-was-orchidee-en-amber-voordeelverpakking-6-x-235g/9300000170626119/
            
            # Find the product slug between /p/ and the product ID
            match = re.search(r'/p/([^/]+)/(\d{10,})/', product_url)
            if match:
                product_slug = match.group(1)
                # Convert slug to readable name
                name = product_slug.replace('-', ' ').title()
                # Clean up common patterns
                name = re.sub(r'\s+', ' ', name)  # Multiple spaces to single space
                name = name.strip()
                return name
            
            # Fallback: try to extract from any part of the URL
            url_parts = product_url.split('/')
            for part in url_parts:
                if part and not part.isdigit() and len(part) > 5:
                    # Skip common URL parts
                    if part not in ['nl', 'p', 'www.bol.com', 'https:', 'http:']:
                        name = part.replace('-', ' ').title()
                        name = re.sub(r'\s+', ' ', name)
                        return name.strip()
            
            return "Product from URL"
            
        except Exception as e:
            print(f"⚠️ Error extracting name from URL: {e}")
            return "Product from URL"
    
    def search_products_mobile(self, keyword: str, max_pages: int = 20, target_product_id: str = None) -> tuple[list[dict], dict]:
        """Search for products using mobile headers (bypasses 403 error)
        Returns: (products_list, target_info) where target_info contains page and position info if found
        """
        print(f"🔍 Searching for '{keyword}' using mobile headers...")
        
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        self.session.headers.update(mobile_headers)
        products = []
        target_info = {"found": False, "page": None, "position_on_page": None, "page_product": None}
        
        for page in range(1, max_pages + 1):
            try:
                encoded_keyword = quote(keyword)
                url = f"https://www.bol.com/nl/nl/s/?searchtext={encoded_keyword}&page={page}"
                
                print(f"  📄 Page {page}...")
                time.sleep(random.uniform(2, 4))
                
                response = self.session.get(url, timeout=30)
                
                if response.status_code == 200:
                    page_products = self._extract_products_from_html(response.text)
                    
                    # Add current page products to the main list first
                    products.extend(page_products)
                    
                    # Check if target product is on this page
                    if target_product_id:
                        for pos, product in enumerate(page_products, 1):
                            if product.get("id") == target_product_id:
                                target_info = {
                                    "found": True,
                                    "page": page,
                                    "position_on_page": pos,
                                    "page_product": len(products) - len(page_products) + pos  # Total products before this page + position on current page
                                }
                                print(f"  🎯 Target product found on page {page}, position {pos} on page (absolute position: {target_info['page_product']})")
                                break
                    print(f"  ✅ Found {len(page_products)} products")
                    
                    # Stop immediately if target product is found
                    if target_info["found"]:
                        break
                        
                else:
                    print(f"  ❌ HTTP {response.status_code}")
                    break
                    
            except Exception as e:
                print(f"  ❌ Error: {e}")
                break
        
        print(f"📊 Total products found: {len(products)}")
        return products, target_info
    
    def search_products_browser(self, keyword: str, max_pages: int = 50, target_product_id: str = None) -> tuple[list[dict], dict]:
        """Fallback using headless Chrome with mobile UA if HTTP requests are blocked.
        Returns: (products_list, target_info) where target_info contains page and position info if found
        """
        if not HAS_SELENIUM:
            print("  ⚠️ Selenium not installed. Run: pip install selenium")
            return [], {"found": False, "page": None, "position_on_page": None, "page_product": None}
        print(f"🔍 Searching for '{keyword}' using headless Chrome (mobile)...")
        products: list[dict] = []
        target_info = {"found": False, "page": None, "position_on_page": None, "page_product": None}
        try:
            options = ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-software-rasterizer")
            options.add_argument("--disable-features=VizDisplayCompositor")
            mobile_emulation = {
                "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 3.0},
            }
            options.add_experimental_option("mobileEmulation", mobile_emulation)
            
            with webdriver.Chrome(options=options) as driver:
                for page in range(1, max_pages + 1):
                    encoded_keyword = quote(keyword)
                    url = f"https://www.bol.com/nl/nl/s/?searchtext={encoded_keyword}&page={page}"
                    print(f"  📄 Page {page} (browser)...")
                    try:
                        driver.get(url)
                        time.sleep(random.uniform(2.0, 3.0))
                        # Try to accept cookie/consent if present
                        try:
                            buttons = driver.find_elements(By.TAG_NAME, "button")
                            for b in buttons:
                                txt = (b.text or "").strip().lower()
                                if txt in ("akkoord", "accept", "accepteer alles", "alles accepteren"):
                                    b.click()
                                    time.sleep(0.8)
                                    break
                        except Exception:
                            pass
                        # Progressive scroll to trigger lazy loaded results
                        for frac in (0.3, 0.6, 0.9):
                            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {frac});")
                            time.sleep(random.uniform(0.6, 1.0))
                        html = driver.page_source
                        page_products = self._extract_products_from_html(html)
                        
                        # Add current page products to the main list first
                        products.extend(page_products)
                        
                        # Check if target product is on this page
                        if target_product_id:
                            for pos, product in enumerate(page_products, 1):
                                if product.get("id") == target_product_id:
                                    target_info = {
                                        "found": True,
                                        "page": page,
                                        "position_on_page": pos,
                                        "page_product": len(products) - len(page_products) + pos  # Total products before this page + position on current page
                                    }
                                    print(f"  🎯 Target product found on page {page}, position {pos} on page (absolute position: {target_info['page_product']})")
                                    break
                        print(f"  ✅ Found {len(page_products)} products (total: {len(products)})")
                        
                        # Stop immediately if target product is found
                        if target_info["found"]:
                            break
                            
                        if len(page_products) == 0:
                            print(f"  ⚠️ No products found on page {page} (browser), stopping")
                            break
                    except WebDriverException as wde:
                        print(f"  ❌ WebDriver error: {wde}")
                        break
        except Exception as e:
            print(f"  ❌ Browser fallback failed: {e}")
            return products, target_info
        print(f"📊 Total products found (browser): {len(products)}")
        return products, target_info

    def _extract_products_from_html(self, html: str) -> list[dict]:
        """Extract product information from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        products = []
        seen_ids = set()  # Avoid duplicates
        
        # Look for product links with better name extraction
        for link in soup.find_all("a", href=True):
            href = link.get("href")
            if href and "/p/" in href:
                # Extract product ID
                match = re.search(r"/(\d{10,})/", href)
                if match:
                    product_id = match.group(1)
                    
                    # Skip if already seen
                    if product_id in seen_ids:
                        continue
                    
                    # Try multiple strategies to get product name
                    product_name = "Unknown Product"
                    
                    # Strategy 1: Look for h2 or h3 elements (most reliable)
                    title_elem = link.find("h2") or link.find("h3")
                    if title_elem and title_elem.get_text(strip=True):
                        product_name = title_elem.get_text(strip=True)[:100]
                    # Strategy 2: Look for title attribute
                    elif link.get("title"):
                        product_name = link.get("title").strip()[:100]
                    # Strategy 3: Look for any text content in the link
                    elif link.get_text(strip=True):
                        text = link.get_text(strip=True)
                        # Skip if it's just numbers or very short, or common non-product text
                        if (len(text) > 10 and not text.isdigit() and 
                            text not in ["Meer verkopers", "Bekijk product", "Bekijk details"]):
                            product_name = text[:100]
                    # Strategy 4: Look for aria-label attribute
                    elif link.get("aria-label"):
                        product_name = link.get("aria-label").strip()[:100]
                    # Strategy 5: Look for data attributes
                    elif link.get("data-testid") and "product" in link.get("data-testid", ""):
                        # Try to find text in parent or sibling elements
                        parent = link.parent
                        if parent:
                            text_elem = parent.find("h2") or parent.find("h3") or parent.find("span")
                            if text_elem and text_elem.get_text(strip=True):
                                product_name = text_elem.get_text(strip=True)[:100]
                    
                    products.append({
                        "id": product_id,
                        "name": product_name,
                        "url": href if href.startswith("http") else f"https://www.bol.com{href}"
                    })
                    seen_ids.add(product_id)
        
        # Also try to extract by data attributes commonly used on cards
        for elem in soup.find_all(attrs={"data-product-id": True}):
            pid = elem.get("data-product-id")
            if pid and re.fullmatch(r"\d{10,}", pid) and pid not in seen_ids:
                name = "Unknown Product"
                title_elem = elem.find("h2") or elem.find("h3") or elem.find("span")
                if title_elem and hasattr(title_elem, 'get_text'):
                    text = title_elem.get_text(strip=True)
                    if text and len(text) > 3:
                        name = text[:100]
                products.append({
                    "id": pid,
                    "name": name,
                    "url": f"https://www.bol.com/nl/nl/p/{pid}/"
                })
                seen_ids.add(pid)

        return products
    
    def find_product_ranking(self, keyword: str, target_product_id: str, max_pages: int = 50) -> dict:
        """Find the ranking position of a specific product with optimized search"""
        print(f"🎯 Looking for product {target_product_id} in '{keyword}' search results...")
        
        # Try mobile search first with early stopping
        products, target_info = self.search_products_mobile(keyword, max_pages, target_product_id)
        
        # If not found with mobile, try browser fallback with early stopping
        if not target_info["found"]:
            print("  🔄 Mobile search didn't find product, trying browser fallback...")
            browser_products, browser_target_info = self.search_products_browser(keyword, max_pages, target_product_id)
            if len(browser_products) > 0:
                products = browser_products
                target_info = browser_target_info
        
        # If product was found, return the results
        if target_info["found"]:
            # Find the product object for additional info
            target_product = None
            for product in products:
                if product["id"] == target_product_id:
                    target_product = product
                    break
            
            print(f"🎉 Found! Product is at position {target_info['page_product']} (page {target_info['page']}, position {target_info['position_on_page']} on page)")
            return {
                "position": target_info["position_on_page"],  # Position on the specific page
                "page_product": target_info["page_product"],  # Absolute position across all pages
                "page": target_info["page"],  # Which page it was found on
                "product": target_product,
                "total_found": len(products)
            }
        
        print(f"❌ Product not found in top {len(products)} results")
        return {
            "position": None,
            "page_product": None,
            "page": None,
            "product": None,
            "total_found": len(products)
        }
    
    def track_product(self, keyword: str, product_url: str):
        """Track a specific product's ranking"""
        try:
            product_id = self.get_product_id(product_url)
            print(f"🚀 Tracking Product Ranking")
            print("=" * 50)
            print(f"Keyword: {keyword}")
            print(f"Product URL: {product_url}")
            print(f"Product ID: {product_id}")
            print()
            
            # Find the ranking
            result = self.find_product_ranking(keyword, product_id)
            
            # Save to database
            self.save_to_db(keyword, product_id, result["product"]["name"] if result["product"] else "Unknown", result["position"], result.get("page_product"))
            
            # Show results
            print("\n📊 RESULTS:")
            print(f"   Ranking: {result['position'] if result['position'] else 'Not found'}")
            print(f"   Total products searched: {result['total_found']}")
            print(f"   Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            if result["product"]:
                print(f"   Product Name: {result['product']['name']}")
            
            return result
            
        except Exception as e:
            print(f"❌ Error: {e}")
            return None
    
    def save_to_db(self, keyword: str, product_id: str, product_name: str, position: int | None, page_product: int | None = None):
        """Save tracking data to database"""
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        c.execute("""
            INSERT INTO product_tracking (keyword, product_id, product_name, position, page_product, date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (keyword, product_id, product_name, position, page_product, self._now_str()))
        conn.commit()
        conn.close()
    
    def get_tracking_history(self, product_id: str = None):
        """Get tracking history from database"""
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        
        if product_id:
            c.execute("""
                SELECT * FROM product_tracking 
                WHERE product_id = ?
                ORDER BY date DESC
            """, (product_id,))
        else:
            c.execute("""
                SELECT * FROM product_tracking 
                ORDER BY date DESC
            """)
        
        results = c.fetchall()
        conn.close()
        return results

    # --- Scheduling helpers ---
    def run_scheduled_checks(self) -> list[dict]:
        """Run ranking checks for all active tracked products with daily scheduler enabled. Also auto-deactivate expired ones.
        Returns a list of result summaries for logging/UI.
        """
        results: list[dict] = []
        conn = sqlite3.connect("bol_tracker.db")
        c = conn.cursor()
        c.execute("SELECT id, keyword, product_id, product_url, product_name, active, created_at, stop_after_days, daily_scheduler FROM tracked_products WHERE active = 1 AND daily_scheduler = 1 ORDER BY id ASC")
        rows = c.fetchall()
        conn.close()
        for (tid, keyword, product_id, product_url, product_name, active, created_at, stop_after_days, daily_scheduler) in rows:
            # Auto-deactivate if expired
            if stop_after_days is not None:
                start_dt = self._parse_dt(created_at)
                if datetime.now() >= start_dt + timedelta(days=int(stop_after_days)):
                    self.remove_tracked_product(tid)
                    results.append({"id": tid, "keyword": keyword, "product_id": product_id, "status": "deactivated_expired"})
                    continue
            # Perform ranking check
            try:
                ranking = self.find_product_ranking(keyword, product_id)
                
                # Better name extraction logic
                name_to_store = product_name  # Use stored name first
                if not name_to_store or name_to_store == "None":
                    # Try to extract from URL if no stored name
                    name_to_store = self._extract_name_from_url(product_url)
                if ranking.get("product") and ranking["product"].get("name") and ranking["product"]["name"] != "Unknown Product":
                    # Use HTML extracted name if it's better
                    name_to_store = ranking["product"]["name"]
                
                self.save_to_db(keyword, product_id, name_to_store, ranking.get("position"), ranking.get("page_product"))
                # Update product_name in tracked_products if missing
                if not product_name and ranking.get("product"):
                    conn = sqlite3.connect("bol_tracker.db")
                    c = conn.cursor()
                    c.execute("UPDATE tracked_products SET product_name = ? WHERE id = ?", (ranking["product"]["name"], tid))
                    conn.commit()
                    conn.close()
                results.append({
                    "id": tid,
                    "keyword": keyword,
                    "product_id": product_id,
                    "position": ranking.get("position"),
                    "total_found": ranking.get("total_found")
                })
            except Exception as e:
                results.append({"id": tid, "keyword": keyword, "product_id": product_id, "error": str(e)})
        return results

def main():
    tracker = ProductTracker()
    
    print("🚀 Bol.com Product Ranking Tracker")
    print("=" * 50)
    print("This tool finds the ranking position of your product in Bol.com search results")
    print()
    
    # Example: Track a real iPhone 14 product
    # You can replace this with any real Bol.com product URL
    keyword = "Lenor"
    product_url = "https://www.bol.com/nl/nl/p/lenor-geurbooster-voor-je-was-orchidee-en-amber-voordeelverpakking-6-x-235g/9300000170626119/?cid=1758284946748-3945200593830&bltgh=54e38dc7-5eee-4a85-86cf-7ce25e373014.ProductList_Middle.0.ProductImage"
    
    print("📝 Example Configuration:")
    print(f"   Keyword: {keyword}")
    print(f"   Product URL: {product_url}")
    print()
    print("💡 To track your own product:")
    print("   1. Go to Bol.com and find your product")
    print("   2. Copy the product URL")
    print("   3. Edit the 'product_url' variable in this script")
    print("   4. Change the 'keyword' to what customers search for")
    print()
    
    # Track the product
    result = tracker.track_product(keyword, product_url)
    
    if result:
        print("\n✅ Tracking completed!")
        
        # Show history
        history = tracker.get_tracking_history()
        if history:
            print(f"\n📈 Tracking History ({len(history)} entries):")
            for i, record in enumerate(history[:5]):  # Show last 5
                print(f"   {i+1}. {record[5]} - Position: {record[4] if record[4] else 'Not found'} - {record[2]}")
    else:
        print("\n❌ Tracking failed!")

if __name__ == "__main__":
    main()
