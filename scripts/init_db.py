"""
Initialize the PostgreSQL database and create required tables.
Run this script once before starting the monitor or server.

Usage:
    python init_db.py
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os
import sys
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
        print("✅ News indexes created successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating news table: {e}")
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
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_category VARCHAR(100);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_impact_level VARCHAR(50);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_reason TEXT;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS news_relevance VARCHAR(100) DEFAULT 'unclassified';",
"ALTER TABLE news ALTER COLUMN news_relevance TYPE VARCHAR(100);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS suggestions_data JSONB;",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS suggestions_status VARCHAR(30);",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS suggestions_summary TEXT;",
]


def migrate_schema():
    """Add analysis columns to the existing news table."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        for sql in MIGRATE_ANALYSIS_COLUMNS:
            cur.execute(sql)

        for sql in MIGRATE_SUGGESTIONS_COLUMNS:
            cur.execute(sql)
        conn.commit()
        print("✅ Analysis columns migrated successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error migrating news schema: {e}")
        raise
    finally:
        conn.close()


CREATE_PREDICTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,

    asset TEXT NOT NULL,
    asset_display_name TEXT,
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

MIGRATE_SUGGESTIONS_COLUMNS = [
    "ALTER TABLE suggestions ALTER COLUMN expected_move_pct TYPE VARCHAR(50);",
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
        print("✅ Prediction indexes created successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating predictions table: {e}")
        raise
    finally:
        conn.close()


CREATE_SUGGESTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS suggestions (
    id BIGSERIAL PRIMARY KEY,
    news_id BIGINT NOT NULL REFERENCES news(id) ON DELETE CASCADE,

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

CREATE_SUGGESTIONS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_suggestions_news_id ON suggestions(news_id);",
    "CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);",
    "CREATE INDEX IF NOT EXISTS idx_suggestions_type ON suggestions(suggestion_type);",
]


def create_suggestions_table():
    """Create the suggestions table and its indexes."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(CREATE_SUGGESTIONS_TABLE_SQL)
        for idx_sql in CREATE_SUGGESTIONS_INDEXES_SQL:
            cur.execute(idx_sql)
        conn.commit()
        print("✅ Suggestions table created successfully")
        print("✅ Suggestion indexes created successfully")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating suggestions table: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print("🔧 Initializing database...")
    create_database()
    create_tables()
    migrate_schema()
    create_predictions_table()
    create_suggestions_table()
    print("🎉 Database initialization complete!")