"""
Global Macro Intelligence — Tools Layer
Free data sources only.

Notes:
- Uses yfinance, CoinGecko, Alternative.me, and your own DB
- Adds:
  - better title normalization
  - theme detection
  - repetition / fatigue scoring
  - escalation detection
  - duplicate / priced-in checks
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import re
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import requests
import yfinance as yf
from bs4 import BeautifulSoup
import ccxt
import pandas as pd

from app.core.db import fetch_all


COINGECKO_URL = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

DEFAULT_TIMEOUT = 10


# =========================================================
# HTTP HELPERS
# =========================================================

def _http_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None


# =========================================================
# GENERIC HELPERS
# =========================================================

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


# =========================================================
# TITLE / EVENT UNDERSTANDING
# =========================================================

def _normalize_event_title(title: str) -> str:
    """
    Strong normalization for title similarity matching.
    """
    t = (title or "").lower().strip()

    # Standardize common market text
    t = t.replace("u.s.", "us")
    t = t.replace("u.s", "us")
    t = t.replace("%", " percent ")
    t = t.replace("&", " and ")

    # Replace numbers for more stable matching
    t = re.sub(r"\d+(?:\.\d+)?", " <num> ", t)

    # Remove punctuation
    t = re.sub(r"[^a-z0-9\s<>]", " ", t)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_theme(title: str) -> str:
    """
    Lightweight theme classifier for repetition detection.
    """
    t = (title or "").lower()

    if any(x in t for x in [
        "nonfarm payroll", "payrolls", "jobs report", "job losses",
        "unemployment rate", "labor market", "employment data",
        "jobless claims", "employment growth"
    ]):
        return "us_labor_market"

    if any(x in t for x in [
        "cpi", "inflation", "consumer price index", "ppi",
        "core inflation", "price pressures", "disinflation"
    ]):
        return "us_inflation"

    if any(x in t for x in [
        "fed", "fomc", "powell", "rate cut", "rate hike",
        "raises rates", "cuts rates", "monetary policy",
        "federal reserve"
    ]):
        return "fed_policy"

    if any(x in t for x in [
        "ecb", "boe", "boj", "snb", "lagarde", "bailey", "ueda"
    ]):
        return "other_central_bank_policy"

    if any(x in t for x in [
        "gdp", "retail sales", "industrial production",
        "pmi", "ism", "durable goods", "consumer confidence"
    ]):
        return "macro_growth_data"

    if any(x in t for x in [
        "oil", "opec", "crude", "brent", "wti", "supply disruption",
        "production cut", "refinery", "energy market"
    ]):
        return "oil_market"

    if any(x in t for x in [
        "bitcoin etf", "spot etf", "ethereum etf", "sec",
        "crypto regulation", "digital asset regulation"
    ]):
        return "crypto_regulation_etf"

    if any(x in t for x in [
        "tariff", "sanctions", "trade war", "import duty", "export controls"
    ]):
        return "trade_policy"

    if any(x in t for x in [
        "missile", "attack", "strike", "airstrike", "ceasefire",
        "troops", "military", "explosion", "conflict", "war"
    ]):
        return "geopolitical_conflict"

    return "general"


def has_escalation_words(title: str) -> bool:
    """
    Strict escalation detector for filter use.
    Only flags genuinely event-like escalation words.
    """
    t = (title or "").lower()

    escalation_words = [
        "sanctions",
        "tariffs",
        "export ban",
        "capital controls",
        "intervention",
        "rate hike",
        "rate cut",
        "emergency meeting",
        "bankruptcy",
        "default",
        "shipping halted",
        "waterway closed",
        "strait closed",
        "pipeline attack",
        "refinery attack",
        "missile strike",
        "new front",
        "new country joins",
        "exchange halt",
        "stablecoin depeg",
    ]

    return any(word in t for word in escalation_words)


def detect_reaction_headline(title: str) -> dict:
    """
    Detect whether a headline is primarily describing market price action.
    """
    t = (title or "").lower().strip()

    move_verbs = [
        "rises", "rise", "falls", "fall", "drops", "drop", "jumps", "jump",
        "surges", "surge", "slides", "slide", "rebounds", "rebound",
        "tumbles", "tumble", "gains", "gain", "selloff", "sells off",
        "rallies", "rally", "slips", "slip"
    ]

    catalyst_words = [
        "after", "amid", "on", "as", "following", "due to", "because of"
    ]

    pct_match = re.search(r'(\d+(\.\d+)?)\s*%', t)
    headline_move_pct = float(pct_match.group(1)) if pct_match else None

    has_move_verb = any(v in t for v in move_verbs)
    has_catalyst = any(w in t for w in catalyst_words)

    # Much broader than before
    reaction_headline = bool(
        has_move_verb and (
            headline_move_pct is not None
            or has_catalyst
            or t.startswith(("oil ", "gold ", "bitcoin ", "stocks ", "dollar ", "usd/", "eur/", "gbp/", "spx", "nasdaq"))
        )
    )

    return {
        "reaction_headline": reaction_headline,
        "headline_move_pct": headline_move_pct,
        "has_new_catalyst": has_catalyst,
    }


# =========================================================
# REACTION / PRICED-IN HELPERS
# =========================================================

def classify_reaction_status(reaction_pct: float | None, atr_pct_reference: float | None) -> str:
    """
    Compare move vs ATR to label priced-in state.
    """
    if reaction_pct is None:
        return "unknown"

    if atr_pct_reference is None or atr_pct_reference <= 0:
        return "normal_reaction"

    ratio = abs(reaction_pct) / atr_pct_reference

    if ratio < 0.3:
        return "underreacted"
    if ratio < 1.0:
        return "normal_reaction"
    return "fully_priced"


def get_time_decay_penalty(published_at: datetime | None) -> int:
    if not published_at:
        return 0

    age_hours = (datetime.now(timezone.utc) - _to_utc(published_at)).total_seconds() / 3600.0

    if age_hours < 0.5:
        return 0
    if age_hours < 2:
        return 1
    if age_hours < 8:
        return 2
    return 3


def _safe_last_close(symbol: str) -> float | None:
    try:
        t = yf.Ticker(symbol)
        h = t.history(period="5d")
        if h is None or h.empty:
            return None
        return float(h["Close"].iloc[-1])
    except Exception:
        return None


def _is_crypto(symbol: str) -> bool:
    s = (symbol or "").upper()
    return "-USD" in s or s in ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE"]

def _crypto_to_binance(symbol: str) -> str:
    base = symbol.split("-")[0].upper()
    if base == "USD": return "USDC/USDT"
    return f"{base}/USDT"

def get_asset_atr(symbol: str, period: int = 14) -> dict:
    if _is_crypto(symbol):
        try:
            binance_sym = _crypto_to_binance(symbol)
            exchange = ccxt.binance()
            ohlcv = exchange.fetch_ohlcv(binance_sym, timeframe='1d', limit=40)
            if ohlcv and len(ohlcv) >= period:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df["H-L"] = df["High"] - df["Low"]
                df["H-PC"] = (df["High"] - df["Close"].shift(1)).abs()
                df["L-PC"] = (df["Low"] - df["Close"].shift(1)).abs()
                
                tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
                atr = tr.rolling(period).mean().iloc[-1]
                price = df["Close"].iloc[-1]
                
                if price != 0:
                    return {
                        "atr_value": round(float(atr), 6),
                        "atr_pct_reference": round(float((atr / price) * 100), 6),
                    }
        except Exception:
            pass # Fallback to yfinance if error

    try:
        t = yf.Ticker(symbol)
        df = t.history(period="30d")
        if df is None or df.empty or len(df) < period:
            return {}

        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = (df["High"] - df["Close"].shift(1)).abs()
        df["L-PC"] = (df["Low"] - df["Close"].shift(1)).abs()

        tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        price = df["Close"].iloc[-1]

        if price == 0:
            return {}

        atr_pct = (atr / price) * 100

        return {
            "atr_value": round(float(atr), 6),
            "atr_pct_reference": round(float(atr_pct), 6),
        }
    except Exception:
        return {}


def calculate_reaction(symbol: str, published_iso: str) -> dict:
    """
    Calculates move from first available candle at/after publish time to now.
    Falls back to daily if intraday unavailable.
    """
    try:
        pub_dt = datetime.fromisoformat((published_iso or "").strip())
        pub_dt = _to_utc(pub_dt)
        now_dt = datetime.now(timezone.utc)

        if _is_crypto(symbol):
            try:
                binance_sym = _crypto_to_binance(symbol)
                exchange = ccxt.binance()
                since_ts = int((pub_dt - timedelta(hours=6)).timestamp() * 1000)
                ohlcv = exchange.fetch_ohlcv(binance_sym, timeframe='15m', since=since_ts, limit=1000)
                
                if ohlcv:
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                    after = df[df['datetime'] >= pub_dt]
                    
                    if not after.empty:
                        news_price = float(after["Close"].iloc[0])
                        used_ts = after['datetime'].iloc[0]
                        current_price = float(df["Close"].iloc[-1])
                        reaction_pct = ((current_price - news_price) / news_price) * 100 if news_price else None
                        
                        return {
                            "news_price": round(news_price, 6),
                            "current_price": round(current_price, 6),
                            "reaction_pct": round(reaction_pct, 6) if reaction_pct is not None else None,
                            "method": "ccxt_intraday_15m",
                            "used_timestamp_utc": used_ts.isoformat(),
                        }
            except Exception:
                pass # Fallback to yfinance if ccxt fails

        t = yf.Ticker(symbol)

        # 1) Try intraday 15m
        start = pub_dt - timedelta(hours=6)
        end = now_dt + timedelta(minutes=5)

        df = t.history(start=start, end=end, interval="15m")
        if df is not None and not df.empty:
            if getattr(df.index, "tz", None) is None:
                df.index = df.index.tz_localize(timezone.utc)
            else:
                df.index = df.index.tz_convert(timezone.utc)

            after = df[df.index >= pub_dt]
            if not after.empty:
                news_price = float(after["Close"].iloc[0])
                used_ts = after.index[0]
                current_price = float(df["Close"].iloc[-1])
                reaction_pct = ((current_price - news_price) / news_price) * 100 if news_price else None

                return {
                    "news_price": round(news_price, 6),
                    "current_price": round(current_price, 6),
                    "reaction_pct": round(reaction_pct, 6) if reaction_pct is not None else None,
                    "method": "intraday_15m",
                    "used_timestamp_utc": used_ts.isoformat(),
                }

        # 2) Daily fallback
        df_d = t.history(period="30d", interval="1d")
        if df_d is not None and not df_d.empty:
            idx = df_d.index
            if getattr(idx, "tz", None) is None:
                pub_date = pub_dt.date()
                row = df_d[df_d.index.date >= pub_date]
                if not row.empty:
                    news_price = float(row["Close"].iloc[0])
                    used_ts = row.index[0]
                else:
                    news_price = float(df_d["Close"].iloc[-1])
                    used_ts = df_d.index[-1]
            else:
                df_d.index = df_d.index.tz_convert(timezone.utc)
                row = df_d[df_d.index >= pub_dt]
                if not row.empty:
                    news_price = float(row["Close"].iloc[0])
                    used_ts = row.index[0]
                else:
                    news_price = float(df_d["Close"].iloc[-1])
                    used_ts = df_d.index[-1]

            current_price = float(df_d["Close"].iloc[-1])
            reaction_pct = ((current_price - news_price) / news_price) * 100 if news_price else None

            return {
                "news_price": round(news_price, 6),
                "current_price": round(current_price, 6),
                "reaction_pct": round(reaction_pct, 6) if reaction_pct is not None else None,
                "method": "daily",
                "used_timestamp_utc": used_ts.isoformat() if hasattr(used_ts, "isoformat") else str(used_ts),
            }

        # 3) Last close fallback
        current_price = _safe_last_close(symbol)
        if current_price is not None:
            return {
                "news_price": None,
                "current_price": round(current_price, 6),
                "reaction_pct": None,
                "method": "last_close",
                "used_timestamp_utc": None,
            }

        return {"method": "failed"}

    except Exception as e:
        return {"method": "failed", "error": str(e)}


# =========================================================
# FOREX / CRYPTO / GLOBAL MARKET TOOLS
# =========================================================

def get_forex_prices(pairs: list[str]) -> dict:
    symbol_map = {
        "DXY": "DX-Y.NYB",
        "GOLD": "GC=F",
        "OIL": "CL=F",
        "SILVER": "SI=F",
    }

    out = {}
    for pair in pairs:
        symbol = symbol_map.get(pair.upper(), pair.replace("/", "").upper() + "=X")
        out[pair] = _safe_last_close(symbol)
    return out


def get_crypto_prices(coin_ids: list[str] | None = None) -> dict:
    if not coin_ids:
        coin_ids = ["bitcoin", "ethereum"]

    cg_to_binance = {
        "bitcoin": "BTC/USDT",
        "ethereum": "ETH/USDT",
        "solana": "SOL/USDT",
        "ripple": "XRP/USDT",
        "cardano": "ADA/USDT",
        "dogecoin": "DOGE/USDT",
        "avalanche-2": "AVAX/USDT",
        "chainlink": "LINK/USDT",
        "polkadot": "DOT/USDT",
        "litecoin": "LTC/USDT",
        "shiba-inu": "SHIB/USDT",
        "polygon": "MATIC/USDT",
        "matic-network": "MATIC/USDT",
        "uniswap": "UNI/USDT"
    }

    try:
        exchange = ccxt.binance()
        to_fetch = []
        for cid in coin_ids:
            sym = cg_to_binance.get(cid.lower())
            if not sym and ("usd" in cid.lower() or "usdt" in cid.lower()):
                sym = cid.upper().replace("-USD", "/USDT").replace("-", "/")
            if sym:
                to_fetch.append(sym)
                
        to_fetch = list(set(to_fetch))
        out = {}
        if to_fetch:
            tickers = exchange.fetch_tickers(to_fetch)
            for cid in coin_ids:
                sym = cg_to_binance.get(cid.lower())
                if not sym:
                    sym = cid.upper().replace("-USD", "/USDT").replace("-", "/")
                
                if sym in tickers and tickers[sym]['last'] is not None:
                    out[cid] = float(tickers[sym]['last'])
                else:
                    out[cid] = None
        return out
    except Exception as e:
        print(f"ccxt get_crypto_prices error: {e}")
    return {}


def get_global_markets() -> dict:
    symbols = {
        "SPX": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "VIX": "^VIX",
        "US10Y": "^TNX",
        "GOLD": "GC=F",
        "OIL": "CL=F",
        "DXY": "DX-Y.NYB",
    }

    out = {}
    for name, sym in symbols.items():
        out[name] = _safe_last_close(sym)
    return out


def get_market_sentiment() -> dict:
    resp = _http_get(FEAR_GREED_URL)
    if resp is None:
        return {}

    try:
        data = resp.json()["data"][0]
        return {
            "fear_greed_value": int(data["value"]),
            "fear_greed_classification": data["value_classification"],
        }
    except Exception:
        return {}


def get_macro_context() -> dict:
    try:
        dxy = yf.Ticker("DX-Y.NYB").history(period="5d")["Close"]
        us10y = yf.Ticker("^TNX").history(period="5d")["Close"]

        if dxy.empty or us10y.empty:
            return {}

        return {
            "dxy_trend_5d_pct": round(float(dxy.pct_change().sum() * 100), 2),
            "us10y_trend_5d_pct": round(float(us10y.pct_change().sum() * 100), 2),
        }
    except Exception:
        return {}


# =========================================================
# ECONOMIC CALENDAR
# =========================================================

def get_economic_calendar() -> dict:
    """
    Scrapes Investing.com economic calendar for high-impact events.
    This may break if their HTML changes.
    """
    try:
        resp = _http_get(
            "https://www.investing.com/economic-calendar/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp is None:
            return {"error": "request_failed"}

        soup = BeautifulSoup(resp.text, "html.parser")
        events = []

        rows = soup.select("tr.js-event-item")
        for row in rows[:20]:
            try:
                event_el = row.select_one(".event")
                country_el = row.select_one(".flagCur")
                time_el = row.select_one(".time")

                if not event_el or not country_el or not time_el:
                    continue

                impact_icons = row.select(".sentiment i.grayFullBullishIcon")
                impact = len(impact_icons)

                events.append({
                    "country": country_el.text.strip(),
                    "event": event_el.text.strip(),
                    "impact_level": impact,
                    "time": time_el.text.strip(),
                })
            except Exception:
                continue

        return {
            "events_found": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": str(e)}


# =========================================================
# RATE DIFFERENTIALS
# =========================================================

def get_interest_rate_differentials() -> dict:
    """
    Safe minimal version. Add more yields only if source is reliable.
    """
    try:
        us10y = _safe_last_close("^TNX")
        return {
            "us_10y": us10y,
            "note": "Only US10Y included by default. Add more sovereign yields only from reliable sources.",
        }
    except Exception:
        return {}


# =========================================================
# SOURCE CREDIBILITY
# =========================================================

def get_news_source_credibility(source: str) -> dict:
    tier1 = ["reuters", "bloomberg", "wsj", "financial times", "ft.com", "ap", "cnbc-tv18"]
    tier2 = ["cnbc", "marketwatch", "coindesk", "the block", "theblock", "fxstreet", "investing.com", "forexlive"]

    s = (source or "").lower()

    if any(x in s for x in tier1):
        return {"credibility": "High"}
    if any(x in s for x in tier2):
        return {"credibility": "Medium"}
    return {"credibility": "Unknown"}


# =========================================================
# DUPLICATE / PRICED-IN / FATIGUE CHECKS
# =========================================================

def search_recent_news(
    title: str,
    current_news_id: int | None = None,
    hours_back: int = 48,
    similarity_threshold: float = 0.86
) -> dict:
    """
    Best-match duplicate / priced-in check for filter/analysis agents.
    Excludes the current row if current_news_id is provided.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        rows = fetch_all(
            """
            SELECT id, title, published
            FROM news
            WHERE published >= %s
            ORDER BY published DESC
            LIMIT 400
            """,
            (since,),
        )

        best = {"score": 0.0, "title": None, "published": None}

        for row in rows:
            row_id = row.get("id")
            if current_news_id is not None and row_id == current_news_id:
                continue

            other_title_raw = row.get("title") or ""
            score = _headline_similarity_score(title, other_title_raw)

            if score > best["score"]:
                best = {
                    "score": score,
                    "title": other_title_raw,
                    "published": row.get("published"),
                }

        if best["title"] is None:
            return {
                "priced_in": False,
                "match_score": 0.0,
                "matched_title": None,
                "matched_hours_ago": None,
            }

        older_than_hrs = None
        if best["published"]:
            older_than_hrs = (
                datetime.now(timezone.utc) - _to_utc(best["published"])
            ).total_seconds() / 3600.0

        priced_in = bool(
            best["score"] >= similarity_threshold
            and older_than_hrs is not None
            and older_than_hrs >= 4
        )

        return {
            "priced_in": priced_in,
            "match_score": round(best["score"], 3),
            "matched_title": best["title"],
            "matched_hours_ago": round(older_than_hrs, 2) if older_than_hrs is not None else None,
        }

    except Exception as e:
        return {
            "priced_in": False,
            "match_score": 0.0,
            "matched_title": None,
            "matched_hours_ago": None,
            "note": f"db_check_failed: {e}",
        }


