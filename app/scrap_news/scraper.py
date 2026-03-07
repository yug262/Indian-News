import sys
import os
import time
import signal
import logging
import json
import hashlib
import re
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil import parser as date_parser
import cloudscraper
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Add the project root to sys.path so we can import app.core.db
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from app.core.db import execute_query, fetch_one

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
FAST_INTERVAL = 15         # seconds between fast source cycles
REUTERS_INTERVAL = 60      # seconds between Reuters cycles (heavy)
DETAIL_WORKERS = 5          # threads for fetching article details
SOURCE_WORKERS = 5          # threads for scraping sources concurrently
MAX_RETRIES = 2             # retries per source on transient failure
RETRY_BACKOFF = 2           # base seconds for exponential backoff

# ══════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("scraper")
logger.setLevel(logging.INFO)

# Console handler
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S UTC"))
logger.addHandler(_ch)

# File handler with rotation
from logging.handlers import RotatingFileHandler
_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "scraper.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_fh)

# ══════════════════════════════════════════════════════
#  SHARED INSTANCES
# ══════════════════════════════════════════════════════
_scraper = None
_scraper_lock = threading.Lock()

def get_scraper():
    global _scraper
    with _scraper_lock:
        if _scraper is None:
            _scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
    return _scraper

# Playwright (for Reuters only) — must be thread-local since Playwright is not thread-safe
_pw_local = threading.local()

def _has_display():
    """Check if a display is available (Windows always True, Linux checks DISPLAY)."""
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def get_reuters_browser():
    """Get or create Playwright browser on the current thread."""
    browser = getattr(_pw_local, 'browser', None)
    if browser is not None and browser.is_connected():
        return browser
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        if not hasattr(_pw_local, 'stealth'):
            _pw_local.stealth = Stealth()
        pw = getattr(_pw_local, 'pw', None)
        if pw is None:
            _pw_local.pw = sync_playwright().start()
        _pw_local.browser = _pw_local.pw.chromium.launch(
            headless=False,
            args=["--start-minimized", "--window-position=-2400,-2400"]
        )
        return _pw_local.browser
    except Exception as e:
        logger.warning(f"Playwright unavailable: {e}")
        return None

# ══════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════
def get_hash(text):
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()

def parse_relative_time(time_str):
    time_str = time_str.lower()
    now = datetime.now(timezone.utc)
    try:
        match = re.search(r'(\d+)', time_str)
        if match:
            num = int(match.group(1))
            if 'min' in time_str:
                return now - timedelta(minutes=num)
            elif 'hour' in time_str:
                return now - timedelta(hours=num)
            elif 'day' in time_str:
                return now - timedelta(days=num)
    except Exception:
        pass
    return None

