import asyncio
import aiohttp
import feedparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dateutil import parser as date_parser
import hashlib
from difflib import SequenceMatcher
from urllib.parse import urlparse
from app.core.agent import classify_batch
from app.core.db import fetch_all, execute_many, execute_query
import re
from app.workers.prediction_monitor import check_predictions
import socket

# ==============================
# CONFIG
# ==============================

RSS_FEEDS = [
    # ✅ Official Crypto News
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptopotato.com/feed/",

    # ✅ Forex & Macro Market News
    "https://www.forexlive.com/feed/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",

    # ✅ Central Bank Official Feeds
    "https://www.bis.org/doclist/cbspeeches.rss",
    "https://www.bis.org/doclist/rss_all_categories.rss",
    "https://www.federalreserve.gov/feeds/speeches.xml",
    "https://www.federalreserve.gov/feeds/press_all.xml",


    "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "https://finance.yahoo.com/rss/topstories",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom",

    "https://cryptoslate.com/feed/",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblock.co/rss.xml",
    "https://www.dailyfx.com/feeds/market-news",
    "https://www.fxstreet.com/rss/news",
    "https://www.investing.com/rss/news_1.rss",
    # "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.ft.com/world?format=rss",
    # "https://feeds.marketwatch.com/marketwatch/topstories/"
    "https://www.ft.com/markets?format=rss",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.ecb.europa.eu/rss/press.html",
    "https://finance.yahoo.com/news/rssindex",
    "https://cointelegraph.com/rss",
    "https://www.investing.com/rss/news.rss",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"
    # "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.ecb.europa.eu/rss/press.html",
]
FETCH_INTERVAL = 30  # seconds

# ==============================
# HELPER FUNCTIONS
# ==============================

def cleanup_old_news():
    """Delete news older than 24 hours based on scrape time."""
    try:
        deleted = execute_query("DELETE FROM news WHERE published < NOW() - INTERVAL '24 hours'")
        if deleted > 0:
            print(f"🧹 Cleaned up {deleted} old article(s) older than 24 hours.")
    except Exception as e:
        print(f"Error during cleanup: {e}")

def get_existing_hashes():
    """Fetch all hashes currently in the database."""
    query = "SELECT title_hash, title FROM news"
    rows = fetch_all(query)
    
    hashes = {row['title_hash'] for row in rows}
    titles = [row['title'] for row in rows]
    return hashes, titles

def is_today(published_time):
    if not published_time:
        return False
    today = datetime.now(timezone.utc).date()
    return published_time.date() == today

def get_hash(text):
    normalized = text.strip().lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()

def is_similar(a, b, threshold=0.9):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold

def extract_source(url):
    """Extract domain name as the source from an RSS URL."""
    try:
        domain = urlparse(url).netloc
        domain = domain.replace("www.", "")
        return domain.capitalize()
    except:
        return "Unknown"

def extract_image(entry):
    """Extract the best image URL from an RSS entry."""
    # Try media_content (most common)
    media = entry.get("media_content", [])
    if media and isinstance(media, list):
        for m in media:
            url = m.get("url", "")
            if url:
                return url

    # Try media_thumbnail
    thumb = entry.get("media_thumbnail", [])
    if thumb and isinstance(thumb, list):
        for t in thumb:
            url = t.get("url", "")
            if url:
                return url

    # Try enclosures
    enclosures = entry.get("enclosures", [])
    if enclosures:
        for enc in enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href", enc.get("url", ""))

    # Try parsing img from summary/description HTML
    summary = entry.get("summary", entry.get("description", ""))
    if summary:
        match = re.search(r'<img[^>]+src=["\']([^"\'>]+)["\']', summary)
        if match:
            return match.group(1)

    return ""

# ==============================
# FETCH SINGLE RSS
# ==============================

class DictWithAttrs(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)
    def __setattr__(self, key, value):
        self[key] = value

async def fetch_feed(session, url, semaphore):
    async with semaphore:
        for attempt in range(2):
            try:
                async with session.get(url, timeout=10) as response:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    # Attach the source URL to each entry so we know where it came from
                    source_name = extract_source(url)
                    
                    entries = feed.entries
                    
                    # If feedparser finds no entries, fallback to treating it as a news sitemap
                    if not entries and "<urlset" in content:
                        try:
                            root = ET.fromstring(content)
                            ns = {
                                'sitemap': 'http://www.sitemaps.org/schemas/sitemap/0.9',
                                'news': 'http://www.google.com/schemas/sitemap-news/0.9',
                                'image': 'http://www.google.com/schemas/sitemap-image/1.1'
                            }
                            for url_elem in root.findall('sitemap:url', ns):
                                loc = url_elem.find('sitemap:loc', ns)
                                news_elem = url_elem.find('news:news', ns)
                                
                                if loc is not None and news_elem is not None:
                                    title_elem = news_elem.find('news:title', ns)
                                    pub_date_elem = news_elem.find('news:publication_date', ns)
                                    
                                    entry = DictWithAttrs()
                                    entry['link'] = loc.text
                                    if title_elem is not None:
                                        entry['title'] = title_elem.text
                                    if pub_date_elem is not None:
                                        entry['published'] = pub_date_elem.text
                                        
                                    entry['summary'] = ""
                                    
                                    image_elem = url_elem.find('image:image/image:loc', ns)
                                    if image_elem is not None:
                                        entry['media_content'] = [{'url': image_elem.text}]
                                    
                                    entries.append(entry)
                        except Exception as xml_e:
                            print(f"Error parsing sitemap XML for {url}: {xml_e}")

                    for entry in entries:
                        entry.source = source_name
                    return entries
            except Exception as e:
                if attempt == 1:
                    print(f"Error fetching {url}: {e}")
                else:
                    await asyncio.sleep(1)
        return []

