from app.db.db import fetch_all

def check_status():
    print("--- Data Check (Last 10) ---")
    query = """
    SELECT id, title, news_relevance, analysis_status, analysis_error 
    FROM indian_news 
    ORDER BY published DESC
    LIMIT 20
    """
    rows = fetch_all(query)
    for r in rows:
        print(f"ID: {r['id']} | Status: {r['analysis_status']} | Rel: {r['news_relevance']} | Title: {r['title'][:40]} | Err: {r['analysis_error']}")

if __name__ == "__main__":
    check_status()
