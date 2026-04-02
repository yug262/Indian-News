import os
import sys
import websocket
import json
import random
import string
import threading
import time
import requests
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from app.core.db import DB_CONFIG
# =========================
# CONFIGURATION
# =========================
# Finnhub key for pair sync (optional, as we can also use TV symbol search)
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# Mandatory exotic pairs to ensure they are always tracked
MANDATORY_EXOTICS = [
    "RUBUSD", "USDIRR", "USDILS", "USDINR", "USDSAR", "USDRUB", 
    "EURINR", "GBPINR", "JPYINR", "USDCNH", "USDTRY", "USDZAR",
    "USDMXN", "USDBRL", "USDKRW", "USDIDR"
]

# Standard major/minor pairs to ensure coverage if API discovery fails
DEFAULT_MAJORS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY", "EURCAD", "EURAUD"
]

# =========================
# DATABASE SETUP
# =========================
def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    

# =========================
# PAIR SYNC (24H)
# =========================
def sync_pairs():
    print("🔄 Syncing forex pairs from multiple sources...")
    
    if not FINNHUB_API_KEY:
        print("❌ ERROR: FINNHUB_API_KEY not found in environment variables.")
        print("⚠️ Falling back to default major and mandatory exotic pairs.")
        
        # Build default list from hardcoded majors and exotics
        # We assume FX_IDC for these as it's the most generic provider
        fallback_pairs = [f"FX_IDC:{p}" for p in (DEFAULT_MAJORS + MANDATORY_EXOTICS)]
        
        # Save them to DB so they persist
        try:
            with psycopg2.connect(**DB_CONFIG) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    for symbol in fallback_pairs:
                        cur.execute("INSERT INTO forex_pairs (symbol) VALUES (%s) ON CONFLICT DO NOTHING", (symbol,))
            print(f"✅ Synced {len(fallback_pairs)} default/mandatory pairs to database.")
        except Exception as db_err:
            print(f"⚠️ Failed to save default pairs to DB: {db_err}")
            
        return fallback_pairs

    try:
        exchanges = ["oanda", "fxcm", "forex.com"]
        canonical_to_provider = {} # pair -> preferred_full_symbol

        # Helper to get canonical pair name (e.g., 'EURUSD')
        def get_canonical(s):
            if ":" in s:
                s = s.split(":")[1]
            return s.replace("_", "").replace("/", "").upper()

        for ex in exchanges:
            url = f"https://finnhub.io/api/v1/forex/symbol?exchange={ex}&token={FINNHUB_API_KEY}"
            res = requests.get(url, timeout=12)
            if res.status_code == 200:
                data = res.json()
                for item in data:
                    full_sym = item["symbol"]
                    # Normalize: 'OANDA:EUR_USD' -> 'OANDA:EURUSD'
                    parts = full_sym.split(":")
                    if len(parts) == 2:
                        provider = parts[0]
                        pair = parts[1].replace("_", "").replace("/", "").upper()
                        norm_full = f"{provider}:{pair}"
                    else:
                        pair = full_sym.replace("_", "").replace("/", "").upper()
                        norm_full = f"FX_IDC:{pair}"
                    
                    canon = get_canonical(full_sym)
                    if len(canon) < 6: continue

                    # Priority: OANDA > FXCM > others
                    if canon not in canonical_to_provider:
                        canonical_to_provider[canon] = norm_full
                    elif "OANDA" in norm_full:
                        canonical_to_provider[canon] = norm_full
                    elif "FXCM" in norm_full and "OANDA" not in canonical_to_provider[canon]:
                        canonical_to_provider[canon] = norm_full

        # Add mandatory exotics if not already present
        for ex_pair in MANDATORY_EXOTICS:
            canon = ex_pair.upper()
            if canon not in canonical_to_provider:
                # Default to FX_IDC for these exotics as it provides broader coverage
                if canon == "RUBUSD": canonical_to_provider[canon] = "FX_IDC:RUBUSD"
                elif canon == "USDIRR": canonical_to_provider[canon] = "FX_IDC:USDIRR"
                elif canon == "USDILS": canonical_to_provider[canon] = "FX_IDC:USDILS"
                elif canon == "USDINR": canonical_to_provider[canon] = "FX_IDC:USDINR"
                elif canon == "USDSAR": canonical_to_provider[canon] = "FX_IDC:USDSAR"
                else: canonical_to_provider[canon] = f"FX_IDC:{canon}"

        unique_symbols = list(canonical_to_provider.values())
        
        with psycopg2.connect(**DB_CONFIG) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                for symbol in unique_symbols:
                    cur.execute("INSERT INTO forex_pairs (symbol) VALUES (%s) ON CONFLICT DO NOTHING", (symbol,))
        
        print(f"✅ Synced {len(unique_symbols)} unique symbols for {len(canonical_to_provider)} forex pairs.")
        return unique_symbols
    except Exception as e:
        print(f"❌ Error syncing pairs: {e}")
    return []