# ==============================
# MAIN FETCH FUNCTION
# ==============================

async def fetch_all_feeds(session, semaphore):
    cleanup_old_news()
    try:
        existing_hashes, existing_titles = get_existing_hashes()
    except Exception as e:
        print(f"Database error checking existing news: {e}")
        return

    tasks = [fetch_feed(session, url, semaphore) for url in RSS_FEEDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out exceptions if any crept through
    results = [r for r in results if isinstance(r, list)]

    new_items_params = []
    
    for entries in results:
        for entry in entries:
            try:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                source = getattr(entry, "source", "Unknown")
                description = entry.get("summary", entry.get("description", "")).strip()
                # Clean HTML tags from description
                
                description = re.sub(r'<[^>]+>', '', description).strip()
                if len(description) > 500:
                    description = description[:500] + '...'
                image_url = extract_image(entry)

                if not title or not link:
                    continue

                # Deduplicate by hash
                news_hash = get_hash(title)
                if news_hash in existing_hashes:
                    continue

                # Optional fuzzy check across existing news titles
                if any(is_similar(title, existing_title) for existing_title in existing_titles):
                    continue

                published = None
                if "published" in entry:
                    published = date_parser.parse(entry.published)
                    if published.tzinfo is None:
                         published = published.replace(tzinfo=timezone.utc)
                    else:
                         published = published.astimezone(timezone.utc)

                if not is_today(published):
                    continue

                # Prepare parameters for batch insert (without relevance yet)
                new_items_params.append((
                    title, 
                    link, 
                    news_hash, 
                    published, 
                    source,
                    description,
                    image_url
                ))
                
                existing_hashes.add(news_hash)
                existing_titles.append(title)

            except Exception as e:
                # print(f"Error processing entry: {e}")
                continue

    if new_items_params:
        print(f"Found {len(new_items_params)} new articles")
        
        # ── Step 1: Classify all new articles FIRST ──
        
        classify_items = [(p[0], p[5]) for p in new_items_params]  # (title, description)
        try:
            classification_results = classify_batch(classify_items)
            for c_res, item in zip(classification_results, classify_items):
                impact = c_res.get("impact_level")
                print(f"  [{impact.upper():7s}] {item[0][:80]}")
        except Exception as e:
            print(f"[CLASSIFY] Batch classification failed: {e}")
            classification_results = [{"category": "error", "impact_level": "none", "reason": str(e)}] * len(new_items_params)

        # ── Step 2: Store all articles in the DB (with relevance) ──
        final_params = []
        mapped_relevances = []
        for params, c_res in zip(new_items_params, classification_results):
            impact = c_res.get("impact_level", "none").lower()
            category = c_res.get("category", "")
            
            # Map category to relevance/type
            crypto_useful_categories=[
                "crypto_ecosystem_event","regulatory_policy"
            ]
            forex_useful_categories=[
                "commodity_supply_shock", "geopolitical_event"
            ]
            very_high_useful_categories=[
                "macro_data_release","central_bank_policy","central_bank_guidance","systemic_risk_event"
            ]
            useful_categories = [
                "institutional_research", 
                "liquidity_flows"
            ]
            medium_neutral_categories = [
                "market_structure_event", "sector_trend_analysis",
                "sentiment_indicator"
            ]
            noisy_categories = [
                "price_action_noise","routine_market_update"
            ]
            if category in crypto_useful_categories:
                relevance = "crypto useful"
            elif category in forex_useful_categories:
                relevance = "forex useful"
            elif category in very_high_useful_categories:
                relevance = "very high useful"
            elif category in useful_categories:
                relevance = "useful"
            elif category in medium_neutral_categories:
                relevance = "medium/neutral"
            elif category in noisy_categories:
                relevance = "noisy"
            else:
                relevance = "neutral"

            mapped_relevances.append(relevance)
            final_params.append(
                (*params, relevance, category, impact, c_res.get("reason", ""))
            )

        insert_query = """
            INSERT INTO news (title, link, title_hash, published, source, description, image_url, news_relevance, news_category, news_impact_level, news_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (title_hash) DO NOTHING
            RETURNING id
        """
        try:
            execute_many(insert_query, final_params)
            print("Successfully saved to database.")
        except Exception as e:
            print(f"Failed to insert articles into database: {e}")
            return  # Can't analyze if insert failed

        
    else:
        print("No new articles")

# ==============================
# MAIN BACKGROUND LOOPS
# ==============================

async def run_predictions_loop():
    while True:
        print(f"\n[{datetime.now(timezone.utc).isoformat()}] Running prediction check...")
        try:
            check_predictions()
        except Exception as e:
            print(f"[PRED] Error during prediction check: {e}")
        await asyncio.sleep(15)

async def main():
    # Start prediction monitor loop concurrently
    asyncio.create_task(run_predictions_loop())
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver(),
        limit=10, 
        limit_per_host=2,
        ttl_dns_cache=300,
        use_dns_cache=True,
        family=socket.AF_INET,
        force_close=False,
    )
    
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        semaphore = asyncio.Semaphore(5)
        
        while True:
            print(f"\nChecking feeds at {datetime.now(timezone.utc)} UTC")
            await fetch_all_feeds(session, semaphore)
            await asyncio.sleep(FETCH_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())