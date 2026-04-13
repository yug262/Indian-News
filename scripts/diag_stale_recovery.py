from app.db.db import execute_query, fetch_one

def test_stale_logic():
    # 1. Setup stale row
    news_id = 288205
    execute_query(
        "UPDATE indian_news SET analysis_status='processing', analysis_started_at=(NOW() - INTERVAL '10 minutes') WHERE id=%s",
        (news_id,)
    )
    print(f"Set {news_id} to stale processing.")

    # 2. Run the recovery query (same as in Cleanup task)
    stale_count = execute_query(
        """UPDATE indian_news 
           SET analysis_status = 'failed', 
               analysis_error = 'Staleness timeout: article was stuck in processing for too long (likely server crash).'
           WHERE analysis_status = 'processing' 
           AND analysis_started_at < (NOW() - INTERVAL '5 minutes')"""
    )
    print(f"Recovery rowcount: {stale_count}")

    # 3. Verify
    row = fetch_one("SELECT analysis_status, analysis_error FROM indian_news WHERE id=%s", (news_id,))
    print(f"Final Status: {row['analysis_status']}")
    print(f"Final Error: {row['analysis_error']}")

if __name__ == "__main__":
    test_stale_logic()