def get_stored_pairs():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM forex_pairs")
        pairs = [row[0] for row in cur.fetchall()]
    conn.close()
    return pairs

# =========================
# TRADINGVIEW ENGINE
# =========================
candles = {}  # (symbol, bucket) -> {open, high, low, close, count}
candles_lock = threading.Lock()

def get_bucket(ts):
    minute = (ts.minute // 3) * 3
    return ts.replace(minute=minute, second=0, microsecond=0)

def process_tick(symbol, price):
    now = datetime.utcnow()
    bucket = get_bucket(now)
    key = (symbol, bucket)

    with candles_lock:
        if key not in candles:
            candles[key] = {"open": price, "high": price, "low": price, "close": price}
        else:
            c = candles[key]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price

def flush_candles():
    now = datetime.utcnow()
    to_delete = []
    
    with candles_lock:
        stored_items = list(candles.items())

    if not stored_items:
        return

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    with conn.cursor() as cur:
        for (symbol, t), c in stored_items:
            # Save candle once the 3-minute bucket has passed
            if now >= t + timedelta(minutes=3):
                cur.execute("""
                INSERT INTO forex_candles_3m (symbol, time, open, high, low, close)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, time) DO NOTHING
                """, (symbol, t, c["open"], c["high"], c["low"], c["close"]))
                to_delete.append((symbol, t))
                print(f"💾 Saved 3m candle for {symbol} at {t}")

    with candles_lock:
        for key in to_delete:
            if key in candles:
                del candles[key]
    conn.close()

def cleanup_old_data():
    """Remove candles older than 24 hours from forex_candles_3m."""
    print("🧹 Cleaning up old forex candle data...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Delete candles older than 24 hours
            cur.execute("DELETE FROM forex_candles_3m WHERE time < %s", (datetime.utcnow() - timedelta(hours=24),))
            print(f"✅ Cleanup complete: Removed {cur.rowcount} old candles.")
        conn.close()
    except Exception as e:
        print(f"❌ Cleanup Failed: {e}")

# WebSocket Helpers
def gen_session():
    return "qs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))

def format_msg(msg):
    m = json.dumps(msg)
    return f"~m~{len(m)}~m~{m}"

def parse_messages(message):
    """
    Parses TradingView WebSocket messages. 
    Handles multi-packet messages (~m~len~m~json~m~len~m~json...)
    Returns list of (symbol, price) updates.
    """
    updates = []
    try:
        parts = message.split("~m~")
        for i in range(2, len(parts), 2):
            raw = parts[i]
            payload = json.loads(raw)
            if payload.get("m") == "qsd":
                p = payload.get("p", [])
                if len(p) >= 2:
                    data = p[1]
                    symbol = data.get("n")
                    v = data.get("v", {})
                    # Last price is preferred, fallback to bid or ask
                    price = v.get("lp") 
                    if price is None: price = v.get("bid")
                    if price is None: price = v.get("ask")
                    
                    if symbol and price is not None:
                        updates.append((symbol, price))
    except Exception:
        pass
    return updates

class TVStreamer:
    def __init__(self, symbols):
        self.symbols = symbols
        self.ws = None
        self.sessions = []

    def on_message(self, ws, message):
        if message.startswith("~h~"):
            ws.send(message)
            return

        updates = parse_messages(message)
        for symbol, price in updates:
            process_tick(symbol, price)

    def on_open(self, ws):
        self.sessions = []
        # TradingView allows ~100 symbols per session. 
        # We'll split our symbols into multiple sessions if needed.
        batch_size = 80 # Using 80 to be safe
        for i in range(0, len(self.symbols), batch_size):
            session = gen_session()
            self.sessions.append(session)
            
            ws.send(format_msg({"m": "quote_create_session", "p": [session]}))
            # Request Last Price, Bid, and Ask to cover all symbol types
            ws.send(format_msg({"m": "quote_set_fields", "p": [session, "lp", "bid", "ask"]}))
            
            batch = self.symbols[i : i + batch_size]
            for sym in batch:
                ws.send(format_msg({"m": "quote_add_symbols", "p": [session, sym]}))
        
        print(f"🛰️ Streaming {len(self.symbols)} symbols across {len(self.sessions)} sessions...")

    def on_error(self, ws, error):
        if "opcode=8" not in str(error):
            print(f"❌ WS Error: {error}")

    def on_close(self, ws, a, b):
        print("🔌 Connection closed. Reconnecting in 10s...")
        time.sleep(10)
        self.start()

    def start(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        self.ws = websocket.WebSocketApp(
            "wss://data.tradingview.com/socket.io/websocket",
            on_message=self.on_message,
            on_open=self.on_open,
            on_error=self.on_error,
            on_close=self.on_close,
            header=headers
        )
        self.ws.run_forever(origin="https://www.tradingview.com")

# =========================
# MAIN
# =========================
def main():
    init_db()
    
    # Initial Sync
    pairs = sync_pairs()
    
    # If sync failed (API error or DB error), try to get from DB
    if not pairs:
        print("⚠️ Sync returned no pairs, attempting to load from database...")
        pairs = get_stored_pairs()
    
    # Final safety fallback: If still no pairs, use hardcoded defaults
    if not pairs:
        print("⚠️ Database empty and sync failed. Using hardcoded fallback lists.")
        pairs = [f"FX_IDC:{p}" for p in (DEFAULT_MAJORS + MANDATORY_EXOTICS)]
    
    if not pairs:
        print("⚠️ No pairs found. Syncing might have failed.")
        return

    # Start Flush Thread
    def flusher():
        while True:
            try:
                flush_candles()
            except Exception as e:
                print(f"⚠️ Flush Error: {e}")
            time.sleep(30) # Check every 30s for completed buckets

    threading.Thread(target=flusher, daemon=True).start()

    # Start Pair Sync Thread (24h)
    def sync_loop():
        while True:
            time.sleep(86400)
            sync_pairs()

    threading.Thread(target=sync_loop, daemon=True).start()

    # Start Cleanup Thread (10m)
    def cleanup_run_loop():
        # First cleanup immediately on start
        try:
            cleanup_old_data()
        except Exception as e:
            print(f"⚠️ Cleanup Initial Error: {e}")
            
        while True:
            time.sleep(600) # Every 10 minutes
            try:
                cleanup_old_data()
            except Exception as e:
                print(f"⚠️ Cleanup Loop Error: {e}")

    threading.Thread(target=cleanup_run_loop, daemon=True).start()

    # Start Streaming
    # Our new TVStreamer handles multi-session internally to bypass the 100-symbol limit.
    streamer = TVStreamer(pairs)
    streamer.start()

if __name__ == "__main__":
    main()