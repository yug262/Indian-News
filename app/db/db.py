import os
import psycopg2
from psycopg2 import pool, extras
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "news_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "your_password"),
}

import threading

# Connection pool tuned for concurrent API + worker load.
_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # Use ThreadedConnectionPool instead of SimpleConnectionPool for thread safety
                _pool = pool.ThreadedConnectionPool(5, 100, **DB_CONFIG)
    return _pool


def get_connection():
    return get_pool().getconn()


def release_connection(conn):
    get_pool().putconn(conn)


def execute_query(query, params=None):
    """Execute a query (INSERT, UPDATE, DELETE) and return affected row count."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_connection(conn)


def execute_many(query, params_list):
    """Execute a query with multiple param sets using execute_batch for speed."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            extras.execute_batch(cur, query, params_list)
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_connection(conn)


def execute_returning(query, params=None):
    """Execute an INSERT/UPDATE query with RETURNING clause and commit."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_connection(conn)


def fetch_all(query, params=None):
    """Execute a SELECT query and return all rows as list of dicts."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        release_connection(conn)


def fetch_one(query, params=None):
    """Execute a SELECT query and return the first row as a dict."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()
    finally:
        release_connection(conn)


# ══════════════════════════════════════════════════════
# REAL-TIME SYNC HELPERS
# ══════════════════════════════════════════════════════

def init_system_tables():
    """Initializes the system state table for real-time synchronization."""
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS indian_system_state (
                key TEXT PRIMARY KEY,
                value BIGINT,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)
        execute_query("""
            INSERT INTO indian_system_state (key, value) 
            VALUES ('last_update_id', 0) 
            ON CONFLICT (key) DO NOTHING;
        """)
    except Exception as e:
        print(f"[DB] Failed to initialize system tables: {e}")

def notify_indian_update():
    """Increments the global update counter to trigger frontend refreshes."""
    try:
        execute_query("""
            UPDATE indian_system_state 
            SET value = value + 1, updated_at = NOW() 
            WHERE key = 'last_update_id';
        """)
    except Exception as e:
        print(f"[DB] Failed to notify update: {e}")

def get_latest_indian_update_id():
    """Reads the current global update counter."""
    try:
        row = fetch_one("SELECT value FROM indian_system_state WHERE key = 'last_update_id'")
        return row['value'] if row else 0
    except Exception:
        return 0

def execute_notify(channel, payload):
    """
    Sends a Postgres NOTIFY signal to a specific channel with a JSON payload.
    This is the core of our 'Strong' real-time sync system.
    """
    conn = get_connection()
    try:
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(f"NOTIFY {channel}, %s;", (payload,))
    except Exception as e:
        print(f"[DB] NOTIFY failed on channel {channel}: {e}")
    finally:
        release_connection(conn)

# # Initialization has been moved to the server's startup event 
# # to ensure cleaner dependency handling. 
# init_system_tables()
