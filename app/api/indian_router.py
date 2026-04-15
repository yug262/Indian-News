from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, Header, HTTPException, status
from datetime import datetime, timezone
from typing import Optional, Any, List
import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.db.db import fetch_all, fetch_one, execute_returning, execute_query
from app.agents.agent import analyze_indian_news, save_indian_analysis

# Thread pool for blocking database operations
executor = ThreadPoolExecutor(max_workers=40)

router = APIRouter()
SERVER_START = datetime.now(timezone.utc)

# ===== WebSocket Connection Manager =====
class ConnectionManager:
    """Manages WebSocket connections for real-time multi-user updates."""
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        print(f"[WS] Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        print(f"[WS] Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Send a JSON message to all connected clients."""
        if not self.active_connections:
            return
        data = json.dumps(message, default=str)
        stale = []
        # Snapshot the connections to avoid "set changed size during iteration" errors
        for connection in list(self.active_connections):
            try:
                await connection.send_text(data)
            except Exception:
                stale.append(connection)
        for conn in stale:
            self.disconnect(conn)

ws_manager = ConnectionManager()

@router.post("/api/internal/new_articles")
async def notify_new_articles(
    count: int = 1, 
    x_internal_token: Optional[str] = Header(None)
):
    """
    Internal endpoint called by scrapers to notify the dashboard 
    of new articles immediately via WebSocket.
    """
    # Simple security check to ensure only our scrapers can trigger global refreshes
    if x_internal_token != "super-secret-sync-token": # Replace with os.getenv in production
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal token"
        )

    await ws_manager.broadcast({"type": "new_articles", "count": count})
    return {"status": "success", "notified_clients": len(ws_manager.active_connections)}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for real-time dashboard updates.
    Clients connect here and receive broadcast messages (new_articles, article_updated, etc.)
    """
    await ws_manager.connect(websocket)
    try:
        # Send a welcome heartbeat so the client knows the connection is live
        await websocket.send_text('{"type":"connected"}')
        # Keep the connection alive — wait for client messages (or disconnection)
        while True:
            # We don't currently need to receive messages from the frontend,
            # but we must await something to detect disconnection.
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# Async helpers to run blocking DB operations
async def run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args))

# Async wrapper with timeout
async def run_with_timeout(func, timeout_sec, *args):
    try:
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(executor, lambda: func(*args))
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Operation timed out after {timeout_sec} seconds")

# ===== Server-Side Stats Cache (avoids hammering COUNT(*) on every poll) =====
import time as _time
_stats_cache = {"global": {"data": None, "ts": 0}, "indian": {"data": None, "ts": 0}}
_STATS_CACHE_TTL = 15  # seconds — shared cache across all users

# ===== Analysis Concurrency Limiter =====
# Prevents thread pool exhaustion: max 5 analyses running at the same time
_analysis_semaphore = asyncio.Semaphore(5)

async def cleanup_stale_analyses():
    """Startup task to reset any articles stuck in 'processing' state."""
    try:
        # Reset articles stuck for more than 5 minutes
        stale_count = await run_in_executor(
            execute_query,
            """UPDATE indian_news 
               SET analysis_status = 'failed', 
                   analysis_error = 'Staleness timeout: article was stuck in processing for too long (likely server crash).'
               WHERE analysis_status = 'processing' 
               AND analysis_started_at < (NOW() - INTERVAL '5 minutes')"""
        )
        if stale_count:
            print(f"[CLEANUP] Reset {stale_count} stale analyses to failed.")
    except Exception as e:
        print(f"[CLEANUP] Failed to reset stale analyses: {e}")

@router.on_event("startup")
async def on_startup():
    asyncio.create_task(cleanup_stale_analyses())



# ===== API Endpoints =====
@router.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.1_fallbacks_active"}