def get_similar_news_counts(title: str, current_news_id: int | None = None) -> dict:
    """
    Count short-term repetition for strict filtering.
    Returns both near-duplicate repetition and broad-theme repetition.
    """
    try:
        rows = fetch_all(
            """
            SELECT id, title, published
            FROM news
            WHERE published > NOW() - INTERVAL '24 hours'
            """
        )

        now = datetime.now(timezone.utc)
        base_theme = detect_theme(title)

        similar_6h = 0
        similar_12h = 0
        similar_24h = 0
        theme_6h = 0
        theme_12h = 0
        theme_24h = 0

        for row in rows:
            row_id = row.get("id")
            other_title_raw = row.get("title", "")
            published = row.get("published")

            if current_news_id is not None and row_id == current_news_id:
                continue
            if not other_title_raw or not published:
                continue

            published_utc = _to_utc(published)
            age_seconds = (now - published_utc).total_seconds()
            if age_seconds < 0:
                continue

            sim_score = _headline_similarity_score(title, other_title_raw)
            other_theme = detect_theme(other_title_raw)

            if sim_score >= 0.84:
                similar_24h += 1
                if age_seconds <= 12 * 3600:
                    similar_12h += 1
                if age_seconds <= 6 * 3600:
                    similar_6h += 1
            elif sim_score >= 0.74 and base_theme != "general" and other_theme == base_theme:
                similar_24h += 1
                if age_seconds <= 12 * 3600:
                    similar_12h += 1
                if age_seconds <= 6 * 3600:
                    similar_6h += 1

            if base_theme != "general" and other_theme == base_theme:
                theme_24h += 1
                if age_seconds <= 12 * 3600:
                    theme_12h += 1
                if age_seconds <= 6 * 3600:
                    theme_6h += 1

        duplicate_score = min(1.0, (similar_6h * 0.40) + (similar_12h * 0.22) + (similar_24h * 0.08))
        theme_score = min(1.0, (theme_6h * 0.18) + (theme_12h * 0.10) + (theme_24h * 0.04))
        repetition_pressure = min(1.0, duplicate_score * 0.7 + theme_score * 0.3)

        return {
            "similar_news_6h": similar_6h,
            "similar_news_12h": similar_12h,
            "similar_news_24h": similar_24h,
            "theme_news_6h": theme_6h,
            "theme_news_12h": theme_12h,
            "theme_news_24h": theme_24h,
            "duplicate_score": round(duplicate_score, 3),
            "theme_score": round(theme_score, 3),
            "repetition_pressure": round(repetition_pressure, 3),
            "theme": base_theme,
        }

    except Exception:
        return {
            "similar_news_6h": 0,
            "similar_news_12h": 0,
            "similar_news_24h": 0,
            "theme_news_6h": 0,
            "theme_news_12h": 0,
            "theme_news_24h": 0,
            "duplicate_score": 0.0,
            "theme_score": 0.0,
            "repetition_pressure": 0.0,
            "theme": "general",
        }


