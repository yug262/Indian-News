import sys
import os
import asyncio
import logging
import hashlib
import time
from datetime import datetime, timezone
import feedparser
import httpx
from bs4 import BeautifulSoup
import socket
from typing import List, Dict, Any

# Add the project root to sys.path so we can import app
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from app.db.db import execute_query, fetch_one, execute_returning, fetch_all
from app.agents.agent import filter_indian_news
from app.core.event_engine import process_event_grouping

# ══════════════════════════════════════════════════════
#  AUTO-ANALYSIS CONFIG
# ══════════════════════════════════════════════════════
AUTO_ANALYZE_RELEVANCE = ["Medium", "Useful", "High Useful"]
API_BASE_URL = os.getenv("API_URL", "http://localhost:8000") # Use localhost unless configured

# Managed resources
GLOBAL_CLIENT: httpx.AsyncClient = None
_BACKGROUND_TASKS = set()

async def trigger_auto_analysis(news_id: int):
    """Detached background task to hit the API server for deep analysis."""
    if not GLOBAL_CLIENT:
        return
    
    try:
        url = f"{API_BASE_URL}/api/indian_analyze/{news_id}"
        logger.info(f"[AUTO-ANALYZE] Triggering analysis for {news_id}...")
        resp = await GLOBAL_CLIENT.post(url, timeout=120)
        if resp.status_code == 200:
            logger.info(f"[AUTO-ANALYZE] Hand-off success for {news_id}")
        else:
            logger.warning(f"[AUTO-ANALYZE] Hand-off returned {resp.status_code} for {news_id}: {resp.text}")
    except Exception as e:
        logger.error(f"[AUTO-ANALYZE] Hand-off failed for {news_id}: {e}")

