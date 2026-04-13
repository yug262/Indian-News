import httpx
import asyncio
from app.db.db import fetch_one, execute_query

async def verify_api_claim(news_id):
    print(f"\n--- Testing API Atomic Claim for ID {news_id} ---")
    url = f"http://localhost:8000/api/indian_analyze/{news_id}"
    
    async with httpx.AsyncClient() as client:
        try:
            # We hit the API. The API should:
            # 1. Claim it (set status to processing)
            # 2. Run analysis
            # 3. Set status to completed
            print(f"Calling API: {url}")
            resp = await client.post(url, timeout=5) # Short timeout for claim check
            print(f"API Response: {resp.status_code} | {resp.text}")
            
            # Re-fetch state
            row = fetch_one("SELECT analysis_status, analysis_error FROM indian_news WHERE id = %s", (news_id,))
            print(f"New Status in DB: {row['analysis_status']}")
            if row['analysis_error']:
                print(f"Error: {row['analysis_error']}")
        except httpx.ReadTimeout:
            print("API hit successful (timed out during analysis as expected). Checking status...")
            # If it timed out, it should still have been claimed
            row = fetch_one("SELECT analysis_status FROM indian_news WHERE id = %s", (news_id,))
            print(f"Status in DB after timeout: {row['analysis_status']}")
        except Exception as e:
            print(f"API Call failed: {e}")

if __name__ == "__main__":
    test_id = 242828
    asyncio.run(verify_api_claim(test_id))