@router.get("/api/events/india")
async def get_indian_events():
    """Get active Indian market events from the indian_news."""
    query = """
    SELECT event_id, MIN(event_title) as event_title, COUNT(*) as article_count, MAX(published) as latest_update
    FROM indian_news
    WHERE event_id IS NOT NULL AND event_id != 'GENERAL_GENERAL'
    GROUP BY event_id
    ORDER BY latest_update DESC
    LIMIT 50
    """
    try:
        events = await run_with_timeout(lambda: fetch_all(query), 10)
        for ev in events:
            if isinstance(ev['latest_update'], datetime):
                ev['latest_update'] = ev['latest_update'].isoformat()
        return {"status": "success", "data": events}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _escape_ilike(s: str) -> str:
    """Escape percent and underscore signs for SQL ILIKE patterns."""
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

@router.get("/api/indian_news")
async def get_indian_news(source: str = Query(None, description="Filter news by source name"), 
             limit: int = Query(20, description="Max number of articles to return"),
             today_only: bool = Query(False, description="Only fetch today's news"),
             relevance: str = Query(None, description="Filter news by relevance"),
             exclude_noisy: bool = Query(False, description="Exclude articles with relevance 'noisy'"),
             analyzed_only: bool = Query(False, description="Only fetch analyzed news"),
             event_id: str = Query(None, description="Filter news by exact event ID"),
             offset: int = Query(0, description="Number of items to skip for pagination"),
             search: str = Query(None, description="Search in title, description, and source")):
    """Get Indian news articles, sorted by newest first."""
    
    query = """SELECT id, title, link, published, source, description, image_url,
        impact_score, impact_summary, analyzed, created_at,
        analysis_data, news_relevance, news_category,
        news_impact_level, news_reason, symbols,
        market_bias, signal_bucket, primary_symbol, executive_summary, 
        event_id, event_title, analysis_confidence AS confidence, horizon
    FROM indian_news WHERE 1=1"""
    params: List[Any] = []
    
    if today_only:
        today = datetime.now(timezone.utc).date()
        query += " AND DATE(published) = %s"
        params.append(today)
        
    if source and source.lower() != "all":
        query += " AND source = %s"
        params.append(source)
    
    if relevance and relevance.lower() != "all":
        query += " AND news_relevance ILIKE %s"
        params.append(relevance)
    elif exclude_noisy:
        query += " AND (news_relevance IS NULL OR LOWER(news_relevance) != 'noisy')"
        
    if analyzed_only:
        query += " AND analyzed = TRUE"
        
    if event_id:
        query += " AND event_id = %s"
        params.append(event_id)
    
    if search and search.strip():
        search_term = f"%{_escape_ilike(search.strip())}%"
        query += " AND (title ILIKE %s ESCAPE '\\' OR description ILIKE %s ESCAPE '\\' OR source ILIKE %s ESCAPE '\\')"
        params.extend([search_term, search_term, search_term])
        
    query += " ORDER BY published DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    try:
        articles = await run_with_timeout(lambda: fetch_all(query, params), 20)
        # Convert datetime objects to string for JSON serialization
        for article in articles:
            if isinstance(article['published'], datetime):
                article['published'] = article['published'].isoformat()
            if isinstance(article.get('created_at'), datetime):
                article['created_at'] = article['created_at'].isoformat()

        return {"status": "success", "count": len(articles), "data": articles}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/api/indian_sources")
async def get_indian_sources():
    """Get list of distinct Indian news sources available (with NULL check)."""
    try:
        rows = await run_with_timeout(
            lambda: fetch_all(
                "SELECT DISTINCT source FROM indian_news WHERE source IS NOT NULL ORDER BY source"
            ),
            10
        )
        sources = [r["source"] for r in rows if r.get("source")]
        return {"status": "success", "data": sources}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _fetch_nse_holidays():
    import requests
    from datetime import datetime
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.nseindia.com/resources/exchange-trading-holidays",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=5)
    res = session.get("https://www.nseindia.com/api/holiday-master?type=trading", headers=headers, timeout=5)
    
    holidays = {}
    if res.status_code == 200:
        data = res.json()
        for segment in ["CM", "EQUITY"]:
            if segment in data:
                for item in data[segment]:
                    try:
                        dt = datetime.strptime(item["tradingDate"], "%d-%b-%Y")
                        holidays[dt.strftime("%Y-%m-%d")] = item["description"]
                    except: continue
                break
    
    if not holidays:
        # Simple fallback for 2026 if API fails
        holidays = { "2026-03-31": "Shri Mahavir Jayanti" } 
    return holidays