async def notify_dashboard(new_count: int):
    """Securely notifies the backend that new articles are available to trigger WS broadcasts."""
    if not GLOBAL_CLIENT or new_count <= 0:
        return

    try:
        url = f"{API_BASE_URL}/api/internal/new_articles"
        # We must include the internal token to pass the security check I added to indian_router.py
        headers = {"X-Internal-Token": "super-secret-sync-token"}
        resp = await GLOBAL_CLIENT.post(url, params={"count": new_count}, headers=headers, timeout=10.0)
        
        if resp.status_code == 200:
            logger.info(f"[NOTIFY] Successfully notified dashboard of {new_count} new articles.")
        else:
            logger.warning(f"[NOTIFY] Failed to notify dashboard (Status {resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"[NOTIFY] Error notifying dashboard: {e}")

#  LOGGING
# ══════════════════════════════════════════════════════
logger = logging.getLogger("indian_scraper")
logger.setLevel(logging.INFO)

# Suppress noisy INFO logs globally for the scraper
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

if not logger.handlers:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S UTC"))
    logger.addHandler(_ch)

# ══════════════════════════════════════════════════════
#  FEEDS & CONFIG
# ══════════════════════════════════════════════════════
FEEDS = {
    "Moneycontrol": [
        "https://www.moneycontrol.com/rss/MCtopnews.xml",
        "https://www.moneycontrol.com/rss/business.xml",
        "https://www.moneycontrol.com/rss/marketreports.xml"
    ],
    "Economic Times": [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms"
    ],
    "LiveMint": [
        "https://www.livemint.com/rss/markets",
        "https://www.livemint.com/rss/companies",
        "https://www.livemint.com/rss/money"
    ],
    "Financial Express": [
        "https://www.financialexpress.com/market/feed/",
        "https://www.financialexpress.com/economy/feed/"
    ],
    "Zee Business": [
        "https://zeenews.india.com/rss/business.xml"
    ],
    "DNA India": [
        "https://www.dnaindia.com/feeds/business.xml"
    ],
    "Investing India": [
        "https://in.investing.com/rss/news_25.rss",
        "https://in.investing.com/rss/news_1.rss"
    ],
    "Indian Express (Business)": [
        "https://indianexpress.com/section/business/feed/"
    ],
    "Times of India (Business)": [
        "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms"
    ],
    "Tickertape": [
        "https://www.tickertape.in/blog/feed/"
    ],
    "Trade Brains": [
        "https://tradebrains.in/feed/"
    ]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edge/122.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def get_hash(text):
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()

def clean_html(raw_html):
    if not raw_html: return ""
    try:
        return BeautifulSoup(raw_html, "lxml").get_text(separator=" ", strip=True)
    except:
        return raw_html

async def fetch_feed_task(client: httpx.AsyncClient, source: str, url: str) -> List[Dict[str, Any]]:
    """Fetch and parse a single RSS feed asynchronously."""
    articles = []
    try:
        # 1. Try fetching with httpx
        resp = await client.get(url, headers=HEADERS, timeout=20.0, follow_redirects=True)
        
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
        elif resp.status_code == 403:
            # 2. Fallback to feedparser direct (some servers whitelist its specific UA or lack thereof)
            # We don't log a warning yet, check if it works first
            feed = await asyncio.to_thread(feedparser.parse, url)
        else:
            logger.warning(f"HTTP {resp.status_code} for {url} ({source}). Trying fallback...")
            feed = await asyncio.to_thread(feedparser.parse, url)

        # Final check if we got anything
        if not feed or not hasattr(feed, 'entries') or len(feed.entries) == 0:
            # If still nothing, and we haven't logged a failure yet, do it now
            status = getattr(feed, 'status', 200)
            if status >= 400:
                 logger.error(f"Failed to fetch {url} for {source}: Status {status}")
            return []

        for entry in feed.entries:
            title = getattr(entry, 'title', '').strip()
            link = getattr(entry, 'link', '').strip()
            if not title or not link: continue

            description = clean_html(getattr(entry, 'description', getattr(entry, 'summary', '')))
            
            published_parsed = getattr(entry, 'published_parsed', getattr(entry, 'updated_parsed', None))
            if published_parsed:
                published = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            else:
                published = datetime.now(timezone.utc)

            # 4. Skip if the article is already older than 24 hours
            if (datetime.now(timezone.utc) - published).total_seconds() > 24 * 3600:
                continue

            image_url = None
            if 'media_content' in entry and len(entry.media_content) > 0:
                image_url = entry.media_content[0].get('url')
            elif 'enclosures' in entry and len(entry.enclosures) > 0:
                image_url = entry.enclosures[0].get('href')

            articles.append({
                "title": title,
                "link": link,
                "title_hash": get_hash(title),
                "published": published,
                "source": source,
                "description": description,
                "image_url": image_url
            })
            
    except asyncio.TimeoutError:
        logger.warning(f"Timeout (20s) for {url} ({source}). Skipping.")
    except Exception as e:
        logger.error(f"Task Error for {url} ({source}): {type(e).__name__} - {str(e)}")
        
    return articles

async def save_article(article):
    """Processes an article: inserts first to claim the hash, then analyzes if new."""
    try:
        # 1. Insert the article first with ON CONFLICT DO NOTHING.
        #    This atomically prevents duplicates and avoids wasting LLM tokens.
        insert_result = await asyncio.to_thread(
            execute_returning,
            """INSERT INTO indian_news 
               (title, link, title_hash, published, source, description, image_url, 
                news_category, news_relevance, news_reason, symbols, analyzed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (title_hash) DO NOTHING
               RETURNING id""",
            (article['title'], article['link'], article['title_hash'], 
             article['published'], article['source'], article['description'], article['image_url'],
             'None', 'None', 'No analysis available.', [], False)
        )

        if not insert_result:
            return 0  # Duplicate — already existed, no LLM cost

        new_id = insert_result['id']
        filter_data = None

        # 2. Filtering Pass (Mandatory for all new articles)
        # Classifies relevance/category/symbols immediately using lightweight agent
        try:
            filter_data = await filter_indian_news(
                title=article['title'],
                description=article['description']
            )
            
            if filter_data:
                # normalize before saving to DB: store only canonical uppercase symbols, deduplicated and sorted
                from app.agents.tools import strict_resolve_symbols
                normalized_symbols = strict_resolve_symbols(filter_data.get('symbols', []))
                
                await asyncio.to_thread(
                    execute_query,
                    """UPDATE indian_news 
                       SET news_category = %s, news_relevance = %s, news_reason = %s, symbols = %s
                       WHERE id = %s""",
                    (filter_data['category'], filter_data['relevance'], filter_data['reason'], normalized_symbols, new_id)
                )

                # 2.2 Auto-Analysis Trigger (Hardened v3)
                relevance = filter_data.get('relevance', 'Noisy')
                if relevance in AUTO_ANALYZE_RELEVANCE:
                    # 1. Atomically claim 'queued' state in DB first (Scraper side)
                    # This prevents re-triggering if the scraper cycle hits the same article again
                    updated = await asyncio.to_thread(
                        execute_query,
                        "UPDATE indian_news SET analysis_status = 'queued' WHERE id = %s AND analysis_status IS NULL",
                        (new_id,)
                    )
                    
                    if updated and updated > 0:
                        # 2. Fire and Forget (Tracked)
                        task = asyncio.create_task(trigger_auto_analysis(new_id))
                        _BACKGROUND_TASKS.add(task)
                        task.add_done_callback(_BACKGROUND_TASKS.discard)
                        logger.info(f"[AUTO-ANALYZE] Queued article {new_id} ({relevance})")

        except Exception as fe:
            logger.warning(f"Filtering Agent error for {new_id}: {fe}")

        # 4. Trigger stateful event grouping AFTER insert
        try:
            category = filter_data.get('category', 'other') if filter_data else 'other'
            await asyncio.to_thread(
                process_event_grouping,
                news_id=new_id,
                title=article['title'],
                category=category,
                table_name='indian_news',
                ai_symbols=filter_data.get('symbols', []) if filter_data else []
            )
        except Exception as eg:
            logger.warning(f"Event grouping error for {new_id}: {eg}")
        
        # 5. Notify all devices of new arrival via Pusher
        # await asyncio.to_thread(trigger_news_created, new_id)

        return 1  # New article added
        
    except Exception as e:
        logger.error(f"Error processing article '{article['title'][:30]}...': {e}")
        return 0

async def run_scraper_cycle():
    start_time = time.time()
    
    # Track stats per source
    source_stats = {s: {"total": 0, "new": 0, "dup": 0} for s in FEEDS.keys()}
    
    async with httpx.AsyncClient(headers=HEADERS) as client:
        tasks = []
        for source, urls in FEEDS.items():
            for url in urls:
                tasks.append(fetch_feed_task(client, source, url))
        
        # results is a list of lists of articles
        results = await asyncio.gather(*tasks)
        
        # Flatten and group by source
        all_articles = []
        for res in results:
            all_articles.extend(res)

    if not all_articles:
        logger.info("No articles found in this cycle.")
        return

    # Parallel DB Insertion
    insert_tasks = [save_article(a) for a in all_articles]
    insert_results = await asyncio.gather(*insert_tasks)
    
    # Process results to update source_stats
    for article, res in zip(all_articles, insert_results):
        src = article['source']
        source_stats[src]["total"] += 1
        if res is not None and res > 0:
            source_stats[src]["new"] += 1
        else:
            source_stats[src]["dup"] += 1

    # Log per-source results (only if they had articles)
    for src, stats in source_stats.items():
        if stats["total"] > 0:
            logger.info(f"[{src}] New: {stats['new']}, Duplicate: {stats['dup']}, Total: {stats['total']}")
    # Final Summary
    total_new = sum(s["new"] for s in source_stats.values())
    total_dup = sum(s["dup"] for s in source_stats.values())
    total_all = sum(s["total"] for s in source_stats.values())
    duration = time.time() - start_time
    logger.info(f"===== Cycle Complete in {duration:.2f}s: {total_new} New, {total_dup} Duplicates, {total_all} Total Articles Processed =====")
    
    # Notify dashboard immediately if new content was found
    if total_new > 0:
        await notify_dashboard(total_new)

async def cleanup_old_news():
    """
    Deletes articles older than 24 hours from the database.
    Safeguard: Skips articles that have linked trade suggestions to preserve evidence/history.
    """
    try:
        # 1. Check for articles that will be SPARED due to suggestions
        spared_rows = await asyncio.to_thread(
            fetch_all,
            """SELECT COUNT(*) as cnt FROM indian_news 
               WHERE published < (NOW() - INTERVAL '24 hours')
               AND id IN (SELECT news_id FROM suggestions)"""
        )
        spared_count = spared_rows[0]['cnt'] if spared_rows else 0

        # 2. Perform the deletion (only on non-suggestion articles)
        deleted_count = await asyncio.to_thread(
            execute_query,
            """DELETE FROM indian_news 
               WHERE published < (NOW() - INTERVAL '24 hours')
               AND id NOT IN (SELECT news_id FROM suggestions)"""
        )
        
        if deleted_count > 0:
            logger.info(f"Background cleanup: Deleted {deleted_count} old articles. Spared {spared_count} articles with active suggestions.")
        elif spared_count > 0:
            logger.info(f"Background cleanup: No articles deleted, but {spared_count} articles skipped (linked to suggestions).")
            
    except Exception as e:
        logger.error(f"Cleanup Error: {e}")


async def main():
    global GLOBAL_CLIENT
    logger.info("Starting Async Indian Market Scraper (20s interval)...")
    
    # Initialize Persistent Client
    GLOBAL_CLIENT = httpx.AsyncClient(headers=HEADERS)
    
    try:
        # Run cleanup immediately on startup
        await cleanup_old_news()
        last_cleanup_time = time.time()
        
        CLEANUP_INTERVAL = 30 * 60  # 30 minutes in seconds

        while True:
            try:
                current_time = time.time()
                
                # Run cleanup every 30 minutes
                if current_time - last_cleanup_time >= CLEANUP_INTERVAL:
                    await cleanup_old_news()
                    last_cleanup_time = current_time

                await run_scraper_cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Global Loop Error: {e}")
            await asyncio.sleep(20)
    finally:
        # ══════════════════════════════════════════════════════
        #  SHUTDOWN CLEANUP
        # ══════════════════════════════════════════════════════
        logger.info("Shutting down scraper and cleaning up resources...")
        if GLOBAL_CLIENT:
            await GLOBAL_CLIENT.aclose()
        
        if _BACKGROUND_TASKS:
            logger.info(f"Waiting for {len(_BACKGROUND_TASKS)} background analysis hand-offs to finish...")
            await asyncio.gather(*_BACKGROUND_TASKS, return_exceptions=True)
        
        logger.info("Scraper shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass