import sys
import os
import time
import signal
import logging
import json
import hashlib
import re
import threading
import asyncio
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil import parser as date_parser
import cloudscraper
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Add the project root to sys.path so we can import app.core.db
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from app.core.db import execute_query, fetch_one, execute_many, fetch_all
from app.core.agent import classify_news_relevance

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
FAST_INTERVAL = 30         # seconds between fast source cycles
SLOW_INTERVAL = 30         # seconds between slow source cycles
REUTERS_INTERVAL = 60     # seconds between Reuters cycles (heavy)
DETAIL_WORKERS = 8         # threads for fetching article details
SOURCE_WORKERS = 12        # threads for scraping sources concurrently
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

# Global Deduplication Cache
_seen_urls = set()
_seen_titles = set()
_cache_lock = threading.Lock()

def preload_cache():
    """Load recent articles from DB into memory cache to avoid DB duplicate checks."""
    try:
        # Load the last 2000 articles for a robust warm cache
        rows = fetch_all("SELECT link, title FROM news ORDER BY id DESC LIMIT 2000")
        with _cache_lock:
            for r in rows:
                if r.get('link'):
                    _seen_urls.add(r['link'])
                if r.get('title'):
                    _seen_titles.add(r['title'])
        logger.info(f"Preloaded cache with {len(rows)} recent articles.")
    except Exception as e:
        logger.error(f"Failed to preload cache: {e}")

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
        if source in ["Reuters", "Bloomberg", "AP News"]:
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
        for prop in ['article:published_time', 'og:article:published_time', 'datePublished', 'parsely-pub-date', 'pubdate']:
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

def extract_clean_title(a_tag):
    """Safely extract just the headline text from an anchor tag, avoiding merged metadata."""
    if not a_tag: return ""
    
    if 'data-title' in a_tag.attrs and a_tag['data-title'].strip():
        return a_tag['data-title'].strip()
        
    aria = a_tag.get('aria-label', '').strip()
    if aria and len(aria) > 20:
        return aria
        
    heading = a_tag.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    if heading:
        return " ".join(heading.get_text(separator=' ', strip=True).split())
        
    for span_class in ['js-headline-text', 'ui-story-headline']:
        span = a_tag.find('span', class_=span_class)
        if span:
            return " ".join(span.get_text(separator=' ', strip=True).split())
            
    # Deep nested spans used by newer AP News and Bloomberg layouts
    for span in a_tag.find_all('span'):
        txt = " ".join(span.get_text(separator=' ', strip=True).split())
        if len(txt) > 25:
            return txt
            
    return " ".join(a_tag.get_text(separator=' ', strip=True).split())

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
            title = extract_clean_title(a)
            if len(title) > 15:
                articles.append({
                    "title": title, "link": link,
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
                title = extract_clean_title(h3)
                if len(title) > 15:
                    articles.append({
                        "title": title, "link": link,
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
            text = extract_clean_title(a)
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
    # Switch back to cloudscraper to handle advanced JS challenges blocking cffi_requests
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    for a in soup.find_all('a', href=True):
        if '/news/articles/' in a['href']:
            text = extract_clean_title(a)
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
    resp = cffi_requests.get(url, impersonate="chrome120", timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/article/' in href:
            text = extract_clean_title(a)
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin("https://apnews.com", href)
                published = extract_time(a.parent) if a.parent else None
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "AP News"
                })
    return articles


def _scrape_apnews_section(scraper, section_url, source_label):
    """Generic AP News section scraper — reused by world, politics, and business."""
    articles = []
    resp = cffi_requests.get(section_url, impersonate="chrome120", timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/article/' in href:
            text = extract_clean_title(a)
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin("https://apnews.com", href)
                published = extract_time(a.parent) if a.parent else None
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": source_label
                })
    return articles


def scrape_apnews_world(scraper):
    return _scrape_apnews_section(scraper, "https://apnews.com/world-news", "AP News")


def scrape_apnews_politics(scraper):
    return _scrape_apnews_section(scraper, "https://apnews.com/politics", "AP News")


def scrape_apnews_business(scraper):
    return _scrape_apnews_section(scraper, "https://apnews.com/business", "AP News")


def scrape_bbc(scraper):
    articles = []
    url = "https://www.bbc.com/news"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/news/articles/' in href:
            text = extract_clean_title(a)
            
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin(url, href)
                published = extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "BBC News"
                })
    return articles