def compute_fatigue_score(
    similar_6h: int,
    similar_12h: int,
    similar_24h: int,
    theme_6h: int,
    theme_12h: int,
    theme_24h: int,
    repetition_pressure: float = 0.0,
) -> int:
    """
    Weighted fatigue score from 0 to 10 for news filter use.
    """
    score = 0

    score += min(similar_6h * 3, 5)
    score += min(similar_12h, 2)
    score += min(similar_24h, 1)
    score += min(theme_6h, 1)
    score += min(theme_12h, 1)

    if repetition_pressure >= 0.85:
        score += 2
    elif repetition_pressure >= 0.65:
        score += 1

    return min(score, 10)


def get_repetition_context(title: str, current_news_id: int | None = None) -> dict:
    counts = get_similar_news_counts(title, current_news_id=current_news_id)

    fatigue_score = compute_fatigue_score(
        counts["similar_news_6h"],
        counts["similar_news_12h"],
        counts["similar_news_24h"],
        counts["theme_news_6h"],
        counts["theme_news_12h"],
        counts["theme_news_24h"],
        counts["repetition_pressure"],
    )

    repetition_level = "low"
    if fatigue_score >= 7:
        repetition_level = "high"
    elif fatigue_score >= 4:
        repetition_level = "medium"

    return {
        "theme": counts["theme"],
        "similar_news_6h": counts["similar_news_6h"],
        "similar_news_12h": counts["similar_news_12h"],
        "similar_news_24h": counts["similar_news_24h"],
        "theme_news_6h": counts["theme_news_6h"],
        "theme_news_12h": counts["theme_news_12h"],
        "theme_news_24h": counts["theme_news_24h"],
        "duplicate_score": counts["duplicate_score"],
        "theme_score": counts["theme_score"],
        "repetition_pressure": counts["repetition_pressure"],
        "fatigue_score": fatigue_score,
        "repetition_level": repetition_level,
        "has_escalation_words": has_escalation_words(title),
    }


def adjust_fatigue_for_novelty(fatigue_score: int, title: str) -> int:
    """
    If repeated theme includes escalation / truly new catalyst words,
    reduce fatigue penalty a bit.
    """
    if has_escalation_words(title):
        return max(0, fatigue_score - 2)
    return fatigue_score


def get_novelty_label(title: str, current_news_id: int | None = None) -> str:
    """
    Strict novelty label for filter agent.
    - true_new_event
    - update_to_existing_theme
    - repetition_only
    """
    rep = get_repetition_context(title, current_news_id=current_news_id)

    if rep["similar_news_24h"] == 0 and rep["theme_news_24h"] == 0:
        return "true_new_event"

    if rep["repetition_pressure"] >= 0.85 and not rep["has_escalation_words"]:
        return "repetition_only"

    if rep["has_escalation_words"]:
        return "update_to_existing_theme"

    if rep["similar_news_12h"] >= 2:
        return "repetition_only"

    return "update_to_existing_theme"


# =========================================================
# SIMPLE IMPACT ADJUSTMENT HELPERS
# =========================================================

def get_fatigue_penalty(fatigue_score: int) -> int:
    if fatigue_score >= 8:
        return 4
    if fatigue_score >= 6:
        return 3
    if fatigue_score >= 4:
        return 2
    if fatigue_score >= 2:
        return 1
    return 0