@router.get("/api/nse/holidays")
async def get_nse_holidays():
    """Return the list of NSE holidays fetched dynamically from NSE."""
    try:
        holidays = await run_with_timeout(_fetch_nse_holidays, 15)
        return {"status": "success", "data": holidays}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---- NSE LIVE CHART API ----

@router.get("/api/nse/pairs")
def get_nse_pairs(q: str = Query("", description="Search query")):
    """Return the list of available NSE pairs (only those with candle data)."""
    try:
        if q:
            rows = fetch_all(
                "SELECT DISTINCT symbol FROM nse_candles_3m WHERE symbol ILIKE %s ORDER BY symbol LIMIT 50",
                (f"%{q}%",)
            )
        else:
            rows = fetch_all("SELECT DISTINCT symbol FROM nse_candles_3m ORDER BY symbol LIMIT 100")
        
        return {"status": "success", "data": [r["symbol"] for r in rows]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/api/nse/candles")
def get_nse_candles(symbol: str = Query(..., description="Symbol e.g. TCS"), limit: int = Query(200)):
    """Return latest 3-minute candles for an NSE symbol, newest first."""
    try:
        clean_symbol = symbol.replace("NSE:", "").upper()
        
        rows = fetch_all(
            """SELECT time, open, high, low, close
            FROM nse_candles_3m
            WHERE symbol = %s
            ORDER BY time DESC
            LIMIT %s""",
            (clean_symbol, limit)
        )

        # fuzzy fallback
        if not rows:
            fuzzy_row = fetch_one(
                "SELECT symbol FROM nse_candles_3m WHERE symbol ILIKE %s LIMIT 1",
                (f"%{clean_symbol}%",)
            )
            if fuzzy_row:
                candidate = fuzzy_row["symbol"]
                rows = fetch_all(
                    """SELECT time, open, high, low, close
                    FROM nse_candles_3m
                    WHERE symbol = %s
                    ORDER BY time DESC
                    LIMIT %s""",
                    (candidate, limit)
                )
                symbol = candidate # fallback used

        data = []
        for r in rows:
            t = r["time"]
            if hasattr(t, "isoformat"):
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                t_str = t.isoformat()
            else:
                t_str = str(t)
                
            data.append({
                "time": t_str,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            })
        return {"status": "success", "symbol": symbol, "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/api/nse/news-markers")
def get_nse_news_markers(symbol: Optional[str] = Query(None, description="Filter by NSE pair (e.g., TCS)")):
    """Return Indian news articles with their affected NSE stocks for chart overlay."""
    try:
        if symbol:
            clean_symbol = symbol.replace("NSE:", "").upper()
            query = """
            SELECT id, title, published, symbols
            FROM indian_news
            WHERE symbols IS NOT NULL 
              AND array_length(symbols, 1) > 0
              AND (
                symbols @> ARRAY[%s]
              )
            ORDER BY published DESC
            LIMIT 500
            """
            rows = fetch_all(query, (clean_symbol,))
        else:
            query = """
            SELECT id, title, published, symbols
            FROM indian_news
            WHERE symbols IS NOT NULL 
              AND array_length(symbols, 1) > 0
            ORDER BY published DESC
            LIMIT 500
            """
            rows = fetch_all(query)
        
        data = []
        for r in rows:
            # Column in DB is 'symbols' (text[])
            syms = r.get("symbols", [])
            
            p = r["published"]
            if hasattr(p, "isoformat"):
                if p.tzinfo is None:
                    p = p.replace(tzinfo=timezone.utc)
                p_str = p.isoformat()
            else:
                p_str = str(p)
                
            data.append({
                "id": r["id"],
                "title": r["title"],
                "published": p_str,
                "affected_stocks": syms if isinstance(syms, list) else []
            })
        
        return {"status": "success", "symbol": symbol, "count": len(data), "data": data}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}




