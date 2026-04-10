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
from app.core.db import fetch_all, fetch_one, execute_query

# Thread pool for blocking database operations
executor = ThreadPoolExecutor(max_workers=20)

app = FastAPI(title="News Website API")
from app.api.indian_router import router as indian_router
app.include_router(indian_router)
SERVER_START = datetime.now(timezone.utc)

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

@app.get("/api/predictions")
def get_predictions(news_id: Optional[int] = Query(None), limit: Optional[int] = Query(1000)):
    """List predictions, optionally filtered by news_id."""
    try:
        rows: List[Dict[str, Any]] = []
        if news_id:
            raw_rows = fetch_all(
                """SELECT p.*, n.title as news_title, n.link as news_link
                FROM predictions p
                LEFT JOIN indian_news n ON n.id = p.news_id
                WHERE p.news_id = %s
                ORDER BY p.created_at DESC""",
                (news_id,),
            )
        else:
            raw_rows = fetch_all(
                """SELECT p.*, n.title as news_title, n.link as news_link
                FROM predictions p
                LEFT JOIN indian_news n ON n.id = p.news_id
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


# API server entry point
    
if __name__ == "__main__":
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    reload_enabled = os.getenv("API_RELOAD", "false").lower() in ("1", "true", "yes")
    print(f"Starting API Server on http://{host}:{port} (reload={reload_enabled})")
    uvicorn.run("server:app", host=host, port=port, reload=reload_enabled)