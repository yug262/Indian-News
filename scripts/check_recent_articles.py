from app.db.db import fetch_all

def check_recent():
    print("--- Recent Articles Status ---")
    query = """
    SELECT id, title, news_relevance, analysis_status, published, created_at AT TIME ZONE 'UTC' as created_at
    FROM indian_news 
    ORDER BY created_at DESC
    LIMIT 15
    """
    rows = fetch_all(query)
    for r in rows:
        print(f"ID: {r['id']} | Status: {r['analysis_status']} | Rel: {r['news_relevance']} | Created: {r['created_at']} | Title: {r['title'][:40]}")

if __name__ == "__main__":
    check_recent()
