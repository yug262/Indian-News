# app/db/init_db.py
"""
Database Initialization Script for Indian News Market Intelligence.
Groups all table definitions, indexes, and initial state setup.
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load from specific paths if needed, but standard .env in root is fine
load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "news_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "your_password"),
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    print("Initializing Indian News Database...")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 1. Indian News Table
            print("  - Creating 'indian_news' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS indian_news (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    title_hash TEXT UNIQUE NOT NULL,
                    published TIMESTAMP WITH TIME ZONE,
                    source TEXT,
                    description TEXT,
                    image_url TEXT,
                    
                    -- Lightweight Classification
                    news_category TEXT DEFAULT 'None',
                    news_relevance TEXT DEFAULT 'None',
                    news_impact_level TEXT DEFAULT 'None',
                    news_reason TEXT DEFAULT 'No analysis available.',
                    affected_sectors TEXT[] DEFAULT '{}',
                    affected_stocks JSONB DEFAULT '{}',

                    
                    -- Analysis State & Output
                    analyzed BOOLEAN DEFAULT FALSE,
                    impact_score INTEGER,
                    impact_summary TEXT,
                    analysis_data JSONB,
                    market_bias TEXT DEFAULT 'neutral',
                    signal_bucket TEXT DEFAULT 'UNCLASSIFIED',
                    primary_symbol TEXT,
                    executive_summary TEXT,
                    decision_trace JSONB,
                    
                    -- Grouping & Identity
                    event_id TEXT,
                    event_title TEXT,
                    
                    -- Timing & Quality
                    analysis_confidence INTEGER DEFAULT 0,
                    horizon TEXT,
                    analysis_status TEXT, -- queued, processing, completed, failed
                    analysis_error TEXT,
                    analysis_started_at TIMESTAMP WITH TIME ZONE,
                    analysis_completed_at TIMESTAMP WITH TIME ZONE,
                    
                    -- Metadata
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    analyzed_at TIMESTAMP WITH TIME ZONE
                );
            """)

            # 2. NSE Companies Table (Synced by nse_pipeline)
            print("  - Creating 'nse_companies' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS nse_companies (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT,
                    series TEXT
                );
            """)

            # 3. Enriched Companies Table (Used by Agent Tools)
            print("  - Creating 'companies' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    nse_symbol TEXT UNIQUE,
                    company_name TEXT NOT NULL,
                    isin TEXT,
                    nse_company_name TEXT,
                    series TEXT,
                    sector TEXT,
                    industry TEXT,
                    macro TEXT,
                    basic_industry TEXT
                );
            """)

            # 4. NSE Candles Table (3-Minute Intervals)
            print("  - Creating 'nse_candles_3m' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS nse_candles_3m (
                    symbol TEXT NOT NULL,
                    time TIMESTAMP WITH TIME ZONE NOT NULL,
                    open NUMERIC,
                    high NUMERIC,
                    low NUMERIC,
                    close NUMERIC,
                    PRIMARY KEY (symbol, time)
                );
            """)

            # 5. System State Table (Real-time Sync)
            print("  - Creating 'indian_system_state' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS indian_system_state (
                    key TEXT PRIMARY KEY,
                    value BIGINT,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)

            # 6. Suggestions / Watchlist Table
            print("  - Creating 'suggestions' table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS suggestions (
                    id SERIAL PRIMARY KEY,
                    news_id INTEGER REFERENCES indian_news(id) ON DELETE CASCADE,
                    suggestion_type TEXT, -- buy, sell, watch, avoid
                    asset TEXT,           -- Symbol
                    direction TEXT,       -- bullish, bearish, neutral
                    reasoning TEXT,
                    market_logic TEXT,
                    time_window TEXT,
                    invalidation TEXT,
                    confidence INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)

            # 7. Indexes for Performance
            print("  - Creating indexes...")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_indian_news_published ON indian_news(published DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_indian_news_status ON indian_news(analysis_status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_indian_news_event_id ON indian_news(event_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nse_candles_time ON nse_candles_3m(time DESC);")

            # 8. Initial State Seed
            print("  - Seeding initial system state...")
            cur.execute("""
                INSERT INTO indian_system_state (key, value) 
                VALUES ('last_update_id', 0) 
                ON CONFLICT (key) DO NOTHING;
            """)

            conn.commit()
            
            # 9. Seed Companies Data
            seed_companies(conn)
            
            print("Database initialization complete!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Error during initialization: {e}")
    finally:
        conn.close()

def seed_companies(conn):
    """Seed the companies table from companies.csv."""
    csv_path = os.path.join(os.path.dirname(__file__), "companies.csv")
    if not os.path.exists(csv_path):
        print(f"  Seed file not found at {csv_path}. Skipping company seeding.")
        return

    import csv
    print(f"  - Seeding 'companies' table from {os.path.basename(csv_path)}...")
    try:
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            with conn.cursor() as cur:
                count = 0
                for row in reader:
                    cur.execute("""
                        INSERT INTO companies (
                            nse_symbol, company_name, isin, nse_company_name, 
                            series, sector, industry, macro, basic_industry
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (nse_symbol) DO UPDATE SET
                            company_name = EXCLUDED.company_name,
                            isin = EXCLUDED.isin,
                            nse_company_name = EXCLUDED.nse_company_name,
                            series = EXCLUDED.series,
                            sector = EXCLUDED.sector,
                            industry = EXCLUDED.industry,
                            macro = EXCLUDED.macro,
                            basic_industry = EXCLUDED.basic_industry;
                    """, (
                        row['nse_symbol'], row['company_name'], row['isin'], 
                        row['nse_company_name'], row['series'], row['sector'], 
                        row['industry'], row['macro'], row['basic_industry']
                    ))
                    count += 1
                conn.commit()
                print(f"    Successfully seeded {count} companies.")
    except Exception as e:
        print(f"  Error seeding companies: {e}")

if __name__ == "__main__":
    init_db()
