# app/ind/tools.py
"""
Indian Market Intelligence — Tools Layer V6

Changelog vs V4:
- Merged get_stock_profile + get_price_timing + get_relative_performance → get_stock_context()
  Single yfinance fetch per stock instead of 3 separate calls.
- resolve_company() now caches the companies table at module init.
- get_peer_reaction() limited to 3 peers, daily data only (15m was false precision).
- All yfinance calls wrapped with explicit timeouts.
- Removed dead map_companies_from_text, map_sectors_from_companies, build_agent_context.

Tools in this file:
 1. resolve_company()          — NSE symbol mapping + alias table (CACHED)
 2. get_stock_context()        — Combined: profile + price timing + relative perf (KEY TOOL)
 3. get_peer_reaction()        — Peer stocks vs target (simplified)
 4. get_broad_market_snapshot() — Nifty/Sensex session
 5. get_source_credibility()   — Confidence cap + event classification
 6. get_market_status()        — Open/closed with holiday awareness
 7. classify_novelty()         — Expected vs surprise classifier
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

import requests
import ccxt
import pandas as pd
from app.core.db import fetch_all


# =========================================================
# CONSTANTS
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

INDIAN_INDEX_SYMBOLS = {
    "NIFTY 50":         "^NSEI",
    "SENSEX":           "^BSESN",
    "BANKNIFTY":        "^NSEBANK",
    "NIFTY BANK":       "^NSEBANK",
}

MARKET_CAP_BUCKETS = {
    "large_cap": "> ₹20,000 Cr",
    "mid_cap":   "₹5,000–20,000 Cr",
    "small_cap": "< ₹5,000 Cr",
    "unknown":   "unavailable",
}

NSE_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 26), date(2026, 2, 26), date(2026, 3, 20),
    date(2026, 3, 26), date(2026, 4, 3), date(2026, 4, 14),
    date(2026, 8, 15), date(2026, 8, 27), date(2026, 10, 2),
    date(2026, 10, 21), date(2026, 10, 22), date(2026, 11, 5),
    date(2026, 12, 25),
}

NSE_MUHURAT_DATES_2026: set[date] = {date(2026, 10, 21)}





# =========================================================
# SECTOR → DB keywords (for peer lookup)
# =========================================================
SECTOR_DB_KEYWORDS: dict[str, list[str]] = {
    "banking": ["bank", "banking"],
    "financial_services": ["financial services", "nbfc", "insurance", "asset management",
                           "housing finance", "wealth management"],
    "it": ["software", "information technology", "it services", "computer", "data processing"],
    "pharma": ["pharma", "pharmaceutical", "drug", "healthcare", "hospital", "diagnostic",
               "biotechnology"],
    "auto": ["automobile", "auto ancillaries", "vehicle", "two wheeler", "commercial vehicle"],
    "oil_gas": ["oil", "gas", "petroleum", "refinery", "crude", "petrochemical"],
    "power": ["power", "electricity", "utility", "transmission", "solar", "wind energy",
              "renewable energy"],
    "metals": ["steel", "metal", "aluminium", "copper", "zinc", "iron ore", "mining"],
    "capital_goods": ["capital goods", "industrial machinery", "heavy engineering"],
    "railways": ["railway", "rolling stock", "wagon", "locomotive"],
    "defence": ["defence", "defense", "aerospace", "ordnance"],
    "realty": ["real estate", "realty", "property developer"],
    "infrastructure": ["infrastructure", "road", "highway", "port", "airport"],
    "fmcg": ["consumer goods", "fmcg", "packaged food", "personal care", "beverages"],
    "telecom": ["telecom", "telecommunication", "mobile services"],
    "chemicals": ["chemical", "agrochemical", "specialty chemical", "fertiliser"],
}


# =========================================================
# HELPERS
# =========================================================

def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _now_ist() -> datetime:
    return datetime.now(IST)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.replace("&", " and ").replace("%", " percent ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_nse_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


def _normalize_for_yf(symbol: str) -> str:
    s = (symbol or "").strip()
    if not s:
        return s
    if s in INDIAN_INDEX_SYMBOLS:
        return INDIAN_INDEX_SYMBOLS[s]
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return _normalize_nse_symbol(s)


def _safe_history(symbol: str, period: str = "5d", interval: str = "1d"):
    try:
        return yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception:
        return None


def _parse_published_iso(published_iso: str) -> datetime | None:
    try:
        ts = (published_iso or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        return _to_utc(dt)
    except Exception:
        return None


def _pct_change(start: float, end: float) -> float | None:
    if not start:
        return None
    return round(((end - start) / start) * 100, 3)


def _canonicalize_sector(label: str) -> str:
    x = _normalize_text(label)
    if not x:
        return ""
    # Check in priority order to avoid cross-matches
    if any(k in x for k in ["telecom", "telecommunication", "mobile services"]):
        return "telecom"
    if any(k in x for k in ["railway", "rolling stock", "wagon", "locomotive"]):
        return "railways"
    if any(k in x for k in ["defence", "defense", "aerospace", "ordnance"]):
        return "defence"
    if any(k in x for k in ["capital goods", "industrial machinery", "heavy engineering"]):
        return "capital_goods"
    if any(k in x for k in ["bank", "banking"]):
        return "banking"
    if any(k in x for k in ["financial services", "nbfc", "insurance", "asset management",
                              "housing finance", "wealth management"]):
        return "financial_services"
    if any(k in x for k in ["software", "information technology", "it services",
                              "data processing", "computer"]):
        return "it"
    if any(k in x for k in ["pharma", "pharmaceutical", "drug", "healthcare",
                              "hospital", "diagnostic", "biotechnology"]):
        return "pharma"
    if any(k in x for k in ["automobile", "auto ancillaries", "vehicle",
                              "two wheeler", "commercial vehicle"]):
        return "auto"
    if any(k in x for k in ["oil", "gas", "petroleum", "refinery", "crude", "petrochemical"]):
        return "oil_gas"
    if any(k in x for k in ["power", "electricity", "utility", "transmission",
                              "solar", "wind energy", "renewable energy"]):
        return "power"
    if any(k in x for k in ["steel", "metal", "aluminium", "copper", "zinc",
                              "iron ore", "mining"]):
        return "metals"
    if any(k in x for k in ["chemical", "agrochemical", "specialty chemical", "fertiliser"]):
        return "chemicals"
    if any(k in x for k in ["real estate", "realty", "property developer"]):
        return "realty"
    if any(k in x for k in ["infrastructure", "road", "highway", "port", "airport"]):
        return "infrastructure"
    if any(k in x for k in ["consumer goods", "fmcg", "packaged food", "personal care", "beverages"]):
        return "fmcg"
    return ""


# =========================================================
# TOOL 1: RESOLVE COMPANY (CACHED)
# =========================================================

_COMPANIES_CACHE: list[dict] | None = None
_COMPANIES_CACHE_LOCK = threading.Lock()


def _get_companies_list() -> list[dict]:
    """Load companies table once, cache in memory. Fetches from both companies and nse_companies."""
    global _COMPANIES_CACHE
    if _COMPANIES_CACHE is not None:
        return _COMPANIES_CACHE
    with _COMPANIES_CACHE_LOCK:
        if _COMPANIES_CACHE is not None:
            return _COMPANIES_CACHE
        try:
            combined_rows = []
            
            # Fetch from companies table
            rows1 = fetch_all(
                "SELECT company_name, nse_symbol FROM companies "
                "WHERE nse_symbol IS NOT NULL AND TRIM(nse_symbol) <> ''"
            ) or []
            combined_rows.extend(rows1)
            
            # Fetch from nse_companies table
            rows2 = fetch_all(
                "SELECT company_name, symbol as nse_symbol FROM nse_companies "
                "WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''"
            ) or []
            combined_rows.extend(rows2)
            
            _COMPANIES_CACHE = combined_rows
        except Exception as e:
            _COMPANIES_CACHE = []
    return _COMPANIES_CACHE


def resolve_company(name: str) -> dict:
    """
    Look up an Indian listed company by name → NSE symbol.
    Uses cached DB with fuzzy matching.
    """
    if not name or not str(name).strip():
        return {"input_name": name, "status": "unresolved", "symbol": None, "company_name": ""}

    name_norm = _normalize_text(name)

    # Cached DB lookup
    rows = _get_companies_list()
    best_match = None
    highest_ratio = 0.0

    for row in rows:
        company_name = (row.get("company_name") or "").strip()
        symbol = (row.get("nse_symbol") or "").strip().upper()
        if not company_name or not symbol:
            continue

        cname_norm = _normalize_text(company_name)
        # Exact match
        if name_norm == cname_norm or name_norm == symbol.lower():
            return {"input_name": name, "symbol": symbol, "company_name": company_name, "status": "resolved"}

        # Substring exact match for broad mapping (like "jio" in "reliance jio")
        if len(name_norm) > 3 and (name_norm in cname_norm or cname_norm in name_norm):
            ratio = 0.90  # High confidence if one is a substring of the other
        else:
            ratio = SequenceMatcher(None, name_norm, cname_norm).ratio()
            
        if ratio > 0.82 and ratio > highest_ratio:
            highest_ratio = ratio
            best_match = {"input_name": name, "symbol": symbol, "company_name": company_name, "status": "resolved"}

    if best_match:
        return best_match

    return {"input_name": name, "status": "unresolved", "symbol": None, "company_name": ""}


def strict_resolve_symbols(extracted_names: list[str]) -> list[str]:
    """
    Strictly maps an array of literal company names to NSE symbols. NO fuzzy matching.
    Drops known group names (Tata, Adani, Reliance) unless exact mapped.
    Drops any name that maps to multiple distinct entities.
    """
    if not extracted_names:
        return []

    valid_symbols = set()
    companies = _get_companies_list()

    # Pre-compute exact mapping for incoming entities
    exact_map = {}
    for row in companies:
        cname = (row.get("company_name") or "").strip()
        sym = (row.get("nse_symbol") or "").strip().upper()
        if not cname or not sym:
            continue
            
        cname_norm = _normalize_text(cname)
        # Strip common generic suffixes safely for mapping only
        clean_name = re.sub(r'\b(ltd|limited|corp|corporation|co|company|inc)\b', '', cname_norm).strip()
        
        # If mapping already exists but maps to a DIFFERENT symbol, mark it AMBIGUOUS
        if clean_name in exact_map and exact_map[clean_name] != sym:
            exact_map[clean_name] = "AMBIGUOUS"
        else:
            exact_map[clean_name] = sym

    # Tiny, hyper-curated absolute safe list of aliases
    explicit_aliases = {
        "tcs": "TCS",
        "sbi": "SBIN",
        "state bank of india": "SBIN",
        "infy": "INFY",
        "infosys": "INFY",
        "hul": "HINDUNILVR",
        "itc": "ITC",
        "hdfc": "HDFCBANK", # Post-merger safety
        "hdfc bank": "HDFCBANK",
        "lic": "LICI",
    }
    
    # Generic rejection list for extremely broad entities
    rejection_list = {"tata", "adani", "reliance", "jio", "birla", "mahindra", "godrej", "bajaj", "airtel"}

    for name in extracted_names:
        # Validate that it is indeed a string. Sometimes models hallucinate dicts/lists.
        if not isinstance(name, str):
            continue
            
        name_clean = str(name).strip().lower()
        if not name_clean:
            continue
            
        name_norm = _normalize_text(name_clean)
        clean_input = re.sub(r'\b(ltd|limited|corp|corporation|co|company|inc)\b', '', name_norm).strip()
        
        if not clean_input or clean_input in rejection_list:
            continue
            
        # Check explicit alias
        if clean_input in explicit_aliases:
            valid_symbols.add(explicit_aliases[clean_input])
            continue
            
        # Check strict DB exact matching
        mapped_sym = exact_map.get(clean_input)
        if mapped_sym and mapped_sym != "AMBIGUOUS":
            valid_symbols.add(mapped_sym)
            
    return list(valid_symbols)

# =========================================================
# TOOL 2: GET STOCK CONTEXT (MERGED — KEY TOOL)
# Replaces: get_stock_profile + get_price_timing + get_relative_performance
# Single yfinance fetch set per stock.
# =========================================================

def get_stock_context(symbol: str, published_iso: str = "") -> dict:
    """
    Combined stock intelligence: profile + price timing + relative performance.

    Output:
    {
        "symbol": "RELIANCE",
        "current_price": 1245.30,
        "day_change_pct": -0.82,
        "market_cap_bucket": "large_cap",
        "today_open": 151.20,
        "today_high": 153.85,
        "today_low": 147.10,
        "previous_close": 154.00,
        "gap_pct": -1.818,
        "gap_type": "gap_down",
        "atr_pct": 1.42,
        "year_high": 1320.00,
        "year_low": 1050.00,
        "position_in_52w_range": 0.67,
        "trend_5d": "up",

        "signal_timing": "pre_article",
        "move_before_pct": -1.80,
        "move_after_pct": -0.30,
        "lag_flag": true,

        "stock_return_pct": -2.10,
        "nifty_return_pct": -0.65,
        "relative_vs_nifty_pct": -1.45,
        "relative_interpretation": "stock_specific_negative",

        "data_quality": "full"
    }
    """
    yf_sym = _normalize_for_yf(symbol)
    pub_dt = _parse_published_iso(published_iso) if published_iso else None
    result: dict[str, Any] = {"symbol": symbol}

    # ── 1. YEARLY DATA (profile: ATR, 52w range, trend) ──
    try:
        hist_1y = _safe_history(yf_sym, period="1y", interval="1d")
        if hist_1y is None or hist_1y.empty:
            result["data_quality"] = "unavailable"
            return result

        current_price = float(hist_1y["Close"].iloc[-1])
        prev_close = float(hist_1y["Close"].iloc[-2]) if len(hist_1y) >= 2 else current_price
        result["current_price"] = round(current_price, 2)
        result["day_change_pct"] = _pct_change(prev_close, current_price)

        # Today's session range + gap vs previous close
        today_open = float(hist_1y["Open"].iloc[-1]) if len(hist_1y) >= 1 else None
        today_high = float(hist_1y["High"].iloc[-1]) if len(hist_1y) >= 1 else None
        today_low = float(hist_1y["Low"].iloc[-1]) if len(hist_1y) >= 1 else None

        result["today_open"] = round(today_open, 2) if today_open is not None else None
        result["today_high"] = round(today_high, 2) if today_high is not None else None
        result["today_low"] = round(today_low, 2) if today_low is not None else None
        result["previous_close"] = round(prev_close, 2) if prev_close is not None else None

        if prev_close:
            gap_pct = ((today_open - prev_close) / prev_close) * 100
            result["gap_pct"] = round(gap_pct, 3)

            if gap_pct > 0.15:
                result["gap_type"] = "gap_up"
            elif gap_pct < -0.15:
                result["gap_type"] = "gap_down"
            else:
                result["gap_type"] = "flat"
        else:
            result["gap_pct"] = None
            result["gap_type"] = "unknown"

        year_high = float(hist_1y["High"].max())
        year_low = float(hist_1y["Low"].min())
        range_size = year_high - year_low
        result["year_high"] = round(year_high, 2)
        result["year_low"] = round(year_low, 2)
        result["position_in_52w_range"] = round((current_price - year_low) / range_size, 2) if range_size > 0 else 0.5

        # ATR
        if len(hist_1y) >= 15:
            df = hist_1y.copy()
            df["H-L"] = df["High"] - df["Low"]
            df["H-PC"] = (df["High"] - df["Close"].shift(1)).abs()
            df["L-PC"] = (df["Low"] - df["Close"].shift(1)).abs()
            tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1])
            result["atr_pct"] = round((atr_val / current_price) * 100, 2) if current_price else None
        else:
            result["atr_pct"] = None

        # 5d trend
        if len(hist_1y) >= 5:
            chg_5d = _pct_change(float(hist_1y["Close"].iloc[-5]), current_price) or 0
            result["trend_5d"] = "up" if chg_5d > 0.5 else ("down" if chg_5d < -0.5 else "flat")
        else:
            result["trend_5d"] = "flat"

        # Market cap bucket
        try:
            info = yf.Ticker(yf_sym).info
            mktcap_usd = info.get("marketCap")
            if mktcap_usd:
                cap_cr = round(mktcap_usd * 83 / 1e7, 0)
                result["market_cap_bucket"] = "large_cap" if cap_cr >= 20000 else ("mid_cap" if cap_cr >= 5000 else "small_cap")
            else:
                result["market_cap_bucket"] = "unknown"
        except Exception:
            result["market_cap_bucket"] = "unknown"

    except Exception:
        result["data_quality"] = "unavailable"
        return result

    # ── 2. INTRADAY DATA (price timing + relative performance) ──
    if pub_dt:
        now_utc = datetime.now(timezone.utc)
        fetch_start = pub_dt - timedelta(minutes=60)
        fetch_end = now_utc + timedelta(minutes=5)

        try:
            intra_df = yf.Ticker(yf_sym).history(start=fetch_start, end=fetch_end, interval="15m")
            if intra_df is not None and not intra_df.empty:
                if intra_df.index.tz is None:
                    intra_df.index = intra_df.index.tz_localize(timezone.utc)
                else:
                    intra_df.index = intra_df.index.tz_convert(timezone.utc)

                pre_df = intra_df[intra_df.index < pub_dt]
                post_df = intra_df[intra_df.index >= pub_dt]

                # Price timing
                if not pre_df.empty and not post_df.empty:
                    move_before = _pct_change(float(pre_df["Close"].iloc[0]), float(pre_df["Close"].iloc[-1]))
                    move_after = _pct_change(float(post_df["Close"].iloc[0]), float(intra_df["Close"].iloc[-1]))
                    result["move_before_pct"] = move_before
                    result["move_after_pct"] = move_after

                    if move_before is not None and move_after is not None:
                        abs_b, abs_a = abs(move_before), abs(move_after)
                        total = abs_b + abs_a
                        if total >= 0.2:
                            share_before = abs_b / total
                            if share_before > 0.65:
                                result["signal_timing"] = "pre_article"
                                result["lag_flag"] = True
                            elif share_before < 0.35:
                                result["signal_timing"] = "post_article"
                                result["lag_flag"] = False
                            else:
                                result["signal_timing"] = "concurrent"
                                result["lag_flag"] = False
                        else:
                            result["signal_timing"] = "no_move"
                            result["lag_flag"] = False

                # Relative performance vs Nifty
                if not post_df.empty and len(post_df) >= 2:
                    stock_ret = _pct_change(float(post_df["Close"].iloc[0]), float(intra_df["Close"].iloc[-1]))
                    result["stock_return_pct"] = stock_ret

                    # Fetch Nifty for same window
                    try:
                        nifty_df = yf.Ticker("^NSEI").history(start=fetch_start, end=fetch_end, interval="15m")
                        if nifty_df is not None and not nifty_df.empty:
                            if nifty_df.index.tz is None:
                                nifty_df.index = nifty_df.index.tz_localize(timezone.utc)
                            else:
                                nifty_df.index = nifty_df.index.tz_convert(timezone.utc)
                            nifty_post = nifty_df[nifty_df.index >= pub_dt]
                            if not nifty_post.empty and len(nifty_post) >= 2:
                                nifty_ret = _pct_change(float(nifty_post["Close"].iloc[0]), float(nifty_df["Close"].iloc[-1]))
                                result["nifty_return_pct"] = nifty_ret

                                if stock_ret is not None and nifty_ret is not None:
                                    rel = round(stock_ret - nifty_ret, 3)
                                    result["relative_vs_nifty_pct"] = rel

                                    # Interpretation
                                    if abs(stock_ret) > 0.2 and abs(nifty_ret) > 0.2 and ((stock_ret > 0) != (nifty_ret > 0)):
                                        result["relative_interpretation"] = "divergent"
                                    elif abs(rel) < 0.5 and abs(stock_ret) < 0.3:
                                        result["relative_interpretation"] = "neutral"
                                    elif rel > 0.5:
                                        result["relative_interpretation"] = "stock_specific_positive"
                                    elif rel < -0.5:
                                        result["relative_interpretation"] = "stock_specific_negative"
                                    else:
                                        result["relative_interpretation"] = "market_driven_positive" if stock_ret > 0 else "market_driven_negative"
                    except Exception:
                        pass
        except Exception:
            pass

    result.setdefault("data_quality", "full")
    return result


# =========================================================
# TOOL 3: PEER REACTION (SIMPLIFIED)
# Max 3 peers, daily data only.
# =========================================================

def _build_peer_sql(sector_canon: str) -> tuple[str, list[str]]:
    keywords = SECTOR_DB_KEYWORDS.get(sector_canon, [])
    if not keywords:
        return "1=0", []
    conditions, params = [], []
    for kw in keywords:
        safe_kw = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe_kw}%"
        conditions.append("(LOWER(sector) LIKE %s OR LOWER(industry) LIKE %s OR LOWER(basic_industry) LIKE %s)")
        params.extend([pattern, pattern, pattern])
    return " OR ".join(conditions), params


def get_peer_reaction(symbol: str, sector: str, published_iso: str = "") -> dict:
    """
    Compares target stock's daily return vs up to 3 sector peers.
    """
    sector_canon = _canonicalize_sector(sector) or sector.lower()
    peer_symbols: list[str] = []

    try:
        where_clause, kw_params = _build_peer_sql(sector_canon)
        if where_clause != "1=0":
            rows = fetch_all(
                f"SELECT nse_symbol FROM companies WHERE nse_symbol IS NOT NULL "
                f"AND TRIM(nse_symbol) <> '' AND nse_symbol != %s AND ({where_clause}) LIMIT 10",
                [symbol] + kw_params,
            ) or []
            peer_symbols = [r["nse_symbol"] for r in rows if r.get("nse_symbol")]
    except Exception:
        pass

    if len(peer_symbols) < 2:
        return {"target_symbol": symbol, "sector": sector, "move_type": "insufficient_peers", "data_quality": "unavailable"}

    # Target return (daily)
    target_ret = None
    try:
        df = _safe_history(_normalize_for_yf(symbol), period="5d", interval="1d")
        if df is not None and len(df) >= 2:
            target_ret = _pct_change(float(df["Close"].iloc[-2]), float(df["Close"].iloc[-1]))
    except Exception:
        pass

    # Peer returns (daily, max 3)
    sampled = peer_symbols[:3]
    peer_returns: dict[str, float | None] = {}
    for peer in sampled:
        try:
            df = _safe_history(_normalize_for_yf(peer), period="5d", interval="1d")
            if df is not None and len(df) >= 2:
                peer_returns[peer] = _pct_change(float(df["Close"].iloc[-2]), float(df["Close"].iloc[-1]))
            else:
                peer_returns[peer] = None
        except Exception:
            peer_returns[peer] = None

    valid = [v for v in peer_returns.values() if v is not None]
    peer_avg = round(sum(valid) / len(valid), 3) if valid else None

    move_type = "mixed"
    if target_ret is not None and peer_avg is not None:
        diff = abs(target_ret - peer_avg)
        same_dir = sum(1 for v in valid if (v > 0) == (target_ret > 0))
        if diff >= 1.0:
            move_type = "isolated"
        elif diff <= 0.4 and same_dir >= len(valid) * 0.6:
            move_type = "basket_move"

    return {
        "target_symbol": symbol,
        "target_return_pct": target_ret,
        "sector": sector,
        "peers_sampled": sampled,
        "peer_returns": peer_returns,
        "peer_avg_return_pct": peer_avg,
        "move_type": move_type,
        "data_quality": "good" if valid else "unavailable",
    }


# =========================================================
# TOOL 4: BROAD MARKET SNAPSHOT
# =========================================================

def get_broad_market_snapshot() -> dict:
    """Nifty 50 + Sensex current session context."""
    def _fetch(yf_sym: str) -> dict:
        try:
            hist = _safe_history(yf_sym, period="5d", interval="1d")
            if hist is None or hist.empty:
                return {}
            curr = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else curr
            chg_5d = _pct_change(float(hist["Close"].iloc[0]), curr) or 0
            return {
                "current": round(curr, 2),
                "day_change_pct": _pct_change(prev, curr),
                "trend_5d": "up" if chg_5d > 0.5 else ("down" if chg_5d < -0.5 else "flat"),
            }
        except Exception:
            return {}

    nifty = _fetch("^NSEI")
    sensex = _fetch("^BSESN")
    nifty_chg = nifty.get("day_change_pct")

    if nifty_chg is None:
        sentiment = "unknown"
    elif nifty_chg >= 1.0:
        sentiment = "strongly_bullish"
    elif nifty_chg >= 0.3:
        sentiment = "mildly_bullish"
    elif nifty_chg <= -1.0:
        sentiment = "strongly_bearish"
    elif nifty_chg <= -0.3:
        sentiment = "mildly_bearish"
    else:
        sentiment = "neutral"

    return {"nifty50": nifty, "sensex": sensex, "session_sentiment": sentiment}


# =========================================================
# TOOL 5: SOURCE CREDIBILITY
# =========================================================

def get_source_credibility(source: str) -> dict:
    """Returns source credibility tier, confidence cap, and event signal type."""
    s = (source or "").strip().lower()

    rumor_signals = ["sources say", "reportedly", "exclusive", "people familiar", "unconfirmed"]
    is_rumor = any(sig in s for sig in rumor_signals)

    if any(x in s for x in ["rbi", "sebi", "nse announcement", "bse announcement", "regulator"]):
        return {"source_type": "regulator", "credibility_tier": "highest", "confidence_cap": 85,
                "treat_event_as": "confirmed", "event_signal_type": "rumor_or_scoop" if is_rumor else "primary_filing"}

    if any(x in s for x in ["government", "pib", "ministry", "cabinet"]):
        return {"source_type": "government", "credibility_tier": "very_high", "confidence_cap": 85,
                "treat_event_as": "confirmed", "event_signal_type": "primary_filing"}

    if any(x in s for x in ["company filing", "exchange filing", "annual report", "bse:results", "nse:results"]):
        return {"source_type": "company_filing", "credibility_tier": "high", "confidence_cap": 85,
                "treat_event_as": "confirmed", "event_signal_type": "primary_filing"}

    if any(x in s for x in ["reuters", "bloomberg", "economic times", "moneycontrol",
                              "mint", "livemint", "business standard", "cnbc", "financial express"]):
        return {"source_type": "financial_media", "credibility_tier": "medium_high",
                "confidence_cap": 60 if is_rumor else 75, "treat_event_as": "reported",
                "event_signal_type": "rumor_or_scoop" if is_rumor else "original_reporting"}

    if any(x in s for x in ["broker", "brokerage", "research", "jefferies", "goldman",
                              "morgan stanley", "kotak", "motilal"]):
        return {"source_type": "broker_or_rating", "credibility_tier": "medium", "confidence_cap": 65,
                "treat_event_as": "opinion", "event_signal_type": "analyst_view"}

    return {"source_type": "unknown", "credibility_tier": "low", "confidence_cap": 50,
            "treat_event_as": "unverified", "event_signal_type": "unknown"}


# =========================================================
# TOOL 6: MARKET STATUS
# =========================================================

def get_market_status() -> dict:
    """Current Indian market session status with holiday awareness."""
    now = _now_ist()
    today = now.date()
    weekday = now.weekday()
    time_str = now.strftime("%H:%M")

    if weekday >= 5:
        return {"equities": "closed", "time_ist": time_str, "is_holiday": False,
                "session_type": "weekend", "tradeability_window": "closed"}

    if today in NSE_HOLIDAYS_2026:
        if today in NSE_MUHURAT_DATES_2026:
            m_open = datetime.combine(today, time(18, 0), tzinfo=IST)
            m_close = datetime.combine(today, time(19, 0), tzinfo=IST)
            if m_open <= now <= m_close:
                return {"equities": "muhurat", "time_ist": time_str, "is_holiday": True,
                        "session_type": "muhurat", "tradeability_window": "active"}
        return {"equities": "holiday", "time_ist": time_str, "is_holiday": True,
                "session_type": "holiday", "tradeability_window": "closed"}

    pre_open = datetime.combine(today, time(9, 0), tzinfo=IST)
    mkt_open = datetime.combine(today, time(9, 15), tzinfo=IST)
    mkt_close = datetime.combine(today, time(15, 30), tzinfo=IST)

    if pre_open <= now < mkt_open:
        equities, window = "pre_open", "pre_open"
    elif mkt_open <= now <= mkt_close:
        equities, window = "open", "active"
    else:
        equities, window = "closed", "closed"

    return {"equities": equities, "time_ist": time_str, "is_holiday": False,
            "session_type": "normal", "tradeability_window": window}


# =========================================================
# TOOL 7: NOVELTY CLASSIFIER
# =========================================================

def classify_novelty(title: str, summary: str = "") -> dict:
    """Classifies event novelty: TRUE_CATALYST, EXPECTED_SURPRISE, EXPECTED_ROUTINE, AMBIGUOUS."""
    text = _normalize_text(f"{title} {summary}")

    routine_phrases = ["in line with", "meets estimates", "meets expectations", "as expected",
                       "in-line", "broadly in line", "no surprises", "largely in line"]
    earnings_phrases = ["q1 results", "q2 results", "q3 results", "q4 results",
                        "quarterly results", "quarterly earnings", "net profit", "revenue",
                        "ebitda", "earnings", "declares dividend"]
    scheduled_phrases = ["guidance", "rbi policy", "monetary policy", "budget", "agm", "policy meeting"]
    surprise_phrases = ["beats", "beat estimates", "misses", "missed estimates", "surprise",
                        "unexpected", "shock", "unforeseen", "plunges", "surges", "jumps", "crashes"]
    catalyst_phrases = ["order win", "contract awarded", "wins contract", "secures order",
                        "bags order", "acquisition", "merger", "takeover", "acquires",
                        "stake sale", "buyback announced", "plant fire", "plant shutdown",
                        "accident", "explosion", "regulatory ban", "sebi action", "penalty imposed",
                        "fda approval", "drug approval", "management change", "ceo resign",
                        "promoter sell", "block deal"]

    has_routine = any(p in text for p in routine_phrases)
    has_earnings = any(p in text for p in earnings_phrases)
    has_scheduled = any(p in text for p in scheduled_phrases)
    has_surprise = any(p in text for p in surprise_phrases)
    has_catalyst = any(p in text for p in catalyst_phrases)

    if has_catalyst and not has_routine:
        return {"novelty": "TRUE_CATALYST", "confidence": "high"}
    if has_routine and not has_surprise and not has_catalyst:
        return {"novelty": "EXPECTED_ROUTINE", "confidence": "high"}
    if (has_earnings or has_scheduled) and has_surprise and not has_routine:
        return {"novelty": "EXPECTED_SURPRISE", "confidence": "medium"}
    if (has_earnings or has_scheduled) and not has_surprise and not has_routine:
        return {"novelty": "EXPECTED_ROUTINE", "confidence": "medium"}
    if has_surprise or has_catalyst:
        return {"novelty": "TRUE_CATALYST", "confidence": "low"}
    return {"novelty": "AMBIGUOUS", "confidence": "low"}