def scrape_aljazeera(scraper):
    articles = []
    url = "https://www.aljazeera.com"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/202' in href:
            text = extract_clean_title(a)
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin(url, href)
                published = extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "Al Jazeera"
                })
    return articles

def scrape_france24(scraper):
    articles = []
    url = "https://www.france24.com/en/"
    # Switching to curl_cffi to bypass 403 Forbidden errors
    resp = cffi_requests.get(url, impersonate="chrome120", timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/en/' in href:
            text = extract_clean_title(a)
            if len(text) > 30 and text not in seen:
                seen.add(text)
                link = urljoin(url, href)
                published = extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "France 24"
                })
    return articles

def scrape_skynews(scraper):
    articles = []
    url = "https://news.sky.com"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/story/' in href:
            text = extract_clean_title(a)
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin(url, href)
                published = extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "Sky News"
                })
    return articles

def scrape_guardian(scraper):
    articles = []
    url = "https://www.theguardian.com/international"
    resp = scraper.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, 'lxml')
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/202' in href and len(href.split('/')) > 4:
            text = extract_clean_title(a)
            if len(text) > 25 and text not in seen:
                seen.add(text)
                link = urljoin(url, href)
                published = extract_time(a.parent)
                articles.append({
                    "title": text, "link": link,
                    "published": published, "source": "The Guardian"
                })
    return articles

# ══════════════════════════════════════════════════════
#  MAIN ENGINE
# ══════════════════════════════════════════════════════
_shutdown = threading.Event()

def _scrape_source(fn, scraper, label):
    """Scrape one source with retry logic."""
    return with_retry(fn, scraper, retries=MAX_RETRIES, label=label)

def fetch_and_store_single(fn):
    """Scrape single source, fetch details, batch-insert to DB."""
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    scraper = get_scraper()
    all_articles = []

    # 1. Scrape source
    try:
        result = _scrape_source(fn, scraper, fn.__name__)
        if result:
            all_articles.extend(result)
    except Exception as e:
        logger.error(f"{fn.__name__} unexpected error: {e}")

    # 2. Filter short titles & check memory cache first
    new_articles = []
    dup_count = 0
    for article in all_articles:
        if len(article['title']) < 15:
            continue
            
        is_dup = False
        with _cache_lock:
            if article['link'] in _seen_urls or article['title'] in _seen_titles:
                is_dup = True
                
        if is_dup:
            dup_count += 1
            continue
            
        try:
            # Secondary check in case it's not in the 2000 cache but is in the DB
            existing = fetch_one(
                "SELECT id FROM news WHERE link = %s OR title = %s",
                (article['link'], article['title'])
            )
            if existing:
                dup_count += 1
                # Add to memory cache to prevent future DB checks for this one
                with _cache_lock:
                    _seen_urls.add(article['link'])
                    _seen_titles.add(article['title'])
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

    # Filter to keep only news from the last 24 hours
    now = datetime.now(timezone.utc)
    filtered_articles = []
    filtered_details = {}
    for i, a in enumerate(new_articles):
        details = details_map.get(i, {"description": None, "image_url": None, "published": None})
        actual_published = details.get('published') or a.get('published')
        if actual_published and actual_published >= now - timedelta(hours=24):
            filtered_details[len(filtered_articles)] = details
            filtered_articles.append(a)
    new_articles = filtered_articles
    details_map = filtered_details

    # 4. Classify new articles concurrently
    if new_articles:
        classification_results = []
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            futures = []
            for i, a in enumerate(new_articles):
                desc = details_map.get(i, {}).get('description') or a['title']
                futures.append(pool.submit(classify_news_relevance, a['title'], desc))
            
            for future in futures:
                try:
                    classification_results.append(future.result())
                except Exception as e:
                    logger.error(f"Classification failed: {e}")
                    classification_results.append({"category": "error", "relevance": "none", "reason": str(e)})
                    
        for c_res, a in zip(classification_results, new_articles):
            rel = str(c_res.get("relevance", "none"))
            logger.info(f"  [{rel.upper():12s}] {a['title'][:80]}")
    else:
        classification_results = []

    # 5. Insert new articles with classification
    new_count = 0
    for i, article in enumerate(new_articles):
        if _shutdown.is_set():
            break
        details = details_map.get(i, {"description": None, "image_url": None, "published": None})
        description = details['description'] or article['title']
        image_url = details['image_url']
        actual_published = details.get('published') or article.get('published')

        c_res = classification_results[i] if i < len(classification_results) else {}
        category = str(c_res.get("category", ""))[:50]
        relevance = str(c_res.get("relevance", "Neutral"))[:20]
        reason = str(c_res.get("reason", ""))[:500]

        try:
            title_hash = get_hash(article['title'])
            execute_query(
                """INSERT INTO news (title, link, title_hash, published, source, description, image_url,
                                    news_relevance, news_category, news_reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (title_hash) DO NOTHING""",
                (article['title'], article['link'], title_hash, actual_published,
                 article['source'], description, image_url,
                 relevance, category, reason)
            )
            new_count += 1
            
            # Add to memory cache so next scrape is O(1)
            with _cache_lock:
                _seen_urls.add(article['link'])
                _seen_titles.add(article['title'])
                
            logger.info(f"[+] {article['source']} - {article['title'][:70]}")
            logger.info(f"     Desc: {description[:80]}...")
            logger.info(f"     Published: {actual_published}")
            logger.info(f"     Classified: {relevance} | {category}")
            if image_url:
                logger.info(f"     Img: {image_url[:60]}...")
        except Exception as e:
            if "duplicate key value" not in str(e).lower():
                logger.error(f"DB insert error: {e}")

    src_name = fn.__name__.replace("scrape_", "").upper()
    logger.info(f"{src_name} → Total: {len(all_articles)} | Dup: {dup_count} | New: {new_count}")
    return new_count

