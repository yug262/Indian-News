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
import pytz
import csv
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from app.core.db import DB_CONFIG

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

def get_market_status():
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
        return True, reason, 0
        
    # Calculate Wait Seconds until next 9:15 AM
    target = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
        
    while target.weekday() >= 5 or target.strftime("%Y-%m-%d") in holidays:
        target += timedelta(days=1)
        
    sleep_secs = int((target - now).total_seconds())
    return False, reason, sleep_secs

# =========================
# DATABASE SETUP
# =========================
def check_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
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
        conn = psycopg2.connect(**DB_CONFIG)
        symbols = []
        with conn.cursor() as cur:
            cur.execute("SELECT symbol FROM nse_companies WHERE series IN ('EQ', 'BE', 'SM', 'ST', 'BZ')")
            symbols = [row[0] for row in cur.fetchall()]
        conn.close()
        return symbols
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
    conn.close()

def cleanup_old_data():
    """Remove candles older than 24 hours from nse_candles_3m."""
    print("🧹 Cleaning up old NSE candle data...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Delete candles older than 24 hours
            cur.execute("DELETE FROM nse_candles_3m WHERE time < %s", (datetime.utcnow() - timedelta(hours=24),))
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
            
            is_open, reason, sleep_secs = get_market_status()
            if is_open:
                print(f"🟢 [Stream {self.stream_id}] Market {reason} at {now.strftime('%H:%M:%S')}. Initiating connection...")
                self.start() # Blocks here until closed/crashed
                print(f"⚠️ [Stream {self.stream_id}] Disconnected at {datetime.now(ist).strftime('%H:%M:%S')}. Rechecking in 10s...")
                time.sleep(10)
            else:
                # Log once and sleep until open
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
