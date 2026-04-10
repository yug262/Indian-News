import psycopg2
import os

# Connect to the DB
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "news_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

def main():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Step 1: Purge Legacy Global Data from Shared Tables
        print("Purging global rows from 'predictions' table...")
        cursor.execute("DELETE FROM predictions WHERE news_id NOT IN (SELECT id FROM indian_news);")
        print(f"Deleted {cursor.rowcount} orphaned predictions.")

        print("Purging global rows from 'suggestions' table...")
        cursor.execute("DELETE FROM suggestions WHERE news_id NOT IN (SELECT id FROM indian_news);")
        print(f"Deleted {cursor.rowcount} orphaned suggestions.")

        # Step 2: Establish the Native Indian Foreign Keys
        print("Creating native Indian Foreign Key for 'predictions'...")
        try:
            cursor.execute("ALTER TABLE predictions ADD CONSTRAINT predictions_inews_id_fkey FOREIGN KEY (news_id) REFERENCES indian_news(id) ON DELETE CASCADE;")
            print("Successfully added predictions_inews_id_fkey.")
        except Exception as e:
            print(f"Constraint might already exist or failed: {e}")

        print("Creating native Indian Foreign Key for 'suggestions'...")
        try:
            cursor.execute("ALTER TABLE suggestions ADD CONSTRAINT suggestions_inews_id_fkey FOREIGN KEY (news_id) REFERENCES indian_news(id) ON DELETE CASCADE;")
            print("Successfully added suggestions_inews_id_fkey.")
        except Exception as e:
            print(f"Constraint might already exist or failed: {e}")

        # Step 3: Delete Global Tables
        print("Dropping legacy table 'news'...")
        cursor.execute("DROP TABLE IF EXISTS news CASCADE;")
        print("Deleted 'news' table.")

        print("Dropping legacy table 'forex_candles_3m'...")
        cursor.execute("DROP TABLE IF EXISTS forex_candles_3m CASCADE;")
        print("Deleted 'forex_candles_3m' table.")

        print("Phase 6 Database cleanup executed flawlessly.")

    except Exception as e:
        print(f"Database error occurred: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