def extract_time(element):
    if not element:
        return None
    for time_tag in element.find_all('time'):
        if time_tag.has_attr('datetime'):
            try:
                dt = date_parser.parse(time_tag['datetime'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except:
                pass
        text = time_tag.text.strip().lower()
        if 'ago' in text:
            parsed = parse_relative_time(text)
            if parsed:
                return parsed
    for span in element.find_all(['span', 'div', 'p']):
        text = span.get_text(strip=True).lower()
        if 'ago' in text and len(text) < 30:
            parsed = parse_relative_time(text)
            if parsed:
                return parsed
    return None

# ══════════════════════════════════════════════════════
#  RETRY WRAPPER
# ══════════════════════════════════════════════════════
def with_retry(fn, *args, retries=MAX_RETRIES, label=""):
    for attempt in range(retries + 1):
        try:
            return fn(*args)
        except Exception as e:
            if attempt < retries:
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.warning(f"{label} attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"{label} failed after {retries+1} attempts: {e}")
                return []

# ══════════════════════════════════════════════════════
#  ARTICLE DETAIL FETCHER
# ══════════════════════════════════════════════════════
def fetch_article_details(link, source):
    """Visit article page → extract description, image, published time."""
    result = {"description": None, "image_url": None, "published": None}
    try:
        if source in ["Reuters", "Bloomberg"]:
            resp = cffi_requests.get(link, impersonate="chrome120", timeout=10)
        else:
            resp = get_scraper().get(link, timeout=10)

        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, 'lxml')

        # Image
        og_img = soup.find('meta', attrs={'property': 'og:image'})
        if og_img and og_img.get('content'):
            result['image_url'] = og_img['content'].strip()

        # Published time — priority: meta tags > JSON-LD > <time> tags
        for prop in ['article:published_time', 'og:article:published_time', 'datePublished']:
            meta_time = soup.find('meta', attrs={'property': prop}) or soup.find('meta', attrs={'name': prop})
            if meta_time and meta_time.get('content'):
                try:
                    dt = date_parser.parse(meta_time['content'])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    result['published'] = dt
                    break
                except:
                    pass

        if not result['published']:
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0]
                    if 'datePublished' in data:
                        dt = date_parser.parse(data['datePublished'])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        result['published'] = dt
                        break
                except:
                    pass

        if not result['published']:
            for time_tag in soup.find_all('time'):
                if time_tag.has_attr('datetime'):
                    try:
                        dt = date_parser.parse(time_tag['datetime'])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        result['published'] = dt
                        break
                    except:
                        pass

        # Description — priority: og:description > meta description > first <p>
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        if og_desc and og_desc.get('content') and len(og_desc['content'].strip()) > 30:
            result['description'] = og_desc['content'].strip()
            return result

        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content') and len(meta_desc['content'].strip()) > 30:
            result['description'] = meta_desc['content'].strip()
            return result

        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 50:
                result['description'] = text
                return result

    except Exception:
        pass
    return result

# ══════════════════════════════════════════════════════
#  SOURCE SCRAPERS
# ══════════════════════════════════════════════════════

def scrape_cnbc(scraper):
    articles = []
    url = "https://www.cnbc.com/finance/"
    resp = scraper.get(url, timeout=10)
    soup = BeautifulSoup(resp.text, 'lxml')
    for card in soup.find_all('div', class_='Card-titleContainer'):
        a = card.find('a')
        if a and a.text:
            link = a.get('href')
            if not link.startswith('http'):
                link = "https://www.cnbc.com" + link
            published = extract_time(card)
            articles.append({
                "title": a.text.strip(), "link": link,
                "published": published, "source": "CNBC"
            })
    return articles

def scrape_yahoo(scraper):
    articles = []
    url = "https://finance.yahoo.com/"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    for a in soup.find_all('a', href=True):
        if '/news/' in a['href'] or '/m/' in a['href']:
            h3 = a.find('h3')
            if h3 and h3.text:
                link = urljoin(url, a['href'])
                published = extract_time(a.parent.parent) if a.parent and a.parent.parent else extract_time(a.parent)
                articles.append({
                    "title": h3.text.strip(), "link": link,
                    "published": published, "source": "Yahoo Finance"
                })
    return articles

def scrape_reuters_playwright():
    """Scrape Reuters via Playwright (requires display)."""
    articles = []
    browser = get_reuters_browser()
    if browser is None:
        return None  # Signal to use fallback

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080}, locale="en-US",
    )
    page = context.new_page()
    stealth = getattr(_pw_local, 'stealth', None)
    if stealth:
        stealth.apply_stealth_sync(page)

    page.goto("https://www.reuters.com/business/finance/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(10000)
    html = page.content()
    context.close()

    if len(html) < 5000:
        logger.warning(f"Reuters Playwright: blocked ({len(html)} bytes)")
        return None  # Signal to use fallback

    soup = BeautifulSoup(html, 'lxml')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/business/' in href or '/markets/' in href:
            text = " ".join(a.get_text(strip=True).split())
            if len(text) > 25:
                link = urljoin("https://www.reuters.com", href)
                published = extract_time(a.parent) if a.parent else None
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "Reuters"
                })
    unique = {a['title']: a for a in articles}.values()
    return list(unique)

def scrape_reuters_sitemap():
    """Fallback: Reuters official news sitemap (works on any server)."""
    articles = []
    url = "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml"
    resp = cffi_requests.get(url, impersonate="chrome120", timeout=15)
    soup = BeautifulSoup(resp.text, 'xml')
    for url_tag in soup.find_all('url'):
        loc = url_tag.find('loc')
        if not loc:
            continue
        link = loc.text.strip()
        news_tag = url_tag.find('news')
        title, published = None, None
        if news_tag:
            title_tag = news_tag.find('title')
            if title_tag:
                title = title_tag.text.strip()
            pub_tag = news_tag.find('publication_date')
            if pub_tag and pub_tag.text:
                try:
                    published = date_parser.parse(pub_tag.text)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except:
                    pass
        if title and len(title) > 25:
            articles.append({
                "title": title, "link": link,
                "published": published, "source": "Reuters"
            })
    unique = {a['title']: a for a in articles}.values()
    return list(unique)

def scrape_reuters(scraper):
    """Scrape Reuters directly from reuters.com/business/finance/ via Playwright."""
    return scrape_reuters_playwright() or []

def scrape_bloomberg(scraper):
    articles = []
    url = "https://www.bloomberg.com/markets"
    resp = cffi_requests.get(url, impersonate="chrome120", timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    for a in soup.find_all('a', href=True):
        if '/news/articles/' in a['href']:
            text = " ".join(a.text.strip().split())
            if len(text) > 25:
                link = urljoin("https://www.bloomberg.com", a['href'])
                published = extract_time(a.parent.parent) if a.parent and a.parent.parent else extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "Bloomberg"
                })
    unique = {a['title']: a for a in articles}.values()
    return list(unique)

def scrape_apnews(scraper):
    articles = []
    url = "https://apnews.com/"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/article/' in href:
            text = " ".join(a.get_text(strip=True).split())
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin("https://apnews.com", href)
                published = extract_time(a.parent) if a.parent else None
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "AP News"
                })
    return articles

# ══════════════════════════════════════════════════════
#  MAIN ENGINE
# ══════════════════════════════════════════════════════
_shutdown = threading.Event()

def _scrape_source(fn, scraper, label):
    """Scrape one source with retry logic."""
    return with_retry(fn, scraper, retries=MAX_RETRIES, label=label)

def fetch_and_store(sources):
    """Scrape given sources concurrently, fetch details, batch-insert to DB."""
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    scraper = get_scraper()
    all_articles = []

    # 1. Scrape all sources concurrently
    with ThreadPoolExecutor(max_workers=SOURCE_WORKERS) as pool:
        futures = {
            pool.submit(_scrape_source, fn, scraper, fn.__name__): fn.__name__
            for fn in sources
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if result:
                    all_articles.extend(result)
            except Exception as e:
                logger.error(f"{name} unexpected error: {e}")

    # 2. Filter short titles & check duplicates
    new_articles = []
    dup_count = 0
    for article in all_articles:
        if len(article['title']) < 15:
            continue
        try:
            existing = fetch_one(
                "SELECT id FROM news WHERE link = %s OR title = %s",
                (article['link'], article['title'])
            )
            if existing:
                dup_count += 1
            else:
                new_articles.append(article)
        except Exception as e:
            logger.error(f"DB check error: {e}")

    # 3. Fetch details for new articles concurrently
    details_map = {}
    if new_articles:
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            futures = {
                pool.submit(fetch_article_details, a['link'], a['source']): i
                for i, a in enumerate(new_articles)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    details_map[idx] = future.result()
                except Exception:
                    details_map[idx] = {"description": None, "image_url": None, "published": None}

    # 4. Batch insert new articles
    new_count = 0
    for i, article in enumerate(new_articles):
        if _shutdown.is_set():
            break
        details = details_map.get(i, {"description": None, "image_url": None, "published": None})
        description = details['description'] or article['title']
        image_url = details['image_url']
        actual_published = details['published'] or article.get('published') or datetime.now(timezone.utc)

        try:
            title_hash = get_hash(article['title'])
            execute_query(
                """INSERT INTO news (title, link, title_hash, published, source, description, image_url, analyzed, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, False, %s)""",
                (article['title'], article['link'], title_hash, actual_published,
                 article['source'], description, image_url, datetime.now(timezone.utc))
            )
            new_count += 1
            logger.info(f"[+] {article['source']} - {article['title'][:70]}")
            logger.info(f"     Desc: {description[:80]}...")
            logger.info(f"     Published: {actual_published}")
            if image_url:
                logger.info(f"     Img: {image_url[:60]}...")
        except Exception as e:
            if "duplicate key value" not in str(e).lower():
                logger.error(f"DB insert error: {e}")

    src_names = ", ".join(fn.__name__.replace("scrape_", "").upper() for fn in sources)
    logger.info(f"[{ts}] {src_names} → Total: {len(all_articles)} | Dup: {dup_count} | New: {new_count}")
    return new_count

# ══════════════════════════════════════════════════════
#  SCHEDULER
# ══════════════════════════════════════════════════════
FAST_SOURCES = [scrape_cnbc, scrape_yahoo, scrape_bloomberg, scrape_apnews]
HEAVY_SOURCES = [scrape_reuters]

def run_fast_loop():
    """Scrape fast sources (CNBC, Yahoo, Bloomberg, AP News) every FAST_INTERVAL seconds."""
    while not _shutdown.is_set():
        try:
            fetch_and_store(FAST_SOURCES)
        except Exception as e:
            logger.error(f"Fast loop error: {e}")
        _shutdown.wait(FAST_INTERVAL)

def run_heavy_loop():
    """Scrape Reuters on a dedicated thread — Playwright MUST stay on this thread."""
    while not _shutdown.is_set():
        try:
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            scraper = get_scraper()
            
            # Call Reuters directly on THIS thread (no ThreadPoolExecutor!)
            articles = with_retry(scrape_reuters, scraper, retries=MAX_RETRIES, label="scrape_reuters") or []
            
            # Filter & deduplicate
            new_articles = []
            dup_count = 0
            for article in articles:
                if len(article['title']) < 15:
                    continue
                try:
                    existing = fetch_one(
                        "SELECT id FROM news WHERE link = %s OR title = %s",
                        (article['link'], article['title'])
                    )
                    if existing:
                        dup_count += 1
                    else:
                        new_articles.append(article)
                except Exception as e:
                    logger.error(f"DB check error: {e}")
            
            # Fetch details & insert (inline, same thread)
            new_count = 0
            for article in new_articles:
                if _shutdown.is_set():
                    break
                details = fetch_article_details(article['link'], article['source'])
                description = details['description'] or article['title']
                image_url = details['image_url']
                actual_published = details['published'] or article.get('published') or datetime.now(timezone.utc)
                


                
                try:
                    title_hash = get_hash(article['title'])
                    execute_query(
                        """INSERT INTO news (title, link, title_hash, published, source, description, image_url, analyzed, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, False, %s)""",
                        (article['title'], article['link'], title_hash, actual_published,
                         article['source'], description, image_url, datetime.now(timezone.utc))
                    )
                    new_count += 1
                    logger.info(f"[+] Reuters - {article['title'][:70]}")
                    logger.info(f"     Desc: {description[:80]}...")
                    logger.info(f"     Published: {actual_published}")
                    if image_url:
                        logger.info(f"     Img: {image_url[:60]}...")
                except Exception as e:
                    if "duplicate key value" not in str(e).lower():
                        logger.error(f"DB insert error: {e}")
            
            logger.info(f"[{ts}] REUTERS → Total: {len(articles)} | Dup: {dup_count} | New: {new_count}")
        except Exception as e:
            logger.error(f"Reuters loop error: {e}")
        _shutdown.wait(REUTERS_INTERVAL)

def cleanup():
    """Clean up Playwright browser on exit."""
    try:
        browser = getattr(_pw_local, 'browser', None)
        pw = getattr(_pw_local, 'pw', None)
        if browser:
            browser.close()
        if pw:
            pw.stop()
    except Exception:
        pass
    logger.info("Cleanup complete.")

def shutdown_handler(signum, frame):
    logger.info("Shutdown signal received. Stopping...")
    _shutdown.set()


def main():
    # Signal handlers only work on the main thread
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

    logger.info("=" * 60)
    logger.info("NEWS SCRAPER STARTED")
    logger.info(f"  Fast sources: every {FAST_INTERVAL}s  |  Reuters: every {REUTERS_INTERVAL}s")
    logger.info(f"  Sources: CNBC, Yahoo, Bloomberg, AP News, Reuters")
    logger.info(f"  Detail workers: {DETAIL_WORKERS}  |  Source workers: {SOURCE_WORKERS}")
    logger.info(f"  Retries: {MAX_RETRIES}  |  Backoff: {RETRY_BACKOFF}s")
    logger.info(f"  Display available: {_has_display()}")
    logger.info("=" * 60)

    fast_thread = threading.Thread(target=run_fast_loop, name="fast-scraper", daemon=True)
    heavy_thread = threading.Thread(target=run_heavy_loop, name="reuters-scraper", daemon=True)

    fast_thread.start()
    heavy_thread.start()

    try:
        while not _shutdown.is_set():
            _shutdown.wait(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
        _shutdown.set()

    # Wait for threads to finish current cycle
    fast_thread.join(timeout=5)
    heavy_thread.join(timeout=5)
    cleanup()
    logger.info("Scraper stopped.")

# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    main()