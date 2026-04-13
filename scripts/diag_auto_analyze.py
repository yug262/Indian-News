from app.db.db import fetch_all, execute_query

def verify():
    print("--- Candidate Check ---")
    query = """
    SELECT id, title, news_relevance, analysis_status 
    FROM indian_news 
    WHERE news_relevance IN ('Medium', 'Useful', 'High Useful') 
    AND (analysis_status IS NULL OR analysis_status = 'failed')
    LIMIT 3
    """
    rows = fetch_all(query)
    for r in rows:
        print(f"ID: {r['id']} | Status: {r['analysis_status']} | Relevance: {r['news_relevance']} | Title: {r['title'][:50]}")
    
    if rows:
        test_id = rows[0]['id']
        print(f"\n--- Simulating Scraper Queue for ID {test_id} ---")
        # Scraper-side claim logic
        updated = execute_query(
            "UPDATE indian_news SET analysis_status = 'queued' WHERE id = %s AND analysis_status IS NULL",
            (test_id,)
        )
        print(f"Update rowcount: {updated}")
        
        # Verify it's queued
        row = fetch_all("SELECT analysis_status FROM indian_news WHERE id = %s", (test_id,))[0]
        print(f"Post-update status: {row['analysis_status']}")

if __name__ == "__main__":
    verify()
