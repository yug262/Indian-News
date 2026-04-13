"""
Initialize the PostgreSQL database and create required tables for the Indian News Intelligence system.
Run this script once before starting the monitor or server.

Usage:
    python scripts/init_db.py
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
import csv
from datetime import datetime
from app.db.db import DB_CONFIG

# --- CORE NEWS TABLE ---
CREATE_INDIAN_NEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS indian_news (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    title_hash VARCHAR(255) UNIQUE NOT NULL,
    published TIMESTAMPTZ NOT NULL,
    source VARCHAR(255),
    
    -- Analysis / AI Agent Output
    analyzed BOOLEAN DEFAULT FALSE,
    impact_score INTEGER,
    impact_summary TEXT,
    description TEXT,
    image_url TEXT,
    analysis_data JSONB,
    news_category VARCHAR(100),
    news_relevance VARCHAR(100) DEFAULT 'unclassified',
    news_reason TEXT,
    symbols TEXT[],
    analyzed_at TIMESTAMPTZ,
    
    event_type VARCHAR(50),
    event_status VARCHAR(30),
    event_scope VARCHAR(30),
    market_bias VARCHAR(20),
    analysis_confidence INTEGER,
    horizon VARCHAR(30),
    surprise VARCHAR(20),
    primary_symbol VARCHAR(50),
    primary_company_name VARCHAR(255),
    executive_summary TEXT,
    stock_impacts JSONB,
    sector_impacts JSONB,
    scenarios JSONB,
    priority_ranking JSONB,
    tradeability_classification VARCHAR(30),
    tradeability_data JSONB,
    confidence_breakdown JSONB,
    agent_output JSONB,
    signal_bucket VARCHAR(20),
    news_summary JSONB,
    affected_entities JSONB,
    evidence JSONB,
    decision_trace JSONB DEFAULT '{}',
    news_impact_level VARCHAR(50),
    
    -- Event Grouping (written by event_engine.py)
    event_id VARCHAR(100),
    event_title TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_INDIAN_NEWS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_inews_published ON indian_news(published DESC);",
    "CREATE INDEX IF NOT EXISTS idx_inews_hash ON indian_news(title_hash);",
    "CREATE INDEX IF NOT EXISTS idx_inews_source ON indian_news(source);",
    "CREATE INDEX IF NOT EXISTS idx_inews_analyzed ON indian_news(analyzed);",
    "CREATE INDEX IF NOT EXISTS idx_inews_event_id ON indian_news(event_id);",
]



# --- SUGGESTIONS TABLE (Linked to Indian News) ---
CREATE_SUGGESTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS suggestions (
    id BIGSERIAL PRIMARY KEY,
    news_id BIGINT NOT NULL REFERENCES indian_news(id) ON DELETE CASCADE,

    suggestion_type VARCHAR(20) NOT NULL,      -- buy / sell / watch / avoid
    asset VARCHAR(100) NOT NULL,
    direction VARCHAR(20),

    reasoning TEXT,
    market_logic TEXT,

    expected_move_pct VARCHAR(50),
    time_window VARCHAR(100),
    expected_duration_minutes INTEGER,

    invalidation TEXT,

    start_price NUMERIC,
    target_price NUMERIC,

    confidence VARCHAR(20),
    confidence_score INTEGER,

    status VARCHAR(20) DEFAULT 'pending',

    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);
"""

# --- NSE DATA TABLES ---
CREATE_NSE_COMPANIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nse_companies (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(50) UNIQUE NOT NULL,
    company_name VARCHAR(255),
    series VARCHAR(10),
    created_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_NSE_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nse_candles_3m (
    symbol TEXT,
    time TIMESTAMP,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    PRIMARY KEY(symbol, time)
);
"""

CREATE_COMPANIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    isin VARCHAR(20) PRIMARY KEY,
    nse_symbol VARCHAR(50) UNIQUE NOT NULL,
    company_name TEXT,
    nse_company_name TEXT,
    series VARCHAR(10),
    sector VARCHAR(255),
    industry VARCHAR(255),
    macro VARCHAR(255),
    basic_industry VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
"""

def create_database():
    """Create the database if it doesn't exist."""
    db_name = DB_CONFIG["dbname"]
    conn_params = {k: v for k, v in DB_CONFIG.items() if k != "dbname"}
    conn_params["dbname"] = "postgres"

    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{db_name}"')
            print(f"✅ Database '{db_name}' created successfully")
        else:
            print(f"ℹ️  Database '{db_name}' already exists")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Error creating database: {e}")
        raise

def run_sql(sql_list, label):
    """Utility to run a list of SQL commands."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        if isinstance(sql_list, str):
            cur.execute(sql_list)
        else:
            for sql in sql_list:
                cur.execute(sql)
        conn.commit()
        print(f"✅ {label} completed successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in {label}: {e}")
        raise
    finally:
        conn.close()

def populate_companies_table():
    """Populate the companies table from companies.csv if it exists."""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companies.csv")
    if not os.path.exists(csv_path):
        print(f"⚠️  CSV file not found at {csv_path}, skipping population")
        return

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                rows.append((
                    row['isin'], row['nse_symbol'], row['company_name'],
                    row['nse_company_name'], row['series'], row['sector'],
                    row['industry'], row['macro'], row['basic_industry'],
                    datetime.now()
                ))

        cur = conn.cursor()
        upsert_query = """
        INSERT INTO companies (
            isin, nse_symbol, company_name, nse_company_name, 
            series, sector, industry, macro, basic_industry, updated_at
        ) VALUES %s
        ON CONFLICT (isin) DO UPDATE SET
            nse_symbol = EXCLUDED.nse_symbol,
            company_name = EXCLUDED.company_name,
            nse_company_name = EXCLUDED.nse_company_name,
            series = EXCLUDED.series,
            sector = EXCLUDED.sector,
            industry = EXCLUDED.industry,
            macro = EXCLUDED.macro,
            basic_industry = EXCLUDED.basic_industry,
            updated_at = NOW();
        """
        psycopg2.extras.execute_values(cur, upsert_query, rows)
        conn.commit()
        print(f"✅ Companies table populated/updated with {len(rows)} rows")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error populating companies table: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔧 Initializing Indian News Intelligence Database...")
    create_database()
    
    # 1. Indian News
    run_sql(CREATE_INDIAN_NEWS_TABLE_SQL, "Indian News table")
    run_sql(CREATE_INDIAN_NEWS_INDEXES_SQL, "Indian News indexes")
    
    # 2. NSE & Companies
    run_sql(CREATE_NSE_COMPANIES_TABLE_SQL, "NSE companies table")
    run_sql(CREATE_NSE_CANDLES_TABLE_SQL, "NSE candles table")
    run_sql("CREATE INDEX IF NOT EXISTS idx_nse_candle_symbol_time ON nse_candles_3m(symbol, time DESC);", "NSE candle indexes")
    run_sql(CREATE_COMPANIES_TABLE_SQL, "Companies master table")
    populate_companies_table()
    

    # 3. Suggestions
    run_sql(CREATE_SUGGESTIONS_TABLE_SQL, "Suggestions table")
    run_sql([
        "CREATE INDEX IF NOT EXISTS idx_suggestions_news_id ON suggestions(news_id);",
        "CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);",
        "CREATE INDEX IF NOT EXISTS idx_suggestions_type ON suggestions(suggestion_type);"
    ], "Suggestion indexes")
    
    print("🎉 Database initialization complete!")