@router.get("/api/indian_stats")
async def get_indian_stats():
    """Get dashboard statistics for the footer, for Indian news (with server-side caching)."""
    now = _time.time()
    cached = _stats_cache["indian"]
    if cached["data"] and (now - cached["ts"]) < _STATS_CACHE_TTL:
        return cached["data"]
    
    try:
        row = await run_with_timeout(
            lambda: fetch_one(
                "SELECT COUNT(*) as total, "
                "COUNT(CASE WHEN analyzed = true THEN 1 END) as analyzed, "
                "COUNT(DISTINCT source) as sources "
                "FROM indian_news"
            ),
            10
        )
        uptime_seconds = int((datetime.now(timezone.utc) - SERVER_START).total_seconds())
        result = {
            "status": "success",
            "data": {
                "total_articles": row["total"] if row else 0,
                "analyzed_articles": row["analyzed"] if row else 0,
                "source_count": row["sources"] if row else 0,
                "uptime_seconds": uptime_seconds
            }
        }
        _stats_cache["indian"] = {"data": result, "ts": now}
        return result
    except TimeoutError:
        if cached["data"]:
            return cached["data"]
        return {"status": "error", "message": "Stats query timeout"}
    except Exception as e:
        if cached["data"]:
            return cached["data"]
        return {"status": "error", "message": str(e)}

