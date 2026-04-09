from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
from datetime import datetime, timezone
from typing import Optional, Any, List, Dict
import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.core.agent import analyze_news, save_analysis
from app.core.db import fetch_all, fetch_one, execute_query

# Thread pool for blocking database operations
executor = ThreadPoolExecutor(max_workers=20)

app = FastAPI(title="News Website API")
SERVER_START = datetime.now(timezone.utc)

# Thread pool for blocking database operations
executor = ThreadPoolExecutor(max_workers=20)

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add response headers middleware for caching and performance
@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    # Add cache control headers for specific endpoints
    if request.url.path.startswith("/api/sources") or request.url.path.startswith("/api/indian_sources"):
        response.headers["Cache-Control"] = "public, max-age=3600"  # 1 hour cache
    elif request.url.path.startswith("/api/stats"):
        response.headers["Cache-Control"] = "public, max-age=30"  # 30 second cache
    elif request.url.path.startswith("/api/news") or request.url.path.startswith("/api/indian_news"):
        response.headers["Cache-Control"] = "public, max-age=5"  # 5 second cache
    # Don't cache analyze endpoints
    elif request.url.path.startswith("/api/analyze"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

# ===== API Endpoints =====
@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.1_fallbacks_active"}

@app.get("/api/news")
async def get_news(source: str = Query(None, description="Filter news by source name"), 
             limit: int = Query(1000, description="Max number of articles to return"),
             today_only: bool = Query(False, description="Only fetch today's news"),
             relevance: str = Query(None, description="Filter news by relevance"),
             analyzed_only: bool = Query(False, description="Only fetch analyzed news"),
             event_id: str = Query(None, description="Filter news by exact event ID"),
             offset: int = Query(0, description="Number of items to skip for pagination"),
             search: str = Query(None, description="Search in title, description, and source")):
    """Get news articles, sorted by newest first (async, non-blocking)."""
    
    query = """SELECT id, title, link, published, source, description, image_url,
        impact_score, impact_summary, affected_markets, affected_sectors, impact_duration,
        analyzed, created_at, market_mode, usd_bias, crypto_bias, trade_actions,
        execution_window, confidence, forex_pairs, affected_forex_pairs, conviction_score, volatility_regime,
        dollar_liquidity_state, position_size_percent, safe_haven_flow, research_text,
        is_new_information, tools_used, analysis_data, news_relevance, news_category,
        news_impact_level, news_reason, event_id, event_title
    FROM news WHERE 1=1"""
    params: List[Any] = []
    
    if today_only:
        today = datetime.now(timezone.utc).date()
        query += " AND DATE(published) = %s"
        params.append(today)
        
    if source and source.lower() != "all":
        query += " AND source = %s"
        params.append(source)
    
    if relevance and relevance.lower() != "all":
        query += " AND LOWER(news_relevance) = %s"
        params.append(relevance.lower())
        
    if analyzed_only:
        query += " AND analyzed = TRUE"
        
    if event_id:
        query += " AND event_id = %s"
        params.append(event_id)
    
    if search and search.strip():
        search_term = f"%{search.strip()}%"
        query += " AND (LOWER(title) ILIKE %s OR LOWER(description) ILIKE %s OR LOWER(source) ILIKE %s)"
        params.extend([search_term, search_term, search_term])
        
    query += " ORDER BY published DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    try:
        articles = fetch_all(query, params)
        # Convert datetime objects to string for JSON serialization
        for article in articles:
            if isinstance(article['published'], datetime):
                article['published'] = article['published'].isoformat()
            if isinstance(article.get('created_at'), datetime):
                article['created_at'] = article['created_at'].isoformat()

        # Enrich with prediction summaries
        try:
            article_ids = [a['id'] for a in articles if a.get('id')]
            if article_ids:
                placeholders = ','.join(['%s'] * len(article_ids))
                pred_rows = fetch_all(
                    f"""SELECT DISTINCT ON (news_id) news_id, asset, asset_display_name, direction,
                        predicted_move_pct, status, final_move_pct, mfe_pct, mae_pct,
                        finalized, expected_duration_label
                    FROM predictions
                    WHERE news_id IN ({placeholders})
                    ORDER BY news_id, id ASC""",
                    tuple(article_ids),
                )
                pred_map = {r['news_id']: r for r in pred_rows}

                count_rows = fetch_all(
                    f"""SELECT news_id, COUNT(*) as pred_count
                    FROM predictions
                    WHERE news_id IN ({placeholders})
                    GROUP BY news_id""",
                    tuple(article_ids),
                )
                count_map = {r['news_id']: r['pred_count'] for r in count_rows}

                for article in articles:
                    aid = article.get('id')
                    if aid in pred_map:
                        p = pred_map[aid]
                        article['prediction_count'] = count_map.get(aid, 0)
                        article['prediction_asset'] = p['asset']
                        article['prediction_asset_display_name'] = p['asset_display_name']
                        article['prediction_direction'] = p['direction']
                        article['prediction_status'] = p['status']
                        article['predicted_move_pct'] = float(p['predicted_move_pct']) if p['predicted_move_pct'] is not None else None
                        article['actual_final_move_pct'] = float(p['final_move_pct']) if p['final_move_pct'] is not None else None
                        article['prediction_mfe_pct'] = float(p['mfe_pct']) if p['mfe_pct'] is not None else None
                        article['prediction_finalized'] = p['finalized']
                        article['prediction_duration'] = p['expected_duration_label']
        except Exception as pred_err:
            print(f"[API] Prediction enrichment failed: {pred_err}")

        return {"status": "success", "count": len(articles), "data": articles}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/events/global")
def get_global_events():
    """Get active global events from the news."""
    query = """
    SELECT event_id, MIN(event_title) as event_title, COUNT(*) as article_count, MAX(published) as latest_update
    FROM news
    WHERE event_id IS NOT NULL AND event_id != 'GENERAL_GENERAL'
    GROUP BY event_id
    ORDER BY latest_update DESC
    LIMIT 50
    """
    try:
        events = fetch_all(query)
        for ev in events:
            if isinstance(ev['latest_update'], datetime):
                ev['latest_update'] = ev['latest_update'].isoformat()
        return {"status": "success", "data": events}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/events/india")
def get_indian_events():
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
        events = fetch_all(query)
        for ev in events:
            if isinstance(ev['latest_update'], datetime):
                ev['latest_update'] = ev['latest_update'].isoformat()
        return {"status": "success", "data": events}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/indian_news")
def get_indian_news(source: str = Query(None, description="Filter news by source name"), 
             limit: int = Query(1000, description="Max number of articles to return"),
             today_only: bool = Query(False, description="Only fetch today's news"),
             relevance: str = Query(None, description="Filter news by relevance"),
             analyzed_only: bool = Query(False, description="Only fetch analyzed news"),
             event_id: str = Query(None, description="Filter news by exact event ID"),
             offset: int = Query(0, description="Number of items to skip for pagination"),
             search: str = Query(None, description="Search in title, description, and source")):
    """Get Indian news articles, sorted by newest first."""
    
    query = """SELECT id, title, link, published, source, description, image_url,
        impact_score, impact_summary, affected_markets, affected_sectors, impact_duration,
        analyzed, created_at, market_mode, usd_bias, crypto_bias, trade_actions,
        execution_window, confidence, forex_pairs, affected_forex_pairs, conviction_score, volatility_regime,
        dollar_liquidity_state, position_size_percent, safe_haven_flow, research_text,
        is_new_information, tools_used, analysis_data, news_relevance, news_category,
        news_impact_level, news_reason, symbols,
        market_bias, signal_bucket, primary_symbol, executive_summary, event_id, event_title
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
        query += " AND LOWER(news_relevance) = %s"
        params.append(relevance.lower())
        
    if analyzed_only:
        query += " AND analyzed = TRUE"
        
    if event_id:
        query += " AND event_id = %s"
        params.append(event_id)
    
    if search and search.strip():
        search_term = f"%{search.strip()}%"
        query += " AND (LOWER(title) ILIKE %s OR LOWER(description) ILIKE %s OR LOWER(source) ILIKE %s)"
        params.extend([search_term, search_term, search_term])
        
    query += " ORDER BY published DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    try:
        articles = fetch_all(query, params)
        # Convert datetime objects to string for JSON serialization
        for article in articles:
            if isinstance(article['published'], datetime):
                article['published'] = article['published'].isoformat()
            if isinstance(article.get('created_at'), datetime):
                article['created_at'] = article['created_at'].isoformat()

        return {"status": "success", "count": len(articles), "data": articles}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/sources")
def get_sources():
    """Get list of distinct news sources available."""
    query = "SELECT DISTINCT source FROM news ORDER BY source"
    try:
         sources = fetch_all(query)
         return {"status": "success", "data": [s['source'] for s in sources]}
    except Exception as e:
         return {"status": "error", "message": str(e)}


@app.get("/api/indian_sources")
def get_indian_sources():
    """Get list of distinct Indian news sources available (with NULL check)."""
    try:
        rows = fetch_all(
            "SELECT DISTINCT source FROM indian_news WHERE source IS NOT NULL ORDER BY source"
        )
        sources = [r["source"] for r in rows if r.get("source")]
        return {"status": "success", "data": sources}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/analyze/{news_id}")
async def analyze_single_article(news_id: int):
    """Analyze a single news article by its DB id (async, non-blocking, with timeout)."""
    
    try:
        # Run blocking DB call in thread pool with 120 second timeout
        article = await run_with_timeout(
            lambda: fetch_one("SELECT id, title, published, description, affected_forex_pairs FROM news WHERE id = %s", (news_id,)),
            120
        )
        
        if not article:
            return {"status": "error", "message": "Article not found"}

        title = article["title"]
        published = str(article["published"])
        description = article.get("description", "") or ""
        existing_pairs = article.get("affected_forex_pairs", []) or []

        # Run analysis with 120 second timeout
        analysis = await run_with_timeout(
            lambda: analyze_news(title, published, description, current_news_id=news_id),
            120
        )

        if analysis:
            # Consistency check: if deep analysis didn't find new pairs, but we have old ones, merge them
            if not analysis.get("affected_forex_pairs") and existing_pairs:
                analysis["affected_forex_pairs"] = existing_pairs
            
            try:
                # Save analysis with 30 second timeout
                await run_with_timeout(
                    lambda: save_analysis(news_id, analysis),
                    30
                )
                print(f"[API] Analysis saved for news_id={news_id}, score={analysis.get('impact_score')}")
                return {"status": "success", "data": analysis}
            except TimeoutError:
                print(f"[API] save_analysis TIMEOUT for news_id={news_id}")
                return {"status": "error", "message": "Analysis completed but save timed out"}
            except Exception as save_err:
                print(f"[API] save_analysis FAILED for news_id={news_id}: {save_err}")
                import traceback
                traceback.print_exc()
                return {"status": "error", "message": f"Save failed: {save_err}"}
        else:
            print(f"[API] analyze_news returned None for news_id={news_id}")
            return {"status": "error", "message": "Analysis failed — click to retry"}
    except TimeoutError as te:
        print(f"[API] TIMEOUT in analyze endpoint for news_id={news_id}: {te}")
        return {"status": "error", "message": "Analysis timeout - took too long (2 min limit)"}
    except asyncio.CancelledError:
        print(f"[API] Analysis cancelled for news_id={news_id}")
        return {"status": "error", "message": "Analysis was cancelled"}
    except Exception as e:
        print(f"[API] Exception in analyze endpoint: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.get("/api/stats")
async def get_stats():
    """Get dashboard statistics for the footer (with caching)."""
    try:
        row = await run_with_timeout(
            lambda: fetch_one(
                "SELECT COUNT(*) as total, "
                "COUNT(CASE WHEN analyzed = true THEN 1 END) as analyzed, "
                "COUNT(DISTINCT source) as sources "
                "FROM news"
            ),
            10  # 10 second timeout for stats
        )
        uptime_seconds = int((datetime.now(timezone.utc) - SERVER_START).total_seconds())
        return {
            "status": "success",
            "data": {
                "total_articles": row["total"] if row else 0,
                "analyzed_articles": row["analyzed"] if row else 0,
                "source_count": row["sources"] if row else 0,
                "uptime_seconds": uptime_seconds
            },
            "cache-control": "max-age=30"  # Cache for 30 seconds
        }
    except TimeoutError:
        return {"status": "error", "message": "Stats query timeout"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/predictions")
def get_predictions(news_id: Optional[int] = Query(None), limit: Optional[int] = Query(1000)):
    """List predictions, optionally filtered by news_id."""
    try:
        rows: List[Dict[str, Any]] = []
        if news_id:
            raw_rows = fetch_all(
                """SELECT p.*, n.title as news_title, n.link as news_link
                FROM predictions p
                LEFT JOIN news n ON n.id = p.news_id
                WHERE p.news_id = %s
                ORDER BY p.created_at DESC""",
                (news_id,),
            )
        else:
            raw_rows = fetch_all(
                """SELECT p.*, n.title as news_title, n.link as news_link
                FROM predictions p
                LEFT JOIN news n ON n.id = p.news_id
                ORDER BY p.created_at DESC
                LIMIT %s""",
                (limit,),
            )
        rows = [dict(r) for r in raw_rows]
        for r in rows:
            for k in ('start_time', 'last_checked_at', 'finalized_at', 'created_at'):
                if isinstance(r.get(k), datetime):
                    r[k] = r[k].isoformat()
            for k in ('predicted_move_pct', 'start_price', 'target_price',
                       'last_price', 'last_move_pct', 'mfe_pct', 'mae_pct',
                       'final_price', 'final_move_pct'):
                if r.get(k) is not None:
                    r[k] = float(r[k])
        return {"status": "success", "count": len(rows), "data": rows}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/prediction-stats")
def get_prediction_stats():
    """Aggregate prediction statistics."""
    try:
        row = fetch_one(
            """SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN finalized = true THEN 1 END) as finalized,
                COUNT(CASE WHEN status = 'hit' THEN 1 END) as hit,
                COUNT(CASE WHEN status = 'overperformed' THEN 1 END) as overperformed,
                COUNT(CASE WHEN status = 'underperformed' THEN 1 END) as underperformed,
                COUNT(CASE WHEN status = 'wrong' THEN 1 END) as wrong,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'error' THEN 1 END) as errors,
                COALESCE(AVG(CASE WHEN finalized THEN final_move_pct END), 0) as avg_final_move,
                COALESCE(AVG(CASE WHEN finalized THEN mfe_pct END), 0) as avg_mfe,
                COALESCE(AVG(CASE WHEN finalized THEN mae_pct END), 0) as avg_mae
            FROM predictions"""
        )
        if not row:
            return {"status": "success", "data": {}}

        total_finalized = float(row['finalized'] or 0)
        hit_count = float((row['hit'] or 0) + (row['overperformed'] or 0))
        hit_rate = float(f"{(hit_count / total_finalized * 100.0):.1f}") if total_finalized > 0 else 0.0

        return {
            "status": "success",
            "data": {
                "total": row['total'],
                "pending": row['pending'],
                "finalized": total_finalized,
                "hit": row['hit'],
                "overperformed": row['overperformed'],
                "underperformed": row['underperformed'],
                "wrong": row['wrong'],
                "errors": row['errors'],
                "hit_rate": hit_rate,
                "avg_final_move_pct": float(f"{float(row['avg_final_move'] if row.get('avg_final_move') is not None else 0):.2f}"),
                "avg_mfe_pct": float(f"{float(row['avg_mfe'] if row.get('avg_mfe') is not None else 0):.2f}"),
                "avg_mae_pct": float(f"{float(row['avg_mae'] if row.get('avg_mae') is not None else 0):.2f}"),
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---- FOREX LIVE CHART API ----

@app.get("/api/forex/pairs")
def get_forex_pairs(q: str = Query("", description="Search query")):
    """Return the list of available forex pairs (only those with candle data)."""
    try:
        if q:
            # Only return symbols that actually have 3m candles
            rows = fetch_all(
                "SELECT DISTINCT symbol FROM forex_candles_3m WHERE symbol ILIKE %s ORDER BY symbol LIMIT 50",
                (f"%{q}%",)
            )
        else:
            rows = fetch_all("SELECT DISTINCT symbol FROM forex_candles_3m ORDER BY symbol LIMIT 100")
        
        return {"status": "success", "data": [r["symbol"] for r in rows]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/forex/candles")
def get_forex_candles(symbol: str = Query(..., description="Symbol e.g. OANDA:EURUSD"), limit: int = Query(200)):
    """Return latest 3-minute candles for a symbol, newest first.
    If the provided symbol has no data, tries common prefixes (OANDA, FX_IDC, etc).
    """
    debug_log_path = os.path.join(os.getcwd(), "tmp", "candle_debug.log")
    os.makedirs(os.path.dirname(debug_log_path), exist_ok=True)
    
    with open(debug_log_path, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] REQ: symbol={symbol}, limit={limit}\n")

    try:
        # First attempt: Exact match
        rows = fetch_all(
            """SELECT time, open, high, low, close
            FROM forex_candles_3m
            WHERE symbol = %s
            ORDER BY time DESC
            LIMIT %s""",
            (symbol, limit)
        )

        # Fallback if no data: try prefixes
        if not rows:
            with open(debug_log_path, "a") as f:
                f.write(f"[{datetime.now().isoformat()}] FALLBACK START for {symbol}\n")

            #Normalize: "USD/INR" -> "USDINR"
            clean_base = symbol.split(':')[-1].replace("/", "").replace("_", "").replace(" ", "").upper()
            
            prefixes = ["OANDA:", "FX_IDC:", "FXCM:", "FOREXCOM:", "ICE:"]
            # To be thorough, also try with NO prefix (in case it was passed with OANDA: but stored as FX_IDC:)
            search_symbols = [clean_base] + [f"{pref}{clean_base}" for pref in prefixes]
            
            for candidate in search_symbols:
                if candidate == symbol: continue # Skip what we already tried
                
                with open(debug_log_path, "a") as f:
                    f.write(f"[{datetime.now().isoformat()}] Trying candidate: {candidate}\n")

                rows = fetch_all(
                    """SELECT time, open, high, low, close
                    FROM forex_candles_3m
                    WHERE symbol = %s
                    ORDER BY time DESC
                    LIMIT %s""",
                    (candidate, limit)
                )
                if rows:
                    with open(debug_log_path, "a") as f:
                        f.write(f"[{datetime.now().isoformat()}] SUCCESS with {candidate}\n")
                    print(f"[API] Found fallback symbol: {candidate} for requested: {symbol}")
                    symbol = candidate
                    break
            
            # FINAL FUZZY FALLBACK: Try a LIKE search if prefixes fail
            if not rows:
                with open(debug_log_path, "a") as f:
                    f.write(f"[{datetime.now().isoformat()}] Trying final fuzzy LIKE %{clean_base}%\n")
                
                fuzzy_row = fetch_one(
                    "SELECT symbol FROM forex_candles_3m WHERE symbol ILIKE %s LIMIT 1",
                    (f"%{clean_base}%",)
                )
                if fuzzy_row:
                    candidate = fuzzy_row["symbol"]
                    with open(debug_log_path, "a") as f:
                        f.write(f"[{datetime.now().isoformat()}] FUZZY SUCCESS with {candidate}\n")
                    rows = fetch_all(
                        """SELECT time, open, high, low, close
                        FROM forex_candles_3m
                        WHERE symbol = %s
                        ORDER BY time DESC
                        LIMIT %s""",
                        (candidate, limit)
                    )
                    symbol = candidate

            if not rows:
                with open(debug_log_path, "a") as f:
                    f.write(f"[{datetime.now().isoformat()}] STILL NO DATA after all candidates.\n")

        data = []
        for r in rows:
            # Ensure time is aware UTC before isoformat()
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


@app.get("/api/forex/news-markers")
def get_forex_news_markers(symbol: Optional[str] = Query(None, description="Filter by forex pair (e.g., EURUSD)")):
    """Return news articles with their affected forex pairs for chart overlay.
    
    If symbol is provided, returns news that affect that specific pair.
    Handles both 'EURUSD' and 'EUR/USD' formats for robust matching.
    """
    try:
        if symbol:
            # Clean symbol (e.g., 'OANDA:EURUSD' -> 'EURUSD')
            clean_symbol = symbol.split(':')[-1].upper()
            
            # Create variations: 'EURUSD' and 'EUR/USD'
            variations = [clean_symbol]
            if len(clean_symbol) == 6:
                variations.append(f"{clean_symbol[:3]}/{clean_symbol[3:]}")
            
            # Filter news that contain any of these variations in affected_forex_pairs JSON array
            query = """
            SELECT id, title, published, affected_forex_pairs
            FROM news
            WHERE affected_forex_pairs IS NOT NULL 
              AND affected_forex_pairs::text != '[]'
              AND (
                affected_forex_pairs @> %s::jsonb
                OR affected_forex_pairs @> %s::jsonb
              )
            ORDER BY published DESC
            LIMIT 500
            """
            
            p1 = json.dumps([variations[0]])
            p2 = json.dumps([variations[1]] if len(variations) > 1 else [variations[0]])
            
            rows = fetch_all(query, (p1, p2))
        else:
            # Return all news with affected forex pairs
            query = """
            SELECT id, title, published, affected_forex_pairs
            FROM news
            WHERE affected_forex_pairs IS NOT NULL 
              AND affected_forex_pairs::text != '[]'
            ORDER BY published DESC
            LIMIT 500
            """
            rows = fetch_all(query)
        
        data = []
        for r in rows:
            # Handle different JSON types (array of objects or array of strings)
            affected_pairs = r.get("affected_forex_pairs", [])
            if isinstance(affected_pairs, str):
                try:
                    affected_pairs = json.loads(affected_pairs)
                except json.JSONDecodeError:
                    affected_pairs = []            
            # Ensure published is aware UTC/offset before isoformat()
            p = r["published"]
            if hasattr(p, "isoformat"):
                if p.tzinfo is None:
                    # News should ideally have offset, but fallback to UTC if naive
                    p = p.replace(tzinfo=timezone.utc)
                p_str = p.isoformat()
            else:
                p_str = str(p)
                
            data.append({
                "id": r["id"],
                "title": r["title"],
                "published": p_str,
                "affected_forex_pairs": affected_pairs if isinstance(affected_pairs, list) else []
            })
        
        return {"status": "success", "symbol": symbol, "count": len(data), "data": data}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.get("/api/nse/holidays")
def get_nse_holidays():
    """Return the list of NSE holidays fetched dynamically from NSE."""
    try:
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

        return {"status": "success", "data": holidays}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---- NSE LIVE CHART API ----

@app.get("/api/nse/pairs")
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

@app.get("/api/nse/candles")
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

@app.get("/api/nse/news-markers")
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




try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    current_dir = os.getcwd()

# API Routes for News Feed

# @app.get("/api/indian_news")
# def get_indian_news(source: str = Query(None), limit: int = Query(1000),
#                     today_only: bool = Query(False), relevance: str = Query(None),
#                     analyzed_only: bool = Query(False)):
#     """Get Indian news articles, sorted by newest first."""
#     query = """SELECT id, title, link, published, source, description, image_url,
#         impact_score, impact_summary, analyzed, created_at, analysis_data,
#         news_relevance, news_category, market_bias, signal_bucket,
#         primary_symbol, executive_summary, analyzed_at
#     FROM indian_news WHERE 1=1"""
#     params: List[Any] = []

#     if today_only:
#         today = datetime.now(timezone.utc).date()
#         query += " AND DATE(published) = %s"
#         params.append(today)

#     if source and source.lower() != "all":
#         query += " AND source = %s"
#         params.append(source)

#     if relevance and relevance.lower() != "all":
#         query += " AND LOWER(news_relevance) = %s"
#         params.append(relevance.lower())

#     if analyzed_only:
#         query += " AND analyzed = TRUE"

#     query += " ORDER BY published DESC LIMIT %s"
#     params.append(limit)

#     try:
#         articles = fetch_all(query, params)
#         for article in articles:
#             if isinstance(article.get('published'), datetime):
#                 article['published'] = article['published'].isoformat()
#             if isinstance(article.get('created_at'), datetime):
#                 article['created_at'] = article['created_at'].isoformat()
#             if isinstance(article.get('analyzed_at'), datetime):
#                 article['analyzed_at'] = article['analyzed_at'].isoformat()

#         return {"status": "success", "data": articles}
#     except Exception as e:
#         import traceback
#         traceback.print_exc()
#         return {"status": "error", "message": str(e)}


# @app.get("/api/indian_sources")
# def get_indian_sources():
#     """Get a list of distinct sources from indian_news."""
#     try:
#         rows = fetch_all(
#             "SELECT DISTINCT source FROM indian_news WHERE source IS NOT NULL ORDER BY source"
#         )
#         sources = [r["source"] for r in rows if r.get("source")]
#         return {"status": "success", "data": sources}
#     except Exception as e:
#         return {"status": "error", "message": str(e)}


@app.get("/api/indian_stats")
def get_indian_stats():
    """Get dashboard statistics for the footer, for Indian news."""
    try:
        row = fetch_one(
            "SELECT COUNT(*) as total, "
            "COUNT(CASE WHEN analyzed = true THEN 1 END) as analyzed, "
            "COUNT(DISTINCT source) as sources "
            "FROM indian_news"
        )
        uptime_seconds = int((datetime.now(timezone.utc) - SERVER_START).total_seconds())
        return {
            "status": "success",
            "data": {
                "total_articles": row["total"] if row else 0,
                "analyzed_articles": row["analyzed"] if row else 0,
                "source_count": row["sources"] if row else 0,
                "uptime_seconds": uptime_seconds
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/indian_analyze/{news_id}")
async def analyze_single_indian_article(news_id: int):
    """Analyze a single Indian news article by its DB id using the Indian Agent (async, non-blocking, with timeout)."""
    from app.ind.agent import analyze_indian_news, save_indian_analysis
    
    try:
        # Run blocking DB call in thread pool with 120 second timeout
        article = await run_with_timeout(
            lambda: fetch_one("SELECT id, title, published, description, source FROM indian_news WHERE id = %s", (news_id,)),
            120
        )
        if not article:
            return {"status": "error", "message": "Indian Article not found"}

        title = article["title"]
        published = str(article["published"])
        description = article.get("description", "") or ""
        source = article.get("source", "") or ""

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
                print(f"[API] Indian Analysis saved for news_id={news_id}")
                
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
                        return {"status": "success", "data": analysis, "article": dict(updated_row)}
                except Exception as row_err:
                    print(f"[API] Re-fetch after save failed (non-critical): {row_err}")
                
                return {"status": "success", "data": analysis}
            except TimeoutError:
                print(f"[API] save_indian_analysis TIMEOUT for news_id={news_id}")
                return {"status": "error", "message": "Analysis completed but save timed out"}
            except Exception as save_err:
                print(f"[API] save_indian_analysis FAILED for news_id={news_id}: {save_err}")
                import traceback
                traceback.print_exc()
                return {"status": "error", "message": f"Save failed: {save_err}"}
        else:
            print(f"[API] analyze_indian_news returned None for news_id={news_id}")
            return {"status": "error", "message": "Analysis failed — click to retry"}
    except TimeoutError as te:
        print(f"[API] TIMEOUT in indian_analyze endpoint for news_id={news_id}: {te}")
        return {"status": "error", "message": "Analysis timeout - took too long (2 min limit)"}
    except asyncio.CancelledError:
        print(f"[API] Indian Analysis cancelled for news_id={news_id}")
        return {"status": "error", "message": "Analysis was cancelled"}
    except Exception as e:
        print(f"[API] Exception in indian_analyze endpoint: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

# API server entry point
    
if __name__ == "__main__":
    print("Starting API Server on http://localhost:8000")
    uvicorn.run("server:app", host="localhost", port=8000, reload=True)