def compute_remaining_tradable_impact(
    base_event_impact: int | float,
    published_at: datetime | None,
    title: str,
    current_news_id: int | None = None,
) -> dict:
    """
    Convenience helper for the second agent.

    Returns:
    {
      base_event_impact,
      fatigue_score,
      adjusted_fatigue_score,
      fatigue_penalty,
      time_decay_penalty,
      novelty_label,
      remaining_tradable_impact
    }
    """
    rep = get_repetition_context(title, current_news_id=current_news_id)
    novelty_label = get_novelty_label(title, current_news_id=current_news_id)

    raw_fatigue = rep["fatigue_score"]
    adjusted_fatigue = adjust_fatigue_for_novelty(raw_fatigue, title)

    fatigue_penalty = get_fatigue_penalty(adjusted_fatigue)
    time_decay_penalty = get_time_decay_penalty(published_at)

    remaining = float(base_event_impact) - fatigue_penalty - time_decay_penalty
    remaining = int(round(_clamp(remaining, 0, 10)))

    return {
        "base_event_impact": float(base_event_impact),
        "theme": rep["theme"],
        "repetition_level": rep["repetition_level"],
        "fatigue_score": raw_fatigue,
        "adjusted_fatigue_score": adjusted_fatigue,
        "fatigue_penalty": fatigue_penalty,
        "time_decay_penalty": time_decay_penalty,
        "novelty_label": novelty_label,
        "remaining_tradable_impact": remaining,
    }


IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")


def get_market_status(now_ist: datetime | None = None) -> dict:
    """
    Return market status using IST as the reference timezone.

    Status meanings:
    - crypto: always "open"
    - forex: "open" or "closed"
    - us_equities: "pre_market" | "regular" | "after_hours" | "closed"
    - futures: "open" | "maintenance_break" | "closed"

    Notes:
    - US equities:
        pre-market   1:30 PM–7:00 PM IST (roughly, DST-adjusted via ET conversion)
        regular       7:00 PM–1:30 AM IST
        after-hours   1:30 AM–5:30 AM IST
      These come from standard ET sessions converted dynamically.
    - Forex:
        Open from Sunday 5:00 PM ET to Friday 5:00 PM ET.
    - CME-style futures:
        Open from Sunday 6:00 PM ET to Friday 5:00 PM ET,
        with daily maintenance break 5:00 PM–6:00 PM ET.
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    now_et = now_ist.astimezone(ET)
    et_weekday = now_et.weekday()  # Mon=0 ... Sun=6
    et_t = now_et.time()

    status = {
        "timestamp_ist": now_ist.isoformat(),
        "timestamp_et": now_et.isoformat(),
        "crypto": "open",
        "forex": _get_forex_status(et_weekday, et_t),
        "us_equities": _get_us_equities_status(et_weekday, et_t),
        "futures": _get_futures_status(et_weekday, et_t),
    }

    return status


def _get_us_equities_status(weekday: int, et_t: time) -> str:
    # Closed on Saturday/Sunday
    if weekday >= 5:
        return "closed"

    pre_market_start = time(4, 0)
    regular_start = time(9, 30)
    regular_end = time(16, 0)
    after_hours_end = time(20, 0)

    if pre_market_start <= et_t < regular_start:
        return "pre_market"
    if regular_start <= et_t < regular_end:
        return "regular"
    if regular_end <= et_t < after_hours_end:
        return "after_hours"
    return "closed"


def _get_forex_status(weekday: int, et_t: time) -> str:
    # Forex: Sunday 5:00 PM ET -> Friday 5:00 PM ET
    # Sat closed, most of Sun closed until 5 PM ET
    if weekday == 5:  # Saturday
        return "closed"

    if weekday == 6:  # Sunday
        return "open" if et_t >= time(17, 0) else "closed"

    if weekday == 4:  # Friday
        return "open" if et_t < time(17, 0) else "closed"

    return "open"  # Mon-Thu


def _get_futures_status(weekday: int, et_t: time) -> str:
    # CME-style futures:
    # Open Sunday 6:00 PM ET -> Friday 5:00 PM ET
    # Daily maintenance break: 5:00 PM–6:00 PM ET
    if weekday == 5:  # Saturday
        return "closed"

    if weekday == 6:  # Sunday
        return "open" if et_t >= time(18, 0) else "closed"

    if weekday == 4:  # Friday
        if et_t < time(17, 0):
            return "open"
        return "closed"

    # Mon-Thu
    if time(17, 0) <= et_t < time(18, 0):
        return "maintenance_break"
    return "open"


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "to", "of", "for", "on", "in",
    "at", "by", "from", "with", "as", "into", "after", "before", "over", "under",
    "amid", "near", "around", "says", "said", "say", "sees", "warns", "expects",
    "watch", "watching", "traders", "markets", "market", "investors", "update"
}


def _title_tokens(title: str) -> set[str]:
    norm = _normalize_event_title(title)
    return {t for t in norm.split() if len(t) > 2 and t not in STOPWORDS and t != "<num>"}


def _token_overlap_score(a: str, b: str) -> float:
    ta = _title_tokens(a)
    tb = _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _headline_similarity_score(a: str, b: str) -> float:
    a_norm = _normalize_event_title(a)
    b_norm = _normalize_event_title(b)
    seq = SequenceMatcher(None, a_norm, b_norm).ratio()
    tok = _token_overlap_score(a_norm, b_norm)
    return max(seq, tok)


def get_filter_context(title: str, current_news_id: int | None = None) -> dict:
    reaction = detect_reaction_headline(title)
    repetition = get_repetition_context(title, current_news_id=current_news_id)
    recent = search_recent_news(title, current_news_id=current_news_id, hours_back=48)

    novelty_label = get_novelty_label(title, current_news_id=current_news_id)

    return {
        "theme": repetition["theme"],
        "novelty_label": novelty_label,
        "has_escalation_words": repetition["has_escalation_words"],
        "reaction_headline": reaction["reaction_headline"],
        "headline_move_pct": reaction["headline_move_pct"],
        "has_new_catalyst_words": reaction["has_new_catalyst"],
        "similar_news_6h": repetition["similar_news_6h"],
        "similar_news_12h": repetition["similar_news_12h"],
        "similar_news_24h": repetition["similar_news_24h"],
        "theme_news_6h": repetition["theme_news_6h"],
        "theme_news_12h": repetition["theme_news_12h"],
        "theme_news_24h": repetition["theme_news_24h"],
        "duplicate_score": repetition["duplicate_score"],
        "theme_score": repetition["theme_score"],
        "repetition_pressure": repetition["repetition_pressure"],
        "fatigue_score": repetition["fatigue_score"],
        "repetition_level": repetition["repetition_level"],
        "priced_in": recent["priced_in"],
        "match_score": recent["match_score"],
        "matched_title": recent.get("matched_title"),
        "matched_hours_ago": recent.get("matched_hours_ago"),
    }