@router.post("/api/indian_analyze/{news_id}")
async def analyze_single_indian_article(news_id: int):
    """Analyze a single Indian news article by its DB id using the Indian Agent (async, non-blocking, with timeout)."""
    # Enforce concurrency limit — prevent thread pool exhaustion under heavy load
    try:
        await asyncio.wait_for(_analysis_semaphore.acquire(), timeout=0.1)
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Server is busy analyzing other articles. Please try again in a moment."}
    
    try:
        # 1. ATOMIC CLAIM (Hardened v3)
        # Atomsically transition from queued/failed/None -> processing. 
        # This prevents race conditions between multiple triggers.
        claim_row = await run_in_executor(
            execute_returning,
            """UPDATE indian_news 
               SET analysis_status = 'processing', 
                   analysis_started_at = NOW(),
                   analysis_error = NULL
               WHERE id = %s AND (analysis_status IS NULL OR analysis_status IN ('queued', 'failed'))
               RETURNING id, title, published, description, source""",
            (news_id,)
        )
        
        if not claim_row:
            # Check why it failed - was it already processed?
            existing = await run_in_executor(
                fetch_one,
                "SELECT id, analysis_status FROM indian_news WHERE id = %s",
                (news_id,)
            )
            if not existing:
                return {"status": "error", "message": "Indian Article not found"}
            
            status = existing.get('analysis_status')
            if status == 'processing':
                return {"status": "success", "message": "Analysis already in progress."}
            if status == 'completed':
                return {"status": "success", "message": "Analysis already completed."}
            
            return {"status": "error", "message": f"Could not claim article for analysis (current state: {status})"}

        article = claim_row

        title = article["title"]
        published = str(article["published"])
        description = article.get("description", "") or ""
        source = article.get("source", "") or ""

        # === START OF ANALYSIS BLOCK ===
        trimmed_title = title[:100] + ("..." if len(title) > 100 else "")
        print(f"\n[INDIA ANALYSIS START] news_id={news_id}")
        print(f"[TITLE] {trimmed_title}")

        # Run analysis with 120 second timeout
        analysis = await run_with_timeout(
            lambda: analyze_indian_news(
                title=title, 
                published_iso=published, 
                summary=description, 
                source=source,
                current_news_id=news_id
            ),
            120
        )

        if analysis:
            try:
                # Save analysis with 30 second timeout
                await run_with_timeout(
                    lambda: save_indian_analysis(news_id, analysis),
                    30
                )
                
                # Update status to completed (Hardened v3)
                await run_in_executor(
                    execute_query,
                    "UPDATE indian_news SET analysis_status = 'completed', analysis_completed_at = NOW() WHERE id = %s",
                    (news_id,)
                )
                print(f"[API] Indian Analysis saved for news_id={news_id}")
                print(f"[INDIA ANALYSIS END] news_id={news_id}")
                
                # Re-fetch the full updated article from DB so frontend gets flat fields
                try:
                    updated_row = await run_with_timeout(
                        lambda: fetch_one(
                            """SELECT id, title, link, published, source, description, image_url,
                                impact_score, impact_summary, analyzed, created_at,
                                market_bias, signal_bucket, news_category, news_relevance,
                                primary_symbol, executive_summary, analysis_data, symbols,
                                event_id, event_title
                            FROM indian_news WHERE id = %s""", (news_id,)
                        ),
                        10
                    )
                    if updated_row:
                        # Convert datetime objects for JSON serialization
                        if isinstance(updated_row.get('published'), datetime):
                            updated_row['published'] = updated_row['published'].isoformat()
                        if isinstance(updated_row.get('created_at'), datetime):
                            updated_row['created_at'] = updated_row['created_at'].isoformat()
                        row_dict = dict(updated_row)
                        # Broadcast to all connected WebSocket clients
                        await ws_manager.broadcast({"type": "article_updated", "scope": "indian", "article": row_dict})
                        return {"status": "success", "data": analysis, "article": row_dict}
                except Exception as row_err:
                    print(f"[API] Re-fetch after save failed (non-critical): {row_err}")
                
                return {"status": "success", "data": analysis}
            except TimeoutError:
                print(f"[API] save_indian_analysis TIMEOUT for news_id={news_id}")
                print(f"[INDIA ANALYSIS END] news_id={news_id}")
                return {"status": "error", "message": "Analysis completed but save timed out"}
            except Exception as save_err:
                print(f"[API] save_indian_analysis FAILED for news_id={news_id}: {save_err}")
                print(f"[INDIA ANALYSIS END] news_id={news_id}")
                import traceback
                traceback.print_exc()
                return {"status": "error", "message": f"Save failed: {save_err}"}
        else:
            print(f"[API] analyze_indian_news returned None for news_id={news_id}")
            print(f"[INDIA ANALYSIS END] news_id={news_id}")
            return {"status": "error", "message": "Analysis failed — click to retry"}
    except TimeoutError as te:
        print(f"[API] TIMEOUT in indian_analyze endpoint for news_id={news_id}: {te}")
        print(f"[INDIA ANALYSIS END] news_id={news_id}")
        return {"status": "error", "message": "Analysis timeout - took too long (2 min limit)"}
    except asyncio.CancelledError:
        print(f"[API] Indian Analysis cancelled for news_id={news_id}")
        print(f"[INDIA ANALYSIS END] news_id={news_id}")
        return {"status": "error", "message": "Analysis was cancelled"}
    except Exception as e:
        print(f"[API] Exception in indian_analyze endpoint: {e}")
        print(f"[INDIA ANALYSIS END] news_id={news_id}")
        # Record failure state (Hardened v3)
        try:
            await run_in_executor(
                execute_query,
                "UPDATE indian_news SET analysis_status = 'failed', analysis_error = %s WHERE id = %s",
                (str(e), news_id)
            )
        except: pass
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    finally:
        _analysis_semaphore.release()

# ===== WebSocket Endpoint =====
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time multi-user dashboard updates.
    
    Clients connect here to receive instant notifications when:
    - An article is analyzed (article_updated)
    - New articles arrive (new_articles)
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; listen for client pings
            data = await websocket.receive_text()
            # Respond to ping with pong
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)