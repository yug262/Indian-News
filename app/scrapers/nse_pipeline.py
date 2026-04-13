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
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
import pytz
import csv
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

# Add the project root to sys.path so we can import app
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from app.db.db import DB_CONFIG

# =========================
# API KEY ROTATION
# =========================
class APIKeyRotator:
    """Manages API key rotation every 2 hours across 30 keys (only during market hours)."""
    def __init__(self, api_keys):
        """
        Initialize with list of API keys.
        Args:
            api_keys: List of 30 API keys
        """
        self.api_keys = api_keys if api_keys else []
        self.current_index = 0
        self.last_rotation = datetime.now()
        self.rotation_interval = timedelta(hours=2)
        self.lock = threading.Lock()
        self.is_market_open = False  # Only rotate during market hours
        
    def set_market_status(self, is_open):
        """Update market status to control rotation."""
        with self.lock:
            self.is_market_open = is_open
        
    def get_current_key(self):
        """Get the current API key and rotate if 2 hours have passed (only during market hours)."""
        with self.lock:
            now = datetime.now()
            # Only rotate if market is open and 2 hours have passed since last rotation
            if self.is_market_open and now - self.last_rotation >= self.rotation_interval:
                prev_index = self.current_index
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                self.last_rotation = now
                # Check if we wrapped around from key #30 to key #1
                if self.current_index == 0 and prev_index > 0:
                    print(f"🔄 [API ROTATION] Completed full cycle! Restarting from key #1 at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
                else:
                    print(f"🔄 [API ROTATION] Switched to key #{self.current_index + 1} at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
            
            return self.api_keys[self.current_index] if self.api_keys else None
    
    def get_all_keys_info(self):
        """Get information about all keys and current key."""
        with self.lock:
            return {
                "total_keys": len(self.api_keys),
                "current_key_index": self.current_index,
                "current_key_number": self.current_index + 1,
                "last_rotation": self.last_rotation,
                "next_rotation": self.last_rotation + self.rotation_interval,
            }

# Initialize API Key Rotator with 30 keys from environment or hardcoded
def init_api_rotator():
    """Initialize API key rotator with keys from environment variables or config."""
    api_keys = []
    
    # Try to load from environment variables (API_KEY_1, API_KEY_2, ..., API_KEY_30)
    for i in range(1, 38):
        key = os.getenv(f"FINNHUB_API_KEY_{i}", None)
        if key:
            api_keys.append(key)
    
    # If not enough keys from env, you can set them here or load from a config file
    if len(api_keys) < 30:
        print(f"⚠️ Only {len(api_keys)} API keys loaded from environment (need 30)")
        # Uncomment and add your keys below if needed:
        # api_keys = [
        #     "key1", "key2", ..., "key30"
        # ]
    
    if not api_keys:
        print("\n" + "="*60)
        print("⚠️  WARNING: No API keys configured!")
        print("   Set FINNHUB_API_KEY_1 through FINNHUB_API_KEY_37 env vars.")
        print("   Running with dummy keys — API calls will fail with 401.")
        print("="*60 + "\n")
        api_keys = [f"dummy_key_{i}" for i in range(1, 38)]  # Fallback dummy keys
    
    return APIKeyRotator(api_keys)

# Global API rotator instance
API_ROTATOR = None

# =========================
# CONFIGURATION
# =========================

# NSE Equity list URL (Official CSV containing all traded companies)
NSE_CSV_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# =========================
# MARKET HOURS LOGIC
# =========================
NSE_HOLIDAYS_2026 = {
    "2026-01-15": "Municipal Corporation Election",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas"
}

NSE_HOLIDAYS = {}

def fetch_nse_holidays():
    """Fetch trading holidays from NSE API."""
    global NSE_HOLIDAYS
    print("🔄 Fetching latest NSE holidays...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.nseindia.com/resources/exchange-trading-holidays",
            "Accept": "*/*"
        }
        
        # Add rotating API key to headers if available
        if API_ROTATOR:
            api_key = API_ROTATOR.get_current_key()
            headers["X-API-Key"] = api_key
            
        session = requests.Session()
        # Visit home page first for cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        res = session.get("https://www.nseindia.com/api/holiday-master?type=trading", headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            new_holidays = {}
            # CM segment contains Equity holidays
            for segment in ["CM", "EQUITY"]:
                if segment in data:
                    for item in data[segment]:
                        try:
                            # Format: "26-Jan-2026"
                            dt = datetime.strptime(item["tradingDate"], "%d-%b-%Y")
                            iso_date = dt.strftime("%Y-%m-%d")
                            new_holidays[iso_date] = item["description"]
                        except: continue
                    break # Use first found segment
            
            if new_holidays:
                NSE_HOLIDAYS = new_holidays
                print(f"✅ Successfully fetched {len(NSE_HOLIDAYS)} holidays from NSE.")
                return True
        print(f"❌ Could not parse NSE holidays (Status: {res.status_code}). Using fallback.")
    except Exception as e:
        print(f"❌ Error fetching holidays: {e}. Using fallback.")
    
    if not NSE_HOLIDAYS:
        NSE_HOLIDAYS = NSE_HOLIDAYS_2026.copy()
        print("ℹ️ Using hardcoded 2026 holiday fallback.")
    return False

class MarketStatus(NamedTuple):
    is_open: bool
    reason: str
    sleep_secs: int

def get_market_status() -> MarketStatus:
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    date_str = now.strftime("%Y-%m-%d")
    
    # Use dynamically fetched holidays if available, else fallback
    holidays = NSE_HOLIDAYS if NSE_HOLIDAYS else NSE_HOLIDAYS_2026
    
    is_open = False
    reason = "Market Hours"
    
    # 1. Check Holiday
    if date_str in holidays:
        is_open = False
        reason = f"Holiday: {holidays[date_str]}"
    # 2. Check Weekend (5=Sat, 6=Sun)
    elif now.weekday() >= 5:
        is_open = False
        reason = "Weekend"
    # 3. Check Time (9:15 AM to 3:32 PM)
    else:
        start_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        end_time = now.replace(hour=15, minute=32, second=0, microsecond=0)
        
        if start_time <= now <= end_time:
            is_open = True
            reason = "Live Session"
        elif now < start_time:
            is_open = False
            reason = "Pre-Market"
        else:
            is_open = False
            reason = "Post-Market"
            
    if is_open:
        return MarketStatus(True, reason, 0)
        
    # Calculate Wait Seconds until next 9:15 AM
    target = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
        
    while target.weekday() >= 5 or target.strftime("%Y-%m-%d") in holidays:
        target += timedelta(days=1)
        
    sleep_secs = int((target - now).total_seconds())
    return MarketStatus(False, reason, sleep_secs)

# =========================
# DATABASE SETUP
# =========================
def check_db():
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            pass  # Connection auto-closes via context manager
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        sys.exit(1)

# =========================
# COMPANIES SYNC (24H)
# =========================
def sync_companies():
    print("🔄 Syncing NSE companies list...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        # Add rotating API key to headers if available
        if API_ROTATOR:
            api_key = API_ROTATOR.get_current_key()
            headers["X-API-Key"] = api_key
            
        res = requests.get(NSE_CSV_URL, headers=headers, timeout=20)
        
        # Sometime NSE requires cookies to be established, so we make a generic request first
        if res.status_code != 200:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers, timeout=15)
            res = session.get(NSE_CSV_URL, headers=headers, timeout=20)
            
        if res.status_code == 200:
            csv_data = StringIO(res.text)
            reader = csv.DictReader(csv_data)
            
            inserted = 0
            with psycopg2.connect(**DB_CONFIG) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    for row in reader:
                        symbol = row.get("SYMBOL", "").strip()
                        company_name = row.get("NAME OF COMPANY", "").strip()
                        series = row.get(" SERIES", "").strip()  # Handle possible leading space in header
                        if not series:
                            series = row.get("SERIES", "").strip()
                            
                        # We only track EQ series (regular equity) to avoid duplicates with ETFs/bonds, unless user wants all
                        # We will insert all rows but mainly focus on tracking prices for EQs if we want to filter.
                        # Wait, the prompt says "all the nse company", so we store all of them.
                        if symbol:
                            cur.execute("""
                            INSERT INTO nse_companies (symbol, company_name, series) 
                            VALUES (%s, %s, %s)
                            ON CONFLICT (symbol) DO NOTHING
                            """, (symbol, company_name, series))
                            inserted += 1
                            
            print(f"✅ Synced {inserted} new NSE companies. (Updates skipped on conflict).")
            return get_stored_companies()
        else:
            print(f"❌ Failed to fetch NSE CSV. Status code: {res.status_code}")
    except Exception as e:
        print(f"❌ Error syncing companies: {e}")
    
    return []

def get_stored_companies():
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM nse_companies WHERE series IN ('EQ', 'BE', 'SM', 'ST', 'BZ')")
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"❌ Error getting stored companies: {e}")
        return []

# =========================
# TRADINGVIEW ENGINE
# =========================
candles = {}  # (symbol, bucket) -> {open, high, low, close, count}
candles_lock = threading.Lock()

def get_bucket(ts):
    minute = (ts.minute // 3) * 3
    return ts.replace(minute=minute, second=0, microsecond=0)

def process_tick(symbol, price):
    if price is None or price <= 0:
        return
        
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
    to_delete = []
    
    with candles_lock:
        stored_items = list(candles.items())

    if not stored_items:
        return

    with psycopg2.connect(**DB_CONFIG) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for (symbol, t), c in stored_items:
                # Save candle once the 3-minute bucket has passed
                if now >= t + timedelta(minutes=3):
                    # We strip the "NSE:" prefix for storage to keep db clean and match company table
                    clean_symbol = symbol.replace("NSE:", "")
                    cur.execute("""
                    INSERT INTO nse_candles_3m (symbol, time, open, high, low, close)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, time) DO NOTHING
                    """, (clean_symbol, t, c["open"], c["high"], c["low"], c["close"]))
                    to_delete.append((symbol, t))
                    print(f"💾 Saved 3m candle for {clean_symbol} at {t}")

    with candles_lock:
        for key in to_delete:
            if key in candles:
                del candles[key]

def cleanup_old_data():
    """Remove candles older than 24 hours from nse_candles_3m."""
    print("🧹 Cleaning up old NSE candle data...")
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DELETE FROM nse_candles_3m WHERE time < %s", (datetime.now(timezone.utc) - timedelta(hours=24),))
                print(f"✅ Cleanup complete: Removed {cur.rowcount} old candles.")
    except Exception as e:
        print(f"❌ Cleanup Failed: {e}")

# WebSocket Helpers
def gen_session():
    return "qs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))

def format_msg(msg):
    m = json.dumps(msg)
    return f"~m~{len(m)}~m~{m}"

def parse_messages(message):
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
                    # Last price is preferred
                    price = v.get("lp") 
                    if price is None: price = v.get("bid")
                    if price is None: price = v.get("ask")
                    
                    if symbol and price is not None:
                        updates.append((symbol, price))
    except Exception:
        pass
    return updates

class TVStreamer:
    def __init__(self, symbols, stream_id=1):
        # Format symbols for TV: 'TCS' -> 'NSE:TCS'
        self.symbols = [f"NSE:{s}" for s in symbols]
        self.stream_id = stream_id
        self.ws = None
        self.sessions = []
        self._should_run = True

    def on_message(self, ws, message):
        if message.startswith("~h~"):
            ws.send(message)
            return

        updates = parse_messages(message)
        for symbol, price in updates:
            process_tick(symbol, price)

    def on_open(self, ws):
        self.sessions = []
        batch_size = 80
        for i in range(0, len(self.symbols), batch_size):
            session = gen_session()
            self.sessions.append(session)
            
            ws.send(format_msg({"m": "quote_create_session", "p": [session]}))
            ws.send(format_msg({"m": "quote_set_fields", "p": [session, "lp", "bid", "ask"]}))
            
            batch = self.symbols[i : i + batch_size]
            for sym in batch:
                ws.send(format_msg({"m": "quote_add_symbols", "p": [session, sym]}))
        
        print(f"🛰️ [Stream {self.stream_id}] Streaming {len(self.symbols)} symbols out of total.")

    def on_error(self, ws, error):
        if "opcode=8" not in str(error):
            print(f"❌ [Stream {self.stream_id}] WS Error: {error}")

    def on_close(self, ws, a, b):
        print(f"🔌 [Stream {self.stream_id}] Connection closed.")

    def start_loop(self):
        while True:
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            
            status = get_market_status()
            is_open, reason, sleep_secs = status.is_open, status.reason, status.sleep_secs
            
            if is_open:
                print(f"🟢 [Stream {self.stream_id}] Market {reason} at {now.strftime('%H:%M:%S')}. Initiating connection...")
                API_ROTATOR.set_market_status(True)  # Enable API rotation during market hours
                self.start() # Blocks here until closed/crashed
                API_ROTATOR.set_market_status(False)  # Disable API rotation when market closes
                print(f"⚠️ [Stream {self.stream_id}] Disconnected at {datetime.now(ist).strftime('%H:%M:%S')}. Rechecking in 10s...")
                time.sleep(10)
            else:
                # Log once and sleep until open
                API_ROTATOR.set_market_status(False)  # Ensure market status is off
                target_dt = now + timedelta(seconds=sleep_secs)
                target_time = target_dt.strftime("%Y-%m-%d %H:%M:%S")
                print(f"🔴 [Stream {self.stream_id}] Market CLOSED ({reason}).")
                print(f"📅 Reopening at: {target_time} IST")
                print(f"💤 Sleeping for {sleep_secs // 3600}h {(sleep_secs % 3600) // 60}m {(sleep_secs % 60)}s...")
                time.sleep(sleep_secs)

    def start(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://www.tradingview.com"
        }
        self.ws = websocket.WebSocketApp(
            "wss://data.tradingview.com/socket.io/websocket",
            on_message=self.on_message,
            on_open=self.on_open,
            on_error=self.on_error,
            on_close=self.on_close,
            header=headers
        )
        self.ws.run_forever()

# =========================
# MAIN
# =========================
def main():
    global API_ROTATOR
    
    # Initialize API Key Rotator
    API_ROTATOR = init_api_rotator()
    print(f"✅ API Key Rotator initialized with {len(API_ROTATOR.api_keys)} keys")
    print(f"🔄 Rotation interval: Every 2 hours")
    
    check_db()
    
    # Initial Holiday Fetch
    fetch_nse_holidays()
    
    # Initial Sync
    companies = sync_companies()
    if not companies:
        companies = get_stored_companies()
    
    if not companies:
        print("⚠️ No NSE companies found. Syncing might have failed.")
        return

    print(f"📊 Preparing to track {len(companies)} NSE companies...")

    # Start Flush Thread
    def flusher():
        while True:
            try:
                flush_candles()
            except Exception as e:
                print(f"⚠️ Flush Error: {e}")
            time.sleep(30) # Check every 30s for completed buckets

    threading.Thread(target=flusher, daemon=True).start()

    # Start Company Sync & Holiday Sync Thread (24h)
    def sync_loop():
        while True:
            time.sleep(86400)
            sync_companies()
            fetch_nse_holidays()

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

    # Start API Key Rotation Monitor Thread (logs status every 30 minutes)
    def api_rotation_monitor():
        while True:
            time.sleep(1800)  # Every 30 minutes
            info = API_ROTATOR.get_all_keys_info()
            print(f"\n📊 [API ROTATION STATUS]")
            print(f"   Total Keys: {info['total_keys']}")
            print(f"   Current Key: #{info['current_key_number']}")
            print(f"   Last Rotation: {info['last_rotation'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   Next Rotation: {info['next_rotation'].strftime('%Y-%m-%d %H:%M:%S')}\n")

    threading.Thread(target=api_rotation_monitor, daemon=True).start()

    # Start Streaming
    # 2400 symbols is a lot, so we split them into multiple concurrent TVStreamer threads
    # 800 symbols per thread
    chunk_size = 800
    streamers = []
    
    for i in range(0, len(companies), chunk_size):
        chunk = companies[i:i+chunk_size]
        stream_id = (i // chunk_size) + 1
        streamer = TVStreamer(chunk, stream_id=stream_id)
        streamers.append(streamer)
        
        # Start each streamer in its own loop thread
        t = threading.Thread(target=streamer.start_loop, daemon=True)
        t.start()
        time.sleep(1) # stagger connections a bit to be safe
        
    print(f"🚀 Started {len(streamers)} WebSocket streams for NSE prices.")

    # Prevent main thread from exiting
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Shutting down NSE pipeline...")

if __name__ == "__main__":
    main()