# ══════════════════════════════════════════════════════
#  SCHEDULER
# ══════════════════════════════════════════════════════
FAST_SOURCES = [
    scrape_cnbc, scrape_yahoo, scrape_bloomberg,
    scrape_apnews, scrape_apnews_world, scrape_apnews_politics, scrape_apnews_business
]
SLOW_SOURCES = [
    scrape_bbc, scrape_aljazeera, scrape_france24, scrape_skynews, scrape_guardian
]
HEAVY_SOURCES = [scrape_reuters]

async def _async_sleep(interval):
    slept = 0.0
    while slept < interval and not _shutdown.is_set():
        await asyncio.sleep(0.1)
        slept += 0.1

async def run_scraper_loop(fn, interval):
    """Scrape a single standard source on its own interval."""
    # Instantly trigger the first loop instead of waiting
    while not _shutdown.is_set():
        try:
            await asyncio.to_thread(fetch_and_store_single, fn)
        except Exception as e:
            logger.error(f"{fn.__name__} loop error: {e}")
        await _async_sleep(interval)

def run_heavy_scrape():
    """Scrape Reuters continuously on a dedicated thread — Playwright MUST stay on this thread."""
    while not _shutdown.is_set():
        try:
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            scraper = get_scraper()
            
            # Call Reuters directly on THIS thread (no ThreadPoolExecutor!)
            articles = with_retry(scrape_reuters, scraper, retries=MAX_RETRIES, label="scrape_reuters") or []
            
            # Filter & deduplicate memory fast check
            new_articles = []
            dup_count = 0
            for article in articles:
                if len(article['title']) < 15:
                    continue
                    
                is_dup = False
                with _cache_lock:
                    if article['link'] in _seen_urls or article['title'] in _seen_titles:
                        is_dup = True
                
                if is_dup:
                    dup_count += 1
                    continue
                    
                try:
                    existing = fetch_one(
                        "SELECT id FROM news WHERE link = %s OR title = %s",
                        (article['link'], article['title'])
                    )
                    if existing:
                        dup_count += 1
                        with _cache_lock:
                            _seen_urls.add(article['link'])
                            _seen_titles.add(article['title'])
                    else:
                        new_articles.append(article)
                except Exception as e:
                    logger.error(f"DB check error: {e}")
            
            # Fetch details (inline, same thread)
            details_map = {}  # cache details by title
            for article in new_articles:
                if _shutdown.is_set():
                    break
                details = fetch_article_details(article['link'], article['source'])
                details_map[article['title']] = details

            # Filter to keep only news from the last 24 hours
            now = datetime.now(timezone.utc)
            filtered_articles = []
            for a in new_articles:
                details = details_map.get(a['title'], {"description": None, "image_url": None, "published": None})
                actual_published = details.get('published') or a.get('published')
                if actual_published and actual_published >= now - timedelta(hours=24):
                    filtered_articles.append(a)
            new_articles = filtered_articles

            # Classify before insert
            if new_articles:
                classification_results = []
                with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
                    futures = []
                    for a in new_articles:
                        desc = details_map.get(a['title'], {}).get('description') or a['title']
                        futures.append(pool.submit(classify_news_relevance, a['title'], desc))
                    
                    for future in futures:
                        try:
                            classification_results.append(future.result())
                        except Exception as e:
                            logger.error(f"Reuters classification failed: {e}")
                            classification_results.append({"category": "error", "relevance": "none", "reason": str(e)})

                for c_res, a in zip(classification_results, new_articles):
                    rel = str(c_res.get("relevance", "none"))
                    logger.info(f"  [{rel.upper():12s}] {a['title'][:80]}")
            else:
                classification_results = []

            # Insert new articles
            new_count = 0

            for idx, article in enumerate(new_articles):
                if _shutdown.is_set():
                    break
                details = details_map.get(article['title'], {"description": None, "image_url": None, "published": None})
                description = details['description'] or article['title']
                image_url = details['image_url']
                actual_published = details.get('published') or article.get('published')

                c_res = classification_results[idx] if idx < len(classification_results) else {}
                category = str(c_res.get("category", ""))[:50]
                relevance = str(c_res.get("relevance", "Neutral"))[:20]
                reason = str(c_res.get("reason", ""))[:500]

                try:
                    title_hash = get_hash(article['title'])
                    execute_query(
                        """INSERT INTO news (title, link, title_hash, published, source, description, image_url,
                                            news_relevance, news_category, news_reason)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (title_hash) DO NOTHING""",
                        (article['title'], article['link'], title_hash, actual_published,
                         article['source'], description, image_url,
                         relevance, category, reason)
                    )
                    new_count += 1
                    with _cache_lock:
                        _seen_urls.add(article['link'])
                        _seen_titles.add(article['title'])
                    logger.info(f"[+] Reuters - {article['title'][:70]}")
                    logger.info(f"     Classified: {relevance} | {category} ")
                except Exception as e:
                    if "duplicate key value" not in str(e).lower():
                        logger.error(f"DB insert error: {e}")
            
            logger.info(f"[{ts}] REUTERS → Total: {len(articles)} | Dup: {dup_count} | New: {new_count}")
        except Exception as e:
            logger.error(f"Reuters scrape error: {e}")
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


async def async_main():
    tasks = []
    
    for source in FAST_SOURCES:
        tasks.append(asyncio.create_task(run_scraper_loop(source, FAST_INTERVAL)))
        
    for source in SLOW_SOURCES:
        tasks.append(asyncio.create_task(run_scraper_loop(source, SLOW_INTERVAL)))
        
    tasks.append(asyncio.create_task(asyncio.to_thread(run_heavy_scrape)))

    await asyncio.gather(*tasks, return_exceptions=True)

def main():
    # Signal handlers only work on the main thread
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

    logger.info("=" * 60)
    logger.info("NEWS SCRAPER STARTED (CONTINUOUS ASYNC LOOP)")
    logger.info(f"  Fast sources ({len(FAST_SOURCES)}): CNBC, Yahoo, Bloomberg, AP News")
    logger.info(f"  Slow sources ({len(SLOW_SOURCES)}): BBC, Al Jazeera, France 24, Sky News, Guardian")
    logger.info(f"  Detail workers: {DETAIL_WORKERS}  |  Source concurrency: Parallel per source")
    logger.info(f"  Retries: {MAX_RETRIES}  |  Backoff: {RETRY_BACKOFF}s")
    logger.info(f"  Display available: {_has_display()}")
    logger.info("=" * 60)

    preload_cache()

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
        _shutdown.set()

    cleanup()
    logger.info("Scraper finished.")

# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    main()