from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from datetime import datetime, timezone
from typing import Optional
import os
from app.core.agent import analyze_news, save_analysis
from app.core.db import fetch_all, fetch_one

app = FastAPI(title="News Website API")
SERVER_START = datetime.now(timezone.utc)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.get("/api/news")
def get_news(source: str = Query(None, description="Filter news by source name"), 
             limit: int = Query(50, description="Max number of articles to return"),
             today_only: bool = Query(False, description="Only fetch today's news"),
             relevance: str = Query(None, description="Filter news by relevance"),
             analyzed_only: bool = Query(False, description="Only fetch analyzed news")):
    """Get news articles, sorted by newest first."""
    
    query = """SELECT id, title, link, published, source, description, image_url,
        impact_score, impact_summary, affected_markets, affected_sectors, impact_duration,
        analyzed, created_at, market_mode, usd_bias, crypto_bias, trade_actions,
        execution_window, confidence, forex_pairs, conviction_score, volatility_regime,
        dollar_liquidity_state, position_size_percent, safe_haven_flow, research_text,
        is_new_information, tools_used, analysis_data, news_relevance, news_category,
        news_impact_level, news_reason
    FROM news WHERE 1=1"""
    params = []
    
    if today_only:
        today = datetime.now(timezone.utc).date()
        query += " AND DATE(published) = %s"
        params.append(today)
        
    if source and source.lower() != "all":
        query += " AND source = %s"
        params.append(source)
    
    if relevance and relevance.lower() != "all":
        query += " AND news_relevance = %s"
        params.append(relevance.lower())
        
    if analyzed_only:
        query += " AND analyzed = TRUE"
        
    query += " ORDER BY published DESC LIMIT %s"
    params.append(limit)
    
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

@app.get("/api/sources")
def get_sources():
    """Get list of distinct news sources available for today."""
    today = datetime.now(timezone.utc).date()
    query = "SELECT DISTINCT source FROM news WHERE DATE(published) = %s ORDER BY source"
    try:
         sources = fetch_all(query, (today,))
         return {"status": "success", "data": [s['source'] for s in sources]}
    except Exception as e:
         return {"status": "error", "message": str(e)}


@app.post("/api/analyze/{news_id}")
def analyze_single_article(news_id: int):
    """Analyze a single news article by its DB id."""
    
    try:
        article = fetch_one("SELECT id, title, published, description FROM news WHERE id = %s", (news_id,))
        if not article:
            return {"status": "error", "message": "Article not found"}

        title = article["title"]
        published = str(article["published"])
        description = article.get("description", "") or ""

        analysis = analyze_news(title, published, description)

        if analysis:
            try:
                save_analysis(news_id, analysis)
                print(f"[API] Analysis saved for news_id={news_id}, score={analysis.get('impact_score')}")
                return {"status": "success", "data": analysis}
            except Exception as save_err:
                print(f"[API] save_analysis FAILED for news_id={news_id}: {save_err}")
                import traceback
                traceback.print_exc()
                return {"status": "error", "message": f"Save failed: {save_err}"}
        else:
            print(f"[API] analyze_news returned None for news_id={news_id}")
            return {"status": "error", "message": "Analysis failed — click to retry"}
    except Exception as e:
        print(f"[API] Exception in analyze endpoint: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.get("/api/stats")
def get_stats():
    """Get dashboard statistics for the footer."""
    try:
        row = fetch_one(
            "SELECT COUNT(*) as total, "
            "COUNT(CASE WHEN analyzed = true THEN 1 END) as analyzed, "
            "COUNT(DISTINCT source) as sources "
            "FROM news"
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


@app.get("/api/predictions")
def get_predictions(news_id: Optional[int] = Query(None), limit: int = Query(50)):
    """List predictions, optionally filtered by news_id."""
    try:
        if news_id:
            rows = fetch_all(
                """SELECT p.*, n.title as news_title
                FROM predictions p
                LEFT JOIN news n ON n.id = p.news_id
                WHERE p.news_id = %s
                ORDER BY p.created_at DESC""",
                (news_id,),
            )
        else:
            rows = fetch_all(
                """SELECT p.*, n.title as news_title
                FROM predictions p
                LEFT JOIN news n ON n.id = p.news_id
                ORDER BY p.created_at DESC
                LIMIT %s""",
                (limit,),
            )
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

        total_finalized = row['finalized'] or 0
        hit_count = (row['hit'] or 0) + (row['overperformed'] or 0)
        hit_rate = round((hit_count / total_finalized * 100), 1) if total_finalized > 0 else 0

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
                "avg_final_move_pct": round(float(row['avg_final_move']), 2),
                "avg_mfe_pct": round(float(row['avg_mfe']), 2),
                "avg_mae_pct": round(float(row['avg_mae']), 2),
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Serve static frontend files
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if not os.path.exists(frontend_dir):
    os.makedirs(frontend_dir)

@app.get("/")
def read_root():
    """Serve the index.html page."""
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Frontend not found. Please create frontend/index.html"}

# Mount static AFTER explicit routes so /api/* and / are matched first
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

if __name__ == "__main__":
    print("Starting API Server on http://localhost:8000")
    uvicorn.run("server:app", host="localhost", port=8000, reload=True)
