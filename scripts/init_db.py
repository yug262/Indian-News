"""
Initialize the PostgreSQL database and create the news table.
Run this script once before starting the monitor or server.

Usage:
    python init_db.py
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from app.core.db import DB_CONFIG

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    title_hash VARCHAR(255) UNIQUE NOT NULL,
    published TIMESTAMPTZ NOT NULL,
    source VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_news_published ON news(published DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_hash ON news(title_hash);",
    "CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);",
]


def create_database():
    """Create the database if it doesn't exist."""
    db_name = DB_CONFIG["dbname"]
    conn_params = {k: v for k, v in DB_CONFIG.items() if k != "dbname"}
    conn_params["dbname"] = "postgres"  # Connect to default db first

    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()

        # Check if database exists
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


def create_tables():
    """Create the news table and indexes."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        conn.commit()
        print("✅ Table 'news' created successfully")
        print("✅ Indexes created successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating tables: {e}")
        raise
    finally:
        conn.close()


MIGRATE_ANALYSIS_COLUMNS = [
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS impact_score INTEGER;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS impact_summary TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS affected_markets JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS affected_sectors TEXT[];",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS impact_duration VARCHAR(255);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS analyzed BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS description TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS image_url TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS market_mode TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS usd_bias TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS crypto_bias TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS trade_actions JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS execution_window TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS forex_pairs JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS confidence VARCHAR(255);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS conviction_score NUMERIC;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS volatility_regime TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dollar_liquidity_state TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS position_size_percent NUMERIC;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS safe_haven_flow JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS research_text TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS is_new_information BOOLEAN;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS tools_used JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS analysis_data JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_age_label TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_age_human TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_priced_in BOOLEAN;",
    # --- FILTER OUTPUT (from filter.py) ---
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_keep BOOLEAN;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_reason TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_trigger BOOLEAN;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_news_type TEXT;",      # event/reaction
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_event_type TEXT;",     # central_bank/geopolitics/...
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_assets TEXT[];",       # ["gold","usd"]
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_impact_score INTEGER;",# pre-score from filter (NOT agent)

    # --- DEDUP OUTPUT (from dedup.py) ---
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_normalized TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_reason TEXT;",          # fingerprint/similarity
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_matched_score NUMERIC;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_matched_title TEXT;",

    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_keep BOOLEAN;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_reason TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_trigger BOOLEAN;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_news_type TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_event_type TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_assets TEXT[];"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS filter_impact_score INTEGER;"

    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_normalized TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_reason TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_matched_score NUMERIC;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_matched_title TEXT;"

    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_age_label TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_age_human TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_priced_in BOOLEAN DEFAULT FALSE;"

    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_category VARCHAR(100);"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_impact_level VARCHAR(50);"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_reason TEXT;"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_relevance VARCHAR(20) DEFAULT 'unclassified';"
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP WITH TIME ZONE;"
    

]


def migrate_schema():
    """Add analysis columns to the existing news table."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        for sql in MIGRATE_ANALYSIS_COLUMNS:
            cur.execute(sql)
        conn.commit()
        print("✅ Analysis columns migrated successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error migrating schema: {e}")
        raise
    finally:
        conn.close()


CREATE_PREDICTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,

    asset TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    asset_display_name TEXT,
    direction TEXT NOT NULL,

    predicted_move_pct NUMERIC NOT NULL,
    expected_duration_label TEXT NOT NULL,
    expected_duration_minutes INTEGER NOT NULL,

    start_time TIMESTAMPTZ NOT NULL,
    start_price NUMERIC NOT NULL,
    target_price NUMERIC NOT NULL,

    last_checked_at TIMESTAMPTZ,
    last_price NUMERIC,
    last_move_pct NUMERIC,

    mfe_pct NUMERIC DEFAULT 0,
    mae_pct NUMERIC DEFAULT 0,

    status TEXT DEFAULT 'pending',
    finalized BOOLEAN DEFAULT FALSE,
    finalized_at TIMESTAMPTZ,
    final_price NUMERIC,
    final_move_pct NUMERIC,

    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_PREDICTIONS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_predictions_pending ON predictions(finalized, status);",
    "CREATE INDEX IF NOT EXISTS idx_predictions_news_id ON predictions(news_id);",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS asset_display_name TEXT;",
]


def create_predictions_table():
    """Create the predictions table and its indexes."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(CREATE_PREDICTIONS_TABLE_SQL)
        for idx_sql in CREATE_PREDICTIONS_INDEXES_SQL:
            cur.execute(idx_sql)
        conn.commit()
        print("✅ Predictions table created successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating predictions table: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print("🔧 Initializing database...")
    create_database()
    create_tables()
    migrate_schema()
    create_predictions_table()
    print("🎉 Database initialization complete!")
