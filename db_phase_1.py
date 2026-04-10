import traceback
from app.core.db import fetch_all, execute_query

def main():
    print("--- STEP 1: Confirm Constraint Names ---")
    constraints = fetch_all(
        "SELECT conname FROM pg_constraint WHERE conrelid::regclass::text IN ('predictions', 'suggestions') AND contype = 'f';"
    )
    names = [c['conname'] for c in constraints]
    print(f"Constraints found: {names}")

    if not names:
        print("No foreign keys found on predictions/suggestions! Already dropped?")
    
    print("\n--- STEP 2: Drop Foreign Keys ---")
    for c_name in names:
        if 'predictions' in c_name:
            execute_query(f"ALTER TABLE predictions DROP CONSTRAINT IF EXISTS {c_name};")
            print(f"Dropped {c_name} from predictions")
        elif 'suggestions' in c_name:
            execute_query(f"ALTER TABLE suggestions DROP CONSTRAINT IF EXISTS {c_name};")
            print(f"Dropped {c_name} from suggestions")
    
    print("\n--- STEP 3: Test Insert Manually (Loose Link Validation) ---")
    try:
        # 1. Insert a mock Indian News row
        execute_query("INSERT INTO indian_news (title, link, title_hash, published) VALUES ('MOCK TEST NEWS', 'http', 'mockhash_12345', NOW()) ON CONFLICT DO NOTHING;")
        rows = fetch_all("SELECT id FROM indian_news WHERE title_hash = 'mockhash_12345';")
        
        if rows:
            inews_id = rows[0]['id']
            # We will use this inews_id directly as `news_id` in predictions.
            # Assuming inews_id doesn't randomly exist in `news`, this will violate FK if not dropped.
            # Even if it exists, it proves insertion succeeds without blowing up.
            
            print(f"Mock indian_news created with ID {inews_id}")
            
            # 2. Insert into predictions
            execute_query(
                """INSERT INTO predictions 
                   (news_id, asset, asset_class, direction, predicted_move_pct, expected_duration_label, expected_duration_minutes, start_time, start_price, target_price) 
                   VALUES (%s, 'MOCK_ASSET', 'stock', 'neutral', 0, '1d', 1440, NOW(), 1, 1);""", 
                (inews_id,)
            )
            print("=> MOCK PREDICTION INSERTED SUCCESSFULLY 🎉 (FK is loose)")
            
            # 3. Cleanup
            execute_query("DELETE FROM predictions WHERE news_id = %s AND asset = 'MOCK_ASSET';", (inews_id,))
            execute_query("DELETE FROM indian_news WHERE id = %s;", (inews_id,))
            print("=> MOCK DATA CLEANED UP SUCCESSFULLY")
        else:
            print("Failed to retrieve mock indian_news ID.")

    except Exception as e:
        print("!!! => TEST FAILED:", e)
        traceback.print_exc()

if __name__ == "__main__":
    main()
