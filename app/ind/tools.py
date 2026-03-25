# tools.py
"""
Indian Market Intelligence — Tools Layer

Focus:
- Indian indices / sectors / stocks
- ATR + reaction logic
- Duplicate / priced-in / fatigue scoring
- Company / sector / theme mapping
- Indian market session awareness
- RBI / SEBI / government / FII-DII context helpers

Design goals:
- Conservative
- Deterministic where possible
- Avoid hallucinated mappings
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import re
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import yfinance as yf

from app.core.db import fetch_all, fetch_one


DEFAULT_TIMEOUT = 10
IST = ZoneInfo("Asia/Kolkata")


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


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _now_ist() -> datetime:
    return datetime.now(IST)


def _safe_history(symbol: str, period: str = "5d", interval: str = "1d"):
    try:
        t = yf.Ticker(symbol)
        return t.history(period=period, interval=interval)
    except Exception:
        return None


def _normalize_nse_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if s.endswith(".NS") or s.endswith(".BO") or s.startswith("^"):
        return s
    return f"{s}.NS"


def _normalize_symbol_for_market_data(symbol: str) -> str:
    s = (symbol or "").strip()
    if not s:
        return s

    # Known Indian index display names
    if s in INDIAN_INDEX_SYMBOLS:
        return INDIAN_INDEX_SYMBOLS[s]

    # Already a Yahoo index or exchange-formatted symbol
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s

    return _normalize_nse_symbol(s)


def get_indian_stock_price(symbol: str) -> dict:
    s = (symbol or "").strip()
    if not s:
        return _build_price_block("", "")

    # Known Indian index display names
    if s in INDIAN_INDEX_SYMBOLS:
        yf_symbol = INDIAN_INDEX_SYMBOLS[s]
        return _build_price_block(yf_symbol, s)

    # Already a Yahoo index symbol
    if s.startswith("^"):
        return _build_price_block(s, s)

    # Normal NSE stock
    yf_symbol = _normalize_nse_symbol(s)
    return _build_price_block(yf_symbol, s)

def _normalize_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.replace("&", " and ")
    t = t.replace("%", " percent ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _contains_phrase(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    return f" {phrase} " in f" {text} "


# =========================================================
# INDIAN MARKET SYMBOL MAPS
# =========================================================

INDIAN_INDEX_SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100.NS",
}

INDIAN_SECTOR_INDEX_SYMBOLS = {
    "banking": "^NSEBANK",
    "financial_services": "NIFTY_FIN_SERVICE.NS",
    "it": "^CNXIT",
    "auto": "^CNXAUTO",
    "pharma": "^CNXPHARMA",
    "fmcg": "^CNXFMCG",
    "metals": "^CNXMETAL",
    "realty": "^CNXREALTY",
    "psu_bank": "^CNXPSUBANK",
    "energy": "^CNXENERGY",
    "oil_gas": "NIFTY_OIL_AND_GAS.NS",
    "media": "^CNXMEDIA",
    "healthcare": "NIFTY_HEALTHCARE.NS",
}

THEME_TO_PROXY_MAP = {
    "financials": "^NSEBANK",
    "infrastructure": "^NSEI",
    "defence": "^NSEI",
    "railways_modernization": "^NSEI",
    "renewable_energy": "^NSEI",
    "electric_vehicles": "^CNXAUTO",
    "consumption": "^CNXFMCG",
    "manufacturing": "^NSEI",
    "psu_reform": "^NSEI",
    "digital_india_ai": "^CNXIT",
}

SECTOR_KEYWORDS = {
    "banking": ["bank", "banking", "nbfc", "lender", "credit", "loan", "deposit"],
    "financial_services": ["financial services", "insurance", "asset management", "wealth", "fintech", "mf", "mutual fund"],
    "it": ["software", "technology", "digital", "cloud", "it services", "saas", "ai services"],
    "pharma": ["pharma", "drug", "formulation", "api", "healthcare", "hospital"],
    "auto": ["auto", "automobile", "vehicle", "car", "truck", "ev", "two wheeler", "two-wheeler"],
    "defence": ["defence", "defense", "aerospace", "missile", "military"],
    "railways": ["railway", "railways", "rolling stock", "station redevelopment", "freight corridor"],
    "infrastructure": ["infrastructure", "infra", "construction", "capex", "roads", "bridges", "ports"],
    "renewable_energy": ["renewable", "solar", "wind", "green hydrogen", "battery storage"],
    "oil_gas": ["oil", "gas", "crude", "lng", "refinery", "petroleum"],
    "power": ["power", "electricity", "transmission", "distribution", "thermal"],
    "psu": ["psu", "state owned", "government owned", "cpse"],
    "metals": ["steel", "aluminium", "copper", "zinc", "metal"],
    "realty": ["real estate", "property", "housing", "commercial realty"],
    "fmcg": ["fmcg", "consumer staples", "packaged foods", "personal care"],
}

THEME_KEYWORDS = {
    "financials": ["bank", "banking", "nbfc", "financial services", "insurance", "credit"],
    "infrastructure": ["infrastructure", "infra", "capex", "roads", "bridges", "ports"],
    "defence": ["defence", "defense", "aerospace", "missile", "military"],
    "railways_modernization": ["railway", "railways", "station redevelopment", "freight corridor"],
    "renewable_energy": ["renewable", "solar", "wind", "green hydrogen"],
    "electric_vehicles": ["ev", "electric vehicle", "charging", "battery"],
    "consumption": ["consumer", "retail", "fmcg", "discretionary"],
    "manufacturing": ["manufacturing", "factory", "production linked incentive", "pli"],
    "psu_reform": ["disinvestment", "privatisation", "privatization", "psu reform"],
    "digital_india_ai": ["ai", "artificial intelligence", "cloud", "digital transformation", "data center"],
}


# =========================================================
# MARKET DATA
# =========================================================

def _build_price_block(symbol: str, display_name: str | None = None) -> dict:
    try:
        hist = _safe_history(symbol, period="5d", interval="1d")
        if hist is None or hist.empty:
            return {
                "symbol": symbol,
                "name": display_name or symbol,
                "price": None,
                "prev_close": None,
                "day_change_pct": None,
                "volume": None,
            }

        current = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
        volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else None
        day_change_pct = ((current - prev_close) / prev_close) * 100 if prev_close else None

        return {
            "symbol": symbol,
            "name": display_name or symbol,
            "price": round(current, 4),
            "prev_close": round(prev_close, 4),
            "day_change_pct": round(day_change_pct, 4) if day_change_pct is not None else None,
            "volume": int(volume) if volume is not None else None,
        }
    except Exception:
        return {
            "symbol": symbol,
            "name": display_name or symbol,
            "price": None,
            "prev_close": None,
            "day_change_pct": None,
            "volume": None,
        }


def get_indian_indices() -> dict:
    out = {}
    for name, symbol in INDIAN_INDEX_SYMBOLS.items():
        out[name] = _build_price_block(symbol, name)
    return out


def get_indian_sector_indices() -> dict:
    out = {}
    for sector, symbol in INDIAN_SECTOR_INDEX_SYMBOLS.items():
        out[sector] = _build_price_block(symbol, sector)
    return out


# =========================================================
# ATR / REACTION
# =========================================================

def get_indian_asset_atr(symbol: str, period: int = 14) -> dict:
    try:
        yf_symbol = _normalize_symbol_for_market_data(symbol)
        df = yf.Ticker(yf_symbol).history(period="30d")
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


def calculate_indian_reaction(symbol: str, published_iso: str) -> dict:
    """
    Calculates move from first available candle at/after publish time to now.
    Falls back to daily if intraday unavailable.
    """
    try:
        yf_symbol = _normalize_symbol_for_market_data(symbol)
        pub_dt = datetime.fromisoformat((published_iso or "").strip().replace("Z", "+00:00"))
        pub_dt = _to_utc(pub_dt)
        now_dt = datetime.now(timezone.utc)

        ticker = yf.Ticker(yf_symbol)

        start = pub_dt - timedelta(hours=8)
        end = now_dt + timedelta(minutes=5)

        df = ticker.history(start=start, end=end, interval="15m")
        if df is not None and not df.empty:
            if df.index.tz is None:
                df.index = df.index.tz_localize(timezone.utc)
            else:
                df.index = df.index.tz_convert(timezone.utc)

            post_df = df[df.index >= pub_dt]
            if post_df is not None and not post_df.empty:
                news_price = float(post_df["Close"].iloc[0])
                current_price = float(df["Close"].iloc[-1])
                reaction_pct = ((current_price - news_price) / news_price) * 100 if news_price else 0.0
                return {
                    "news_price": round(news_price, 6),
                    "current_price": round(current_price, 6),
                    "reaction_pct": round(reaction_pct, 6),
                    "interval_used": "15m",
                }

        df = ticker.history(period="30d", interval="1d")
        if df is None or df.empty:
            return {}

        if df.index.tz is None:
            df.index = df.index.tz_localize(timezone.utc)
        else:
            df.index = df.index.tz_convert(timezone.utc)

        post_df = df[df.index >= pub_dt]
        if post_df is None or post_df.empty:
            post_df = df.tail(1)

        news_price = float(post_df["Close"].iloc[0])
        current_price = float(df["Close"].iloc[-1])
        reaction_pct = ((current_price - news_price) / news_price) * 100 if news_price else 0.0

        return {
            "news_price": round(news_price, 6),
            "current_price": round(current_price, 6),
            "reaction_pct": round(reaction_pct, 6),
            "interval_used": "1d",
        }
    except Exception:
        return {}


def classify_indian_reaction_status(reaction_pct: float, atr_pct_reference: float) -> str:
    try:
        move = abs(float(reaction_pct))
        atr = abs(float(atr_pct_reference))
        if atr <= 0:
            if move < 0.5:
                return "underreacted"
            if move < 2.0:
                return "normal_reaction"
            return "overreacted"

        ratio = move / atr
        if ratio < 0.35:
            return "underreacted"
        if ratio <= 1.25:
            return "normal_reaction"
        if ratio <= 2.0:
            return "strong_reaction"
        return "overreacted"
    except Exception:
        return "normal_reaction"


# =========================================================
# TEXT NORMALIZATION / HEADLINE SIGNALS
# =========================================================

_RE_NUMBERS = re.compile(r"\d+(?:\.\d+)?")
_RE_PUNCTUATION = re.compile(r"[^a-z0-9\s<>]")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_PCT = re.compile(r"(\d+(\.\d+)?)\s*%")
_RE_MONEY = re.compile(r"(₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE)

def _normalize_event_title(title: str) -> str:
    t = (title or "").lower().strip()
    t = t.replace("%", " percent ").replace("&", " and ")
    t = t.replace("rbi’s", "rbi").replace("sebi’s", "sebi")
    t = _RE_NUMBERS.sub(" <num> ", t)
    t = _RE_PUNCTUATION.sub(" ", t)
    t = _RE_WHITESPACE.sub(" ", t).strip()
    return t


def detect_indian_theme(title: str) -> str:
    t = (title or "").lower()

    if any(x in t for x in ["rbi", "repo rate", "crr", "slr", "monetary policy", "liquidity infusion", "liquidity withdrawal"]):
        return "rbi_policy"
    if any(x in t for x in ["sebi", "margin rule", "surveillance", "ban", "settlement cycle", "compliance", "show cause"]):
        return "sebi_regulation"
    if any(x in t for x in ["budget", "union budget", "allocation", "capital expenditure", "capex", "outlay"]):
        return "budget_policy"
    if any(x in t for x in ["fii", "dii", "foreign institutional", "domestic institutional"]):
        return "fii_dii_flows"
    if any(x in t for x in ["cpi", "wpi", "gdp", "iip", "inflation", "fiscal deficit", "current account"]):
        return "macro_data"
    if any(x in t for x in ["order win", "order book", "contract", "l1 bidder", "work order"]):
        return "order_win"
    if any(x in t for x in ["results", "q1", "q2", "q3", "q4", "earnings", "profit", "ebitda", "margin"]):
        return "earnings"
    if any(x in t for x in ["promoter stake", "stake sale", "block deal", "bulk deal", "pledge"]):
        return "promoter_action"
    if any(x in t for x in ["defence", "missile", "armed forces", "defense procurement"]):
        return "defence_policy"
    if any(x in t for x in ["railway", "railways", "station redevelopment", "rail infra"]):
        return "railways_capex"
    if any(x in t for x in ["infra", "infrastructure", "roads", "highways", "ports"]):
        return "infra_capex"
    if any(x in t for x in ["oil", "crude", "lng", "gas", "petrol", "diesel"]):
        return "commodity_pass_through"

    return "general"


def has_indian_escalation_words(title: str) -> bool:
    t = (title or "").lower()
    escalation_words = [
        "repo rate hike",
        "repo rate cut",
        "crr hike",
        "crr cut",
        "liquidity infusion",
        "liquidity withdrawal",
        "sebi bans",
        "sebi action",
        "government approves",
        "cabinet approves",
        "wins order worth",
        "surges to record",
        "cuts guidance",
        "profit warning",
        "default",
        "insolvency",
        "raid",
        "fraud",
    ]
    return any(x in t for x in escalation_words)


def detect_indian_reaction_headline(title: str) -> dict:
    t = (title or "").lower()

    pct_match = _RE_PCT.search(t)
    move_pct = float(pct_match.group(1)) if pct_match else None

    move_words = ["jumps", "surges", "falls", "drops", "slides", "gains", "rises", "up", "down"]
    reaction_headline = any(w in t for w in move_words) and move_pct is not None

    new_catalyst_words = [
        "after results",
        "after earnings",
        "after order",
        "after contract",
        "after rbi policy",
        "after sebi action",
        "after government decision",
    ]
    has_new_catalyst = any(x in t for x in new_catalyst_words)

    return {
        "reaction_headline": reaction_headline,
        "headline_move_pct": move_pct,
        "has_new_catalyst": has_new_catalyst,
    }


def detect_corporate_event_type(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()

    if any(x in text for x in ["results", "net profit", "revenue", "ebitda", "margin"]):
        return "earnings"
    if any(x in text for x in ["order win", "order worth", "contract", "work order", "l1 bidder"]):
        return "order_win"
    if any(x in text for x in ["acquire", "acquisition", "merge", "merger", "demerger"]):
        return "mna"
    if any(x in text for x in ["stake sale", "block deal", "bulk deal", "pledge", "promoter sold"]):
        return "stake_change"
    if any(x in text for x in ["qip", "rights issue", "fpo", "fund raise", "fundraising", "preferential issue"]):
        return "fundraising"
    if any(x in text for x in ["penalty", "fine", "regulatory action", "show cause", "investigation"]):
        return "regulatory_issue"
    if any(x in text for x in ["expansion", "capacity expansion", "new plant", "commissioning"]):
        return "expansion"
    if any(x in text for x in ["debt", "borrowing", "loan", "ncd", "bond issue"]):
        return "debt"
    if any(x in text for x in ["dividend", "buyback", "bonus issue", "split"]):
        return "capital_return"
    if any(x in text for x in ["shutdown", "fire", "accident", "closure"]):
        return "operational_disruption"

    return "general_corporate_update"


# =========================================================
# MAPPING HELPERS
# =========================================================

def map_companies_from_text(title: str, summary: str = "", max_results: int = 5) -> dict:
    """
    Conservative company mapping with explicit quality tiers.

    Tiers:
      - exact:  Full normalized company name found as phrase in text (score 0.98)
      - exact_symbol: NSE symbol found as phrase in text (score 0.96)
      - strong: Shortened name (>= 4 words, drop last) found as phrase (score 0.88)
      - weak:   Fuzzy match, strict guards (ratio >= 0.80, name >= 15 chars, >= 3 words)
      - rejected: Everything else — excluded from output entirely

    Only exact + strong tiers should influence tradable stock output.
    Weak tier may support sector/theme context only.
    """
    text = _normalize_text(f"{title} {summary}")
    if not text:
        return {"matches": [], "mapping_confidence": 0.0}

    try:
        rows = fetch_all(
            """
            SELECT
                company_name,
                nse_symbol,
                isin
            FROM companies
            WHERE nse_symbol IS NOT NULL
              AND TRIM(nse_symbol) <> ''
            """
        ) or []
    except Exception:
        rows = []

    matches: list[dict] = []

    for row in rows:
        company_name = (row.get("company_name") or "").strip()
        symbol = (row.get("nse_symbol") or "").strip().upper()
        isin = (row.get("isin") or "").strip()

        if not company_name or not symbol:
            continue

        cname = _normalize_text(company_name)
        sym = symbol.lower()
        cname_words = cname.split()

        score = 0.0
        match_text = ""
        tier = "rejected"

        # --- Tier: exact (full normalized name as phrase) ---
        if _contains_phrase(text, cname):
            score = 0.98
            match_text = company_name
            tier = "exact"

        # --- Tier: exact_symbol (NSE symbol as phrase) ---
        elif _contains_phrase(text, sym):
            score = 0.96
            match_text = symbol
            tier = "exact_symbol"

        # --- Tier: strong (shortened name, >= 4 words, drop last generic word) ---
        elif len(cname_words) >= 4:
            shortened = " ".join(cname_words[:-1])
            if _contains_phrase(text, shortened):
                score = 0.88
                match_text = company_name
                tier = "strong"

        # --- Tier: weak (fuzzy, heavily guarded) ---
        # Only attempt fuzzy if name is long enough to be meaningful
        # and has enough words to avoid matching generic terms
        if tier == "rejected" and len(cname) >= 15 and len(cname_words) >= 3:
            ratio_name = SequenceMatcher(None, cname, text).ratio()
            if ratio_name >= 0.80:
                score = round(ratio_name * 0.85, 4)  # Discount fuzzy score
                match_text = company_name
                tier = "weak"

        # Only keep exact, strong, and weak tiers
        if tier != "rejected":
            matches.append(
                {
                    "symbol": symbol,
                    "company_name": company_name,
                    "isin": isin,
                    "match_text": match_text,
                    "confidence": round(score, 4),
                    "tier": tier,
                }
            )

    matches.sort(key=lambda x: (-x["confidence"], x["company_name"]))
    matches = matches[:max_results]

    # mapping_confidence is based on BEST match only from exact/strong tiers
    # Weak tier matches do NOT contribute to mapping_confidence
    tradable_matches = [m for m in matches if m["tier"] in {"exact", "exact_symbol", "strong"}]
    top_conf = tradable_matches[0]["confidence"] if tradable_matches else 0.0
    mapping_confidence = round(_clamp(top_conf, 0.0, 1.0), 4)

    return {
        "matches": matches,
        "mapping_confidence": mapping_confidence,
    }


def map_sectors_from_text(title: str, summary: str = "") -> list[str]:
    text = _normalize_text(f"{title} {summary}")
    found: list[str] = []

    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if _contains_phrase(text, _normalize_text(kw)):
                found.append(sector)
                break

    return sorted(set(found))


def map_themes_from_text(title: str, summary: str = "") -> list[str]:
    text = _normalize_text(f"{title} {summary}")
    found: list[str] = []

    for theme, keywords in THEME_KEYWORDS.items():
        for kw in keywords:
            if _contains_phrase(text, _normalize_text(kw)):
                found.append(theme)
                break

    return sorted(set(found))


# =========================================================
# RECENT NEWS / FATIGUE / NOVELTY
# =========================================================

def search_recent_indian_news(title: str, current_news_id: int | None = None, hours_back: int = 48) -> dict:
    normalized_title = _normalize_event_title(title)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    try:
        rows = fetch_all(
            """
            SELECT id, title, published_at
            FROM news
            WHERE published_at >= %s
            ORDER BY published_at DESC
            """,
            (cutoff,),
        ) or []
    except Exception:
        rows = []

    similar = []
    for row in rows:
        row_id = row.get("id")
        row_title = row.get("title") or ""
        if current_news_id is not None and row_id == current_news_id:
            continue

        ratio = SequenceMatcher(None, normalized_title, _normalize_event_title(row_title)).ratio()
        if ratio >= 0.78:
            similar.append({"id": row_id, "title": row_title, "similarity": round(ratio, 4)})

    return {
        "priced_in": len(similar) >= 3,
        "similar_matches": similar[:10],
        "count": len(similar),
    }


def get_indian_repetition_context(title: str, current_news_id: int | None = None) -> dict:
    normalized_title = _normalize_event_title(title)
    now = datetime.now(timezone.utc)
    cutoff_12h = now - timedelta(hours=12)
    cutoff_24h = now - timedelta(hours=24)

    try:
        rows = fetch_all(
            """
            SELECT id, title, published_at
            FROM news
            WHERE published_at >= %s
            ORDER BY published_at DESC
            """,
            (cutoff_24h,),
        ) or []
    except Exception:
        rows = []

    similar_12h = 0
    similar_24h = 0
    theme_news_12h = 0
    theme_news_24h = 0

    current_theme = detect_indian_theme(title)

    for row in rows:
        row_id = row.get("id")
        row_title = row.get("title") or ""
        row_pub = row.get("published_at")

        if current_news_id is not None and row_id == current_news_id:
            continue

        ratio = SequenceMatcher(None, normalized_title, _normalize_event_title(row_title)).ratio()
        row_theme = detect_indian_theme(row_title)

        if row_pub and row_pub >= cutoff_12h:
            if ratio >= 0.78:
                similar_12h += 1
            if row_theme == current_theme:
                theme_news_12h += 1

        if ratio >= 0.78:
            similar_24h += 1
        if row_theme == current_theme:
            theme_news_24h += 1

    fatigue_score = min(10, similar_24h + max(theme_news_24h - 1, 0))
    repetition_level = "low"
    if fatigue_score >= 6:
        repetition_level = "high"
    elif fatigue_score >= 3:
        repetition_level = "medium"

    return {
        "theme": current_theme,
        "similar_news_12h": similar_12h,
        "similar_news_24h": similar_24h,
        "theme_news_12h": theme_news_12h,
        "theme_news_24h": theme_news_24h,
        "fatigue_score": fatigue_score,
        "repetition_level": repetition_level,
        "has_escalation_words": has_indian_escalation_words(title),
    }


def get_indian_novelty_label(title: str, current_news_id: int | None = None) -> str:
    rep = get_indian_repetition_context(title, current_news_id=current_news_id)
    if rep["similar_news_24h"] == 0:
        return "new_information"
    if rep["has_escalation_words"]:
        return "escalation_of_existing_theme"
    if rep["similar_news_24h"] >= 2:
        return "repetition_only"
    return "update_to_existing_theme"


def compute_indian_remaining_tradable_impact(
    base_event_impact: int,
    published_at: datetime,
    title: str,
    current_news_id: int | None = None,
) -> dict:
    hours_old = max(0.0, (datetime.now(timezone.utc) - _to_utc(published_at)).total_seconds() / 3600.0)
    novelty_label = get_indian_novelty_label(title, current_news_id=current_news_id)
    rep = get_indian_repetition_context(title, current_news_id=current_news_id)

    fatigue_score = rep.get("fatigue_score", 0)
    repetition_level = rep.get("repetition_level", "low")
    adjusted_fatigue_score = fatigue_score

    fatigue_penalty = 0
    if repetition_level == "medium":
        fatigue_penalty = 1
    elif repetition_level == "high":
        fatigue_penalty = 2

    time_decay_penalty = 0
    if hours_old > 12:
        time_decay_penalty = 2
    elif hours_old > 4:
        time_decay_penalty = 1

    novelty_bonus = 0
    if novelty_label == "new_information":
        novelty_bonus = 1
    elif novelty_label == "escalation_of_existing_theme":
        novelty_bonus = 1

    remaining = base_event_impact - fatigue_penalty - time_decay_penalty + novelty_bonus
    remaining = int(_clamp(remaining, 0, 10))

    return {
        "base_event_impact": base_event_impact,
        "theme": rep.get("theme", "general"),
        "repetition_level": repetition_level,
        "fatigue_score": fatigue_score,
        "adjusted_fatigue_score": adjusted_fatigue_score,
        "fatigue_penalty": fatigue_penalty,
        "time_decay_penalty": time_decay_penalty,
        "novelty_label": novelty_label,
        "remaining_tradable_impact": remaining,
    }


# =========================================================
# INDIA CONTEXT HELPERS
# =========================================================

def get_fii_dii_flows() -> dict:
    """
    Best-effort from DB if available.
    Expected optional table columns:
    trade_date, fii_net, dii_net
    """
    try:
        row = fetch_one(
            """
            SELECT trade_date, fii_net, dii_net
            FROM fii_dii_flows
            ORDER BY trade_date DESC
            LIMIT 1
            """
        )
        if not row:
            return {}

        return {
            "trade_date": str(row.get("trade_date") or ""),
            "fii_net": _safe_float(row.get("fii_net")),
            "dii_net": _safe_float(row.get("dii_net")),
        }
    except Exception:
        return {}


def get_fii_dii_bias() -> dict:
    flows = get_fii_dii_flows()
    fii = _safe_float(flows.get("fii_net"))
    dii = _safe_float(flows.get("dii_net"))

    def _bias(v: float | None) -> str:
        if v is None:
            return "unknown"
        if v > 0:
            return "bullish"
        if v < 0:
            return "bearish"
        return "neutral"

    strength = 0
    if fii is not None:
        strength += min(5, int(abs(fii) / 1000))
    if dii is not None:
        strength += min(5, int(abs(dii) / 1000))
    strength = int(_clamp(strength, 0, 10))

    return {
        "fii_flow_bias": _bias(fii),
        "dii_flow_bias": _bias(dii),
        "institutional_flow_signal_strength": strength,
        "fii_net": fii,
        "dii_net": dii,
    }


def get_rbi_policy_context() -> dict:
    return {
        "current_regime": "unknown",
        "policy_bias": "neutral",
        "policy_sensitivity_score": 5,
    }


def get_sebi_regulatory_context() -> dict:
    return {
        "regulatory_tightness": "neutral",
        "market_structure_sensitivity_score": 5,
    }


def get_government_policy_context() -> dict:
    return {
        "policy_push": "neutral",
        "government_policy_sensitivity_score": 5,
    }


# =========================================================
# PRODUCTION-GRADE DETERMINISTIC HELPERS
# =========================================================

def get_indian_event_family(title: str, summary: str = "") -> str:
    """
    Categorizes headline into one of:
    macro_policy, regulation_policy, market_flow, earnings,
    corporate_action, order_contract, management, commentary,
    commodity_macro, general_corporate
    """
    text = f"{title} {summary}".lower()
    
    if any(x in text for x in ["rbi", "repo rate", "monetary policy", "liquidity", "gdp", "cpi", "inflation"]):
        return "macro_policy"
    if any(x in text for x in ["sebi", "regulation", "tariff", "duty", "ban", "rule change", "compliance"]):
        return "regulation_policy"
    if any(x in text for x in ["fii", "dii", "net sell", "net buy", "block deal", "bulk deal", "flows"]):
        return "market_flow"
    if any(x in text for x in ["results", "profit", "revenue", "ebitda", "margin", "earnings"]):
        return "earnings"
    if any(x in text for x in ["dividend", "buyback", "split", "bonus", "rights issue"]):
        return "corporate_action"
    if any(x in text for x in ["order win", "contract", "l1 bidder", "loi", "signed"]):
        return "order_contract"
    if any(x in text for x in ["ceo", "cfo", "resigns", "appoints", "management", "board"]):
        return "management"
    if any(x in text for x in ["brokerage", "analyst", "upgrade", "downgrade", "target price", "expects", "likely"]):
        return "commentary"
    if any(x in text for x in ["crude", "oil", "commodity", "gold", "steel", "metal price"]):
        return "commodity_macro"
    
    return "general_corporate"


def normalize_indian_source_credibility(source: str) -> dict:
    """
    Normalizes source_type and produces source_strength.
    Canonical source types: regulator, exchange, government, company_filing, financial_media, broker, unknown
    """
    s = (source or "").strip().lower()
    
    if any(x in s for x in ["rbi", "sebi", "nse", "bse", "regulator", "exchange"]):
        return {"source_type": "regulator", "source_strength": 1.0}
    if any(x in s for x in ["government", "pib", "ministry", "cabinet"]):
        return {"source_type": "government", "source_strength": 0.95}
    if any(x in s for x in ["company filing", "results", "filing", "investor updates"]):
        return {"source_type": "company_filing", "source_strength": 0.9}
    if any(x in s for x in ["bloomberg", "reuters", "moneycontrol", "economic times", "mint", "cnbc", "business standard"]):
        return {"source_type": "financial_media", "source_strength": 0.8}
    if any(x in s for x in ["broker", "brokerage", "research report", "jefferies", "morgan stanley", "goldman"]):
        return {"source_type": "broker", "source_strength": 0.7}
        
    return {"source_type": "unknown", "source_strength": 0.5}


def get_deterministic_indian_asset_mappings(family: str, title: str, summary: str = "") -> dict:
    """
    Conservative deterministic asset mappings.

    Design rules:
    - Family alone must NOT produce broad sector/theme lists
    - Only map indices/sectors when the relationship is unambiguous
    - Prefer sparse/empty output over speculative breadth
    - Commodity headlines map to primary exposed sector ONLY (not downstream guesses)
    - Themes are NOT added from family alone — they come from keyword text analysis
    """
    out: dict[str, list[str]] = {"indices": [], "sectors": [], "themes": []}

    if family == "macro_policy":
        # Only NIFTY 50 — do NOT auto-map banking/realty/financials.
        # The LLM must evaluate whether the specific macro event affects those.
        out["indices"] = ["NIFTY 50"]
    elif family == "market_flow":
        # Flow is a broad-market signal, but only the benchmark index
        out["indices"] = ["NIFTY 50"]
    elif family == "commodity_macro":
        text = f"{title} {summary}".lower()
        # Only the primary exposed sector — no downstream guesses
        # (e.g. oil → oil_gas only, NOT paints/aviation — those are second-order,
        #  LLM must evaluate them independently)
        if any(x in text for x in ["oil", "crude", "petrol"]):
            out["sectors"] = ["oil_gas"]
        elif any(x in text for x in ["steel", "iron", "metal"]):
            out["sectors"] = ["metals"]
    # regulation_policy, earnings, order_contract, etc. → no auto-mapping.
    # The LLM uses keyword-based sector/theme analysis + company mapping.

    return out


def get_composite_indian_mapping_confidence(mapped_entities: dict, family: str, source_strength: float) -> float:
    """
    Conservative composite mapping confidence score (0.0 - 1.0).

    Design rules:
    - Stock company mapping is the strongest signal (weighted 0.50)
    - Keyword-based sector/index matching is WEAK (0.4/0.3 conf) —
      a keyword match is NOT the same as confirmed economic linkage
    - Family membership does NOT inflate confidence
    - Source credibility is supporting evidence only
    - The result should reflect TRUE certainty about mapping quality,
      not optimistic assumption
    """
    w_stock = 0.50
    w_sector = 0.20
    w_index = 0.15
    w_source = 0.15

    comp_map = mapped_entities.get("company_mapping", {})
    stock_conf = float(comp_map.get("mapping_confidence", 0.0))

    # Keyword sector/index matches are NOT confirmed mappings — use low confidence
    sector_conf = 0.4 if mapped_entities.get("sectors") else 0.0
    index_conf = 0.3 if mapped_entities.get("indices") else 0.0

    # NO inflation for macro_policy / market_flow — family ≠ confirmed mapping

    score = (stock_conf * w_stock) + (sector_conf * w_sector) + (index_conf * w_index) + (source_strength * w_source)
    return round(_clamp(score, 0.0, 1.0), 4)


def get_event_aware_reaction_basket(
    mapped: dict,
    published_iso: str,
    market_scope: str,
    mapping_confidence: float = 0.0,
) -> dict:
    """
    Calculates market reaction based on event scope.

    GUARD: If mapping_confidence < 0.6, we do NOT trust the mapped symbols
    enough to use them as price confirmation. Weak mapping → fake confirmation
    is one of the most dangerous failure modes. Return no_market_confirmation.

    For broad_market scope we still check NIFTY 50 (always valid).
    """
    no_confirmation = {
        "reaction_pct": 0.0,
        "atr_pct_reference": 1.0,
        "reaction_status": "no_market_confirmation",
    }

    if market_scope == "broad_market":
        # NIFTY 50 is always valid for broad market — no mapping trust needed
        symbols_to_check = ["^NSEI"]
    elif mapping_confidence < 0.6:
        # Weak mapping — do NOT use mapped symbols as price confirmation.
        # This prevents fake confirmation from bad company matches.
        return no_confirmation
    elif market_scope in {"sector", "sector_specific"}:
        symbols_to_check = mapped.get("sector_symbols", [])[:2]
    elif market_scope in {"single_stock", "peer_group", "stock_specific"}:
        symbols_to_check = mapped.get("stock_symbols", [])[:3]
    else:
        symbols_to_check = mapped.get("all_symbols", [])[:2]

    if not symbols_to_check:
        return no_confirmation

    reactions = []
    atrs = []

    for sym in symbols_to_check:
        res = calculate_indian_reaction(sym, published_iso)
        atr = get_indian_asset_atr(sym)
        if res and "reaction_pct" in res:
            reactions.append(res["reaction_pct"])
            atrs.append(atr.get("atr_pct_reference") or 1.0)

    if not reactions:
        return no_confirmation

    avg_reaction = sum(reactions) / len(reactions)
    avg_atr = sum(atrs) / len(atrs)
    status = classify_indian_reaction_status(avg_reaction, avg_atr)

    return {
        "reaction_pct": round(avg_reaction, 4),
        "atr_pct_reference": round(avg_atr, 4),
        "reaction_status": status,
    }


def get_indian_market_status() -> dict:
    now_ist = _now_ist()
    weekday = now_ist.weekday()

    equities = "closed"
    if weekday < 5:
        market_open = datetime.combine(now_ist.date(), time(9, 15), tzinfo=IST)
        market_close = datetime.combine(now_ist.date(), time(15, 30), tzinfo=IST)
        if market_open <= now_ist <= market_close:
            equities = "regular"

    currency = "closed"
    if weekday < 5:
        c_open = datetime.combine(now_ist.date(), time(9, 0), tzinfo=IST)
        c_close = datetime.combine(now_ist.date(), time(17, 0), tzinfo=IST)
        if c_open <= now_ist <= c_close:
            currency = "open"

    commodity = "closed"
    if weekday < 5:
        mcx_open = datetime.combine(now_ist.date(), time(9, 0), tzinfo=IST)
        mcx_close = datetime.combine(now_ist.date(), time(23, 30), tzinfo=IST)
        if mcx_open <= now_ist <= mcx_close:
            commodity = "open"

    return {
        "timestamp_ist": now_ist.isoformat(),
        "indian_equities": equities,
        "currency_market": currency,
        "commodity_market": commodity,
    }


# =========================================================
# AGENT CONTEXT + VALIDATION + POST-PROCESS + ORCHESTRATOR
# =========================================================

BIAS_ENUM = {"bullish", "bearish", "mixed", "neutral", "unclear"}
ROLE_ENUM = {"direct", "indirect", "peer"}
STATUS_ENUM = {"confirmed", "developing", "rumor", "follow_up", "noise"}
SCOPE_ENUM = {"single_stock", "peer_group", "sector", "broad_market"}
HORIZON_ENUM = {"intraday", "short_term", "medium_term", "long_term"}
SURPRISE_ENUM = {"low", "medium", "high", "unknown"}
MOVE_BAND_ENUM = {"0-1%", "1-3%", "3-5%", "5-8%", "8%+", "unclear"}

SUPPORTED_TOP_LEVEL_KEYS = {
    "event",
    "analysis",
    "market_logic",
    "affected_entities",
    "stock_impacts",
    "scenario",
    "evidence",
    "missing_info",
    "executive_summary",
}


def _safe_iso(ts: str) -> str:
    try:
        return _to_utc(datetime.fromisoformat((ts or "").replace("Z", "+00:00"))).isoformat()
    except Exception:
        return str(ts or "")


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for x in items or []:
        if x and x not in seen:
            out.append(x)
            seen.add(x)
    return out


def _confidence_label(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _impact_label(score: int) -> str:
    if score >= 8:
        return "strong"
    if score >= 5:
        return "moderate"
    if score >= 3:
        return "mild"
    return "weak"


def _move_band_rank(band: str) -> int:
    order = ["0-1%", "1-3%", "3-5%", "5-8%", "8%+", "unclear"]
    try:
        return order.index(band)
    except Exception:
        return len(order) - 1


def _cap_move_band(band: str, cap: str) -> str:
    if band not in MOVE_BAND_ENUM:
        return cap
    if cap not in MOVE_BAND_ENUM:
        return band
    if band == "unclear":
        return band
    if cap == "unclear":
        return band
    return band if _move_band_rank(band) <= _move_band_rank(cap) else cap


def _infer_market_scope(
    family: str,
    company_mapping: dict,
    sectors: list[str],
    themes: list[str],
) -> str:
    """
    Conservative HINT for market scope — NOT a hard decision engine.
    The LLM must independently evaluate scope based on directness, breadth,
    and materiality. This hint is passed as advisory context only.
    """
    stock_count = len((company_mapping or {}).get("matches", []))

    # Macro/flow events naturally span broad market — safe hint
    if family in {"macro_policy", "market_flow"}:
        return "broad_market"

    # Single clearly-mapped stock with no sector spillover — safe hint
    if stock_count == 1 and not sectors:
        return "single_stock"

    # Multiple mapped stocks, or one stock with sector context — hint peer_group
    if stock_count >= 2 or (stock_count >= 1 and sectors):
        return "peer_group"

    # Sector keywords but no specific company — hint sector
    if sectors:
        return "sector"

    # Default: when context is ambiguous, "sector" is the safest default.
    # broad_market should be reserved for events that genuinely span multiple unrelated sectors.
    return "sector"


def _infer_status(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if any(x in text for x in ["rumor", "reportedly", "may", "could", "unconfirmed", "talks"]):
        return "rumor"
    if any(x in text for x in ["said", "says", "according to", "expected", "likely"]):
        return "developing"
    if any(x in text for x in ["approved", "announced", "declared", "reported", "wins", "signed", "files"]):
        return "confirmed"
    return "developing"


def _infer_bias_from_family(family: str, title: str, summary: str = "") -> str:
    """
    Conservative HINT for market bias — NOT a hard decision engine.

    Design rules:
    - Only HIGH-PRECISION keyword signals produce a non-unclear hint
    - Generic words like 'contract', 'approved', 'expansion', 'falls' are
      excluded because they fire in unrelated contexts
    - 'stake sale' is NOT always bearish (PE exit can be positive)
    - 'approved' is NOT always positive (regulatory context can be negative)
    - Price-action words ('falls', 'drops', 'slides', 'surges', 'jumps', 'rises', 'gains')
      are excluded — they describe past price movement, not fundamental impact
    - Family name alone NEVER determines bias
    """
    text = f"{title} {summary}".lower()

    # Bearish: only strong negative FUNDAMENTAL signals
    bearish_signals = [
        "cuts guidance", "profit warning", "default", "insolvency",
        "fraud", "raid", "shutdown", "closure", "misses estimates",
        "margin slips", "margin decline", "loss widens",
    ]
    # Bullish: only strong positive FUNDAMENTAL signals
    bullish_signals = [
        "wins order", "order worth", "buyback", "dividend",
        "bonus", "split", "strong results", "beats estimates",
        "profit rises", "profit jumps", "margin expands",
    ]

    if any(x in text for x in bearish_signals):
        return "bearish"
    if any(x in text for x in bullish_signals):
        return "bullish"

    # No high-precision signal found — return unclear so LLM decides independently
    return "unclear"


def _infer_horizon(family: str) -> str:
    """
    Conservative HINT for time horizon — NOT a hard decision engine.
    The LLM must independently assess horizon based on the specific event
    content, magnitude, and second-order effects.
    These are reasonable starting defaults only.
    """
    if family in {"market_flow", "commentary"}:
        return "intraday"       # Hint: flow/commentary typically intraday
    if family in {"earnings", "order_contract", "corporate_action", "management"}:
        return "short_term"     # Hint: corporate events typically short_term
    if family in {"macro_policy", "regulation_policy", "commodity_macro"}:
        return "medium_term"   # Hint: policy/macro typically medium_term
    return "short_term"         # Conservative default


def _infer_surprise(title: str, repetition_context: dict) -> str:
    """
    Conservative HINT for surprise level — NOT a hard decision engine.
    Based only on repetition signals; the LLM must factor in expectation
    context, analyst consensus, and market positioning independently.
    """
    similar_24h = repetition_context.get("similar_news_24h", 0)
    has_escalation = repetition_context.get("has_escalation_words", False)

    # No similar recent headlines → likely fresh → hint: high surprise
    if similar_24h == 0:
        return "high"
    # Escalation language on already-seen theme → still noteworthy
    if has_escalation:
        return "medium"
    # Heavily repeated theme → probably priced in → hint: low surprise
    if similar_24h >= 2:
        return "low"
    # Ambiguous — let LLM decide
    return "unknown"


def _infer_financial_impacts(family: str, title: str, summary: str = "") -> list[str]:
    text = f"{title} {summary}".lower()
    impacts = []

    if family == "order_contract":
        impacts += ["order_book", "revenue", "sentiment"]
    elif family == "earnings":
        impacts += ["revenue", "margin", "valuation"]
    elif family == "macro_policy":
        impacts += ["regulation", "sentiment", "valuation"]
    elif family == "market_flow":
        impacts += ["sentiment", "valuation"]
    elif family == "commodity_macro":
        impacts += ["cost", "margin", "sentiment"]
    elif family == "regulation_policy":
        impacts += ["regulation", "valuation"]
    else:
        impacts += ["sentiment"]

    if "capex" in text or "expansion" in text:
        impacts.append("revenue")
    if "margin" in text:
        impacts.append("margin")
    if "demand" in text:
        impacts.append("demand")
    if "cost" in text or "crude" in text or "oil" in text:
        impacts.append("cost")

    return _dedupe_keep_order(impacts)


def _build_causal_chain(family: str, bias: str, title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()

    if family == "order_contract":
        return "New order / contract -> stronger order book visibility -> better revenue visibility -> positive stock reaction"
    if family == "earnings":
        return "Results update -> market re-prices earnings / margin outlook -> valuation adjusts -> stock reacts"
    if family == "macro_policy":
        return "Policy signal -> sector sensitivity repriced -> broad market and rate-sensitive names react"
    if family == "market_flow":
        return "Institutional flows / deal activity -> near-term demand-supply imbalance -> price reaction in affected names"
    if family == "commodity_macro":
        if any(x in text for x in ["oil", "crude", "petrol", "diesel"]):
            return "Commodity move -> input cost / pass-through changes -> margin outlook shifts -> sector and stock reactions follow"
        return "Commodity price move -> earnings sensitivity repriced -> related sectors react"
    if family == "regulation_policy":
        return "Regulatory / policy change -> compliance / demand / profitability outlook changes -> affected names re-rate"

    if bias == "bullish":
        return "Positive trigger -> better sentiment / earnings expectations -> selective buying in affected names"
    if bias == "bearish":
        return "Negative trigger -> weaker sentiment / earnings expectations -> selling pressure in affected names"
    return "Headline arrives -> market evaluates directness, materiality, and confirmation -> reaction depends on follow-up details"


def _default_why_points(
    family: str,
    bias: str,
    mapped: dict,
    novelty_label: str,
    source_type: str,
) -> list[str]:
    points = []

    if family == "order_contract":
        points.append("Order-related headlines can improve order book visibility and near-term sentiment.")
    elif family == "earnings":
        points.append("Earnings-related updates can directly affect profit, margin, and valuation expectations.")
    elif family == "macro_policy":
        points.append("Macro and policy signals can reprice rate-sensitive and benchmark-heavy sectors.")
    elif family == "commodity_macro":
        points.append("Commodity moves can change cost structures and margin outlook for exposed sectors.")
    elif family == "regulation_policy":
        points.append("Regulatory changes can alter compliance cost, growth visibility, or sector positioning.")
    else:
        points.append("The headline may influence sentiment and repricing in linked Indian equities.")

    if mapped.get("company_mapping", {}).get("matches"):
        points.append("There are directly mappable listed names in the headline/summary.")
    elif mapped.get("sectors") or mapped.get("themes"):
        points.append("The headline has identifiable sector/theme spillover even without a single explicit company.")

    if novelty_label == "new_information":
        points.append("This appears to be relatively fresh information, so tradable impact may still remain.")
    elif novelty_label == "repetition_only":
        points.append("Similar headlines appeared recently, so some of the impact may already be priced in.")

    if source_type in {"regulator", "government", "company_filing"}:
        points.append("Source credibility is relatively strong, which supports higher conviction if market logic is clear.")

    return points[:3]


def build_analysis_context(
    title: str,
    summary: str,
    published_iso: str,
    source: str,
    current_news_id: int | None = None,
) -> dict:
    """
    Build one deterministic context blob for the LLM.
    This should be passed along with title/summary/time.
    """
    title = title or ""
    summary = summary or ""
    published_iso = _safe_iso(published_iso)

    family = get_indian_event_family(title, summary)
    source_meta = normalize_indian_source_credibility(source)
    company_mapping = map_companies_from_text(title, summary, max_results=5)
    sectors = map_sectors_from_text(title, summary)
    themes = map_themes_from_text(title, summary)
    deterministic = get_deterministic_indian_asset_mappings(family, title, summary)

    merged_sectors = _dedupe_keep_order(sectors + deterministic.get("sectors", []))
    merged_themes = _dedupe_keep_order(themes + deterministic.get("themes", []))
    indices = _dedupe_keep_order(deterministic.get("indices", []))

    market_scope = _infer_market_scope(family, company_mapping, merged_sectors, merged_themes)

    stock_symbols = [m.get("symbol") for m in company_mapping.get("matches", []) if m.get("symbol")]
    sector_symbols = []
    for sec in merged_sectors:
        sec_sym = INDIAN_SECTOR_INDEX_SYMBOLS.get(sec)
        if sec_sym:
            sector_symbols.append(sec_sym)

    theme_symbols = []
    for theme in merged_themes:
        proxy = THEME_TO_PROXY_MAP.get(theme)
        if proxy:
            theme_symbols.append(proxy)

    mapped = {
        "company_mapping": company_mapping,
        "sectors": merged_sectors,
        "themes": merged_themes,
        "indices": indices,
        "stock_symbols": stock_symbols,
        "sector_symbols": sector_symbols,
        "theme_symbols": theme_symbols,
        "all_symbols": _dedupe_keep_order(stock_symbols + sector_symbols + theme_symbols + indices),
    }

    composite_mapping_confidence = get_composite_indian_mapping_confidence(
        mapped_entities=mapped,
        family=family,
        source_strength=float(source_meta.get("source_strength", 0.5)),
    )

    repetition_context = get_indian_repetition_context(title, current_news_id=current_news_id)
    novelty_label = get_indian_novelty_label(title, current_news_id=current_news_id)
    market_status = get_indian_market_status()
    reaction_basket = get_event_aware_reaction_basket(
        mapped, published_iso, market_scope,
        mapping_confidence=composite_mapping_confidence,
    )

    remaining_tradable = compute_indian_remaining_tradable_impact(
        base_event_impact=7,
        published_at=datetime.fromisoformat(published_iso.replace("Z", "+00:00")),
        title=title,
        current_news_id=current_news_id,
    )

    reaction_signal = detect_indian_reaction_headline(title)
    theme = detect_indian_theme(title)
    corp_type = detect_corporate_event_type(title, summary)
    status = _infer_status(title, summary)
    bias = _infer_bias_from_family(family, title, summary)
    horizon = _infer_horizon(family)
    surprise = _infer_surprise(title, repetition_context)
    impacts = _infer_financial_impacts(family, title, summary)
    causal_chain = _build_causal_chain(family, bias, title, summary)

    # --- Separate validated (exact/strong) vs weak company matches ---
    all_matches = company_mapping.get("matches", [])
    validated_matches = [m for m in all_matches if m.get("tier") in {"exact", "exact_symbol", "strong"}]
    weak_matches = [m for m in all_matches if m.get("tier") == "weak"]
    validated_stock_symbols = [m.get("symbol") for m in validated_matches if m.get("symbol")]

    # --- STRUCTURED CONTEXT WITH SIGNAL-QUALITY TIERS ---
    # This makes signal quality explicit. The LLM can see which signals
    # are trustworthy (hard_facts, validated_mappings) vs tentative (weak_hints).
    return {
        "hard_facts": {
            "title": title,
            "summary": summary,
            "published_iso": published_iso,
            "source": source,
            "source_type": source_meta.get("source_type", "unknown"),
            "source_strength": source_meta.get("source_strength", 0.5),
            "market_status": market_status,
            "current_news_id": current_news_id,
        },
        "validated_mappings": {
            "company_matches": validated_matches,
            "mapped_stock_symbols": validated_stock_symbols,
            "mapping_confidence": composite_mapping_confidence,
            "note": "Only exact/strong tier matches. These have high-confidence text linkage to the headline.",
        },
        "weak_hints": {
            "event_family": family,
            "detected_theme": theme,
            "corporate_event_type": corp_type,
            "weak_company_matches": weak_matches,
            "keyword_sectors": merged_sectors,
            "keyword_themes": merged_themes,
            "scope_hint": market_scope,
            "status_hint": status,
            "bias_hint": bias,
            "horizon_hint": horizon,
            "surprise_hint": surprise,
            "financial_impacts": impacts,
            "causal_chain_hint": causal_chain,
            "why_it_matters_hint": _default_why_points(
                family=family,
                bias=bias,
                mapped=mapped,
                novelty_label=novelty_label,
                source_type=source_meta.get("source_type", "unknown"),
            ),
            "note": "These are keyword-based hints with mixed reliability. Validate independently.",
        },
        "market_observations": {
            "repetition": repetition_context,
            "novelty_label": novelty_label,
            "reaction": reaction_basket,
            "remaining_tradable_impact": remaining_tradable,
            "flow_context": get_fii_dii_bias(),
            "note": "Market-level observations. Reaction data is only reliable when mapping_confidence >= 0.6.",
        },
        # --- Internal fields used by post-processing (not for LLM reasoning) ---
        "_internal": {
            "classification": {
                "event_family": family,
                "market_scope": market_scope,
                "status_hint": status,
                "bias_hint": bias,
                "horizon_hint": horizon,
                "surprise_hint": surprise,
            },
            "source": source_meta,
            "mapping": mapped,
            "mapping_confidence": composite_mapping_confidence,
            "reaction": reaction_basket,
            "repetition": repetition_context,
            "novelty_label": novelty_label,
            "signal_hints": {
                "reaction_headline": reaction_signal,
                "financial_impacts": impacts,
                "causal_chain_hint": causal_chain,
                "why_it_matters_hint": _default_why_points(
                    family=family,
                    bias=bias,
                    mapped=mapped,
                    novelty_label=novelty_label,
                    source_type=source_meta.get("source_type", "unknown"),
                ),
            },
        },
    }


def validate_agent_output(payload: dict) -> dict:
    """
    Raises ValueError on invalid output.
    Returns the same payload if valid.
    """
    if not isinstance(payload, dict):
        raise ValueError("Agent output must be a dict")

    missing_top = [k for k in SUPPORTED_TOP_LEVEL_KEYS if k not in payload]
    if missing_top:
        raise ValueError(f"Missing top-level keys: {missing_top}")

    event = payload.get("event") or {}
    analysis = payload.get("analysis") or {}
    market_logic = payload.get("market_logic") or {}
    affected_entities = payload.get("affected_entities") or {}
    stock_impacts = payload.get("stock_impacts") or []
    scenario = payload.get("scenario") or {}

    if event.get("status") not in STATUS_ENUM:
        raise ValueError(f"Invalid event.status: {event.get('status')}")
    if event.get("scope") not in SCOPE_ENUM:
        raise ValueError(f"Invalid event.scope: {event.get('scope')}")
    if analysis.get("market_bias") not in BIAS_ENUM:
        raise ValueError(f"Invalid analysis.market_bias: {analysis.get('market_bias')}")
    if analysis.get("horizon") not in HORIZON_ENUM:
        raise ValueError(f"Invalid analysis.horizon: {analysis.get('horizon')}")
    if analysis.get("surprise") not in SURPRISE_ENUM:
        raise ValueError(f"Invalid analysis.surprise: {analysis.get('surprise')}")

    # impact_score is 0-10; confidence is 0-100 — validate separately
    impact_val = analysis.get("impact_score")
    if not isinstance(impact_val, int) or not (0 <= impact_val <= 10):
        raise ValueError(f"analysis.impact_score must be int 0-10, got: {impact_val!r}")

    conf_val = analysis.get("confidence")
    if not isinstance(conf_val, int) or not (0 <= conf_val <= 100):
        raise ValueError(f"analysis.confidence must be int 0-100, got: {conf_val!r}")

    if not isinstance(analysis.get("why_it_matters"), list):
        raise ValueError("analysis.why_it_matters must be a list")

    if not isinstance(market_logic.get("financial_impact"), list):
        raise ValueError("market_logic.financial_impact must be a list")

    if not isinstance(affected_entities.get("stocks"), list):
        raise ValueError("affected_entities.stocks must be a list")
    if not isinstance(affected_entities.get("sectors"), list):
        raise ValueError("affected_entities.sectors must be a list")

    if not isinstance(stock_impacts, list):
        raise ValueError("stock_impacts must be a list")
    if len(stock_impacts) > 10:
        raise ValueError("stock_impacts too long; max 10")

    for idx, item in enumerate(stock_impacts):
        if item.get("role") not in ROLE_ENUM:
            raise ValueError(f"stock_impacts[{idx}].role invalid")
        if item.get("bias") not in BIAS_ENUM:
            raise ValueError(f"stock_impacts[{idx}].bias invalid")

        conf = item.get("confidence")
        if not isinstance(conf, int) or not (0 <= conf <= 100):
            raise ValueError(f"stock_impacts[{idx}].confidence must be int 0-100")

        exp = item.get("expected_move") or {}
        if exp.get("intraday") not in MOVE_BAND_ENUM:
            raise ValueError(f"stock_impacts[{idx}].expected_move.intraday invalid")
        if exp.get("short_term") not in MOVE_BAND_ENUM:
            raise ValueError(f"stock_impacts[{idx}].expected_move.short_term invalid")

        if item.get("bias") in {"bullish", "bearish", "mixed"} and not item.get("why"):
            raise ValueError(f"stock_impacts[{idx}] missing why for non-unclear bias")

        if item.get("bias") == "unclear":
            if exp.get("intraday") != "unclear" or exp.get("short_term") != "unclear":
                raise ValueError(f"stock_impacts[{idx}] unclear bias cannot have explicit move bands")

    if event.get("scope") == "single_stock" and len(stock_impacts) < 1:
        raise ValueError("single_stock scope requires at least one stock_impacts item")

    if analysis.get("confidence", 0) >= 70 and not payload.get("evidence"):
        raise ValueError("high-confidence output requires evidence")

    if analysis.get("market_bias") in {"bullish", "bearish"} and not market_logic.get("causal_chain"):
        raise ValueError("directional analysis requires market_logic.causal_chain")

    soi = scenario.get("second_order_insights") or []
    if not isinstance(soi, list):
        raise ValueError("scenario.second_order_insights must be a list")

    for idx, item in enumerate(soi):
        conf = item.get("confidence")
        if not isinstance(conf, int) or not (0 <= conf <= 100):
            raise ValueError(f"scenario.second_order_insights[{idx}].confidence must be int 0-100")

    return payload


def post_process_agent_output(payload: dict, context: dict) -> dict:
    """
    Deterministic cleanup after LLM output.
    Caps confidence, trims stock list, downgrades fuzzy mappings,
    and fills weak missing pieces conservatively.
    """
    out = dict(payload)

    event = out.setdefault("event", {})
    analysis = out.setdefault("analysis", {})
    market_logic = out.setdefault("market_logic", {})
    affected_entities = out.setdefault("affected_entities", {})
    scenario = out.setdefault("scenario", {})
    stock_impacts = out.setdefault("stock_impacts", [])
    evidence = out.setdefault("evidence", [])
    missing_info = out.setdefault("missing_info", [])

    # Post-processing uses _internal context (not the LLM-facing tiers)
    _ctx = context.get("_internal", context)  # Fallback to flat context for backward compat
    source_strength = float(_ctx.get("source", {}).get("source_strength", 0.5))
    mapping_conf = float(_ctx.get("mapping_confidence", 0.0))
    novelty_label = _ctx.get("novelty_label", "update_to_existing_theme")
    rep = _ctx.get("repetition", {}) or {}
    reaction = _ctx.get("reaction", {}) or {}
    mapped = _ctx.get("mapping", {}) or {}

    # Clamp impact_score to 0-10 and confidence to 0-100 first
    analysis["impact_score"] = int(_clamp(int(analysis.get("impact_score", 0) or 0), 0, 10))
    analysis["confidence"] = int(_clamp(int(analysis.get("confidence", 0) or 0), 0, 100))

    # --- ENUM NORMALIZATION ---
    # LLM sometimes returns non-enum values (e.g. "positive" instead of "high").
    # Normalize to valid enums BEFORE validation.
    _surprise_map = {
        "positive": "high", "negative": "high", "very high": "high",
        "significant": "high", "moderate": "medium", "partial": "medium",
        "expected": "low", "none": "low", "minimal": "low",
    }
    raw_surprise = str(analysis.get("surprise", "unknown")).lower().strip()
    if raw_surprise not in SURPRISE_ENUM:
        analysis["surprise"] = _surprise_map.get(raw_surprise, "unknown")

    _bias_map = {
        "positive": "bullish", "negative": "bearish", "uncertain": "unclear",
    }
    raw_bias = str(analysis.get("market_bias", "unclear")).lower().strip()
    if raw_bias not in BIAS_ENUM:
        analysis["market_bias"] = _bias_map.get(raw_bias, "unclear")

    raw_status = str(event.get("status", "developing")).lower().strip()
    if raw_status not in STATUS_ENUM:
        event["status"] = "developing"

    raw_scope = str(event.get("scope", "sector")).lower().strip()
    if raw_scope not in SCOPE_ENUM:
        event["scope"] = "sector"

    raw_horizon = str(analysis.get("horizon", "short_term")).lower().strip()
    if raw_horizon not in HORIZON_ENUM:
        analysis["horizon"] = "short_term"

    # Base confidence caps — derived from source strength and mapping quality.
    # Cap is set conservatively; system prompt mandates never exceeding 85.
    source_cap = int(round(source_strength * 100))
    mapping_cap = int(round(max(35.0, mapping_conf * 100)))
    hard_cap = min(85, max(35, int((source_cap * 0.6) + (mapping_cap * 0.4))))

    if event.get("status") == "rumor":
        hard_cap = min(hard_cap, 45)

    if novelty_label == "repetition_only":
        # Repeated theme: cap impact at 6/10 (not 65 — impact_score is 0-10)
        analysis["impact_score"] = min(analysis["impact_score"], 6)
        hard_cap = min(hard_cap, 60)

    if rep.get("fatigue_score", 0) >= 6:
        # High fatigue: cap impact at 6/10 (not 60 — impact_score is 0-10)
        analysis["impact_score"] = min(analysis["impact_score"], 6)

    if reaction.get("reaction_status") == "overreacted":
        missing_info.append("Current price action may already reflect a large part of the headline impact.")
    elif reaction.get("reaction_status") == "no_market_confirmation":
        hard_cap = min(hard_cap, 70)

    analysis["confidence"] = min(analysis["confidence"], hard_cap)

    if not analysis.get("summary"):
        analysis["summary"] = _ctx.get("signal_hints", {}).get("causal_chain_hint", "")

    if not analysis.get("why_it_matters"):
        analysis["why_it_matters"] = _ctx.get("signal_hints", {}).get("why_it_matters_hint", [])[:3]

    if not market_logic.get("financial_impact"):
        market_logic["financial_impact"] = _ctx.get("signal_hints", {}).get("financial_impacts", [])

    if not market_logic.get("causal_chain"):
        market_logic["causal_chain"] = _ctx.get("signal_hints", {}).get("causal_chain_hint", "")

    # Ensure entity lists are synced
    explicit_symbols = [x.get("symbol") for x in stock_impacts if x.get("symbol")]
    mapped_symbols = [x.get("symbol") for x in mapped.get("company_mapping", {}).get("matches", []) if x.get("symbol")]
    affected_entities["stocks"] = _dedupe_keep_order(explicit_symbols or mapped_symbols)
    affected_entities["sectors"] = _dedupe_keep_order((affected_entities.get("sectors") or []) + (mapped.get("sectors") or []))

    # REMOVED: Synthetic stock impact fabrication.
    # If the LLM returned no stock_impacts, we trust that decision.
    # Post-processing must NOT invent tradable stock calls from weak mapping.
    # This was the #1 source of manufactured conviction in previous versions.

    # Cap and normalize stock impacts
    cleaned_stock_impacts = []
    family = _ctx.get("classification", {}).get("event_family", "")
    title_text = (event.get("title", "") or "").lower()

    for item in stock_impacts[:5]:
        item = dict(item)
        item["symbol"] = str(item.get("symbol", "")).strip().upper()
        item["company_name"] = str(item.get("company_name", "")).strip()
        item["role"] = item.get("role", "peer")
        item["bias"] = item.get("bias", "unclear")

        # --- RULE 1: ROLE CORRECTION (HARD OVERRIDE) ---
        # earnings event + company is main subject → role MUST be "direct"
        # macro / cost / supply chain → role = "indirect"
        # commentary / peer-only context → role = "peer"
        company_lower = (item.get("company_name") or "").lower()
        sym_lower = (item.get("symbol") or "").lower()

        # Strip corporate suffixes for matching: "Infosys Ltd" → "infosys"
        _corp_suffixes = {"ltd", "limited", "inc", "corp", "corporation", "pvt", "private", "nse", "bse"}
        core_name_words = [w for w in company_lower.split() if w not in _corp_suffixes]
        core_name = " ".join(core_name_words).strip()

        is_main_subject = (
            (core_name and core_name in title_text)
            or (sym_lower and sym_lower in title_text)
        )

        if family == "earnings" and is_main_subject:
            item["role"] = "direct"  # Earnings event where company IS the headline
        elif family in {"macro_policy", "commodity_macro", "market_flow"}:
            item["role"] = "indirect"  # Macro/commodity/flow = indirect linkage
        elif family == "commentary":
            item["role"] = "peer"  # Analyst talk is peer sentiment
        elif family in {"order_contract", "corporate_action"} and is_main_subject:
            item["role"] = "direct"  # Direct corporate action on named company
        elif item.get("role") not in ROLE_ENUM:
            item["role"] = "peer"  # Fallback for invalid/missing role

        if item["role"] not in ROLE_ENUM:
            item["role"] = "peer"
        if item["bias"] not in BIAS_ENUM:
            item["bias"] = "unclear"

        exp = item.get("expected_move") or {}
        if not isinstance(exp, dict):
            exp = {"intraday": "unclear", "short_term": "unclear"}
        intraday = exp.get("intraday", "unclear")
        short_term = exp.get("short_term", "unclear")

        # Low mapping confidence guard — but exempt headline-confirmed subjects.
        # If the company IS the headline subject, mapping_conf from DB is irrelevant.
        role_protected = is_main_subject and family in {"earnings", "order_contract", "corporate_action"}
        if mapping_conf < 0.55 and not role_protected:
            item["role"] = "peer" if item["role"] == "direct" else item["role"]
            intraday = _cap_move_band(intraday, "0-1%")
            short_term = _cap_move_band(short_term, "1-3%")

        if event.get("status") == "rumor":
            intraday = _cap_move_band(intraday, "1-3%")
            short_term = _cap_move_band(short_term, "3-5%")

        if item["bias"] == "unclear":
            intraday = "unclear"
            short_term = "unclear"

        item["expected_move"] = {
            "intraday": intraday if intraday in MOVE_BAND_ENUM else "unclear",
            "short_term": short_term if short_term in MOVE_BAND_ENUM else "unclear",
        }

        conf = int(_clamp(int(item.get("confidence", analysis["confidence"])), 0, 100))
        item["confidence"] = min(conf, analysis["confidence"])

        if not item.get("why"):
            item["why"] = "Market impact exists but article detail is limited, so this remains a lower-conviction mapping."
        if not item.get("risk"):
            item["risk"] = "Full financial materiality is not clearly disclosed in the summary."
        if not item.get("invalidation"):
            item["invalidation"] = "Follow-up disclosures do not confirm direct earnings, cost, demand, or order-book impact."

        cleaned_stock_impacts.append(item)

    out["stock_impacts"] = cleaned_stock_impacts

    # Re-sync stocks
    affected_entities["stocks"] = _dedupe_keep_order([x.get("symbol") for x in out["stock_impacts"] if x.get("symbol")])

    # Scope correction
    if event.get("scope") == "single_stock" and len(out["stock_impacts"]) != 1:
        if len(out["stock_impacts"]) > 1:
            event["scope"] = "peer_group"
        else:
            event["scope"] = _ctx.get("classification", {}).get("market_scope", "sector")

    # Evidence and missing info cleanup
    out["evidence"] = _dedupe_keep_order([str(x).strip() for x in evidence if str(x).strip()])[:3]
    out["missing_info"] = _dedupe_keep_order([str(x).strip() for x in missing_info if str(x).strip()])[:4]

    # Scenario cleanup
    soi = scenario.get("second_order_insights") or []
    cleaned_soi = []
    for item in soi[:3]:
        if not isinstance(item, dict):
            continue
        if_text = str(item.get("if", "")).strip()
        then_text = str(item.get("then", "")).strip()
        conf = int(_clamp(int(item.get("confidence", analysis["confidence"])), 0, 100))
        if if_text and then_text:
            cleaned_soi.append(
                {
                    "if": if_text,
                    "then": then_text,
                    "confidence": min(conf, analysis["confidence"]),
                }
            )
    scenario["second_order_insights"] = cleaned_soi

    if not scenario.get("invalidation_trigger"):
        scenario["invalidation_trigger"] = (
            out["stock_impacts"][0]["invalidation"]
            if out["stock_impacts"]
            else "Follow-up details fail to confirm direct material impact on Indian listed equities."
        )

    if not out.get("executive_summary"):
        direction = analysis.get("market_bias", "unclear")
        horizon = analysis.get("horizon", "short_term").replace("_", " ")
        title = event.get("title", "this headline")
        out["executive_summary"] = f"{direction.title()} setup over the {horizon} for Indian equities based on: {title}"

    return out


def _refinement_calibrate(out: dict, context: dict) -> dict:
    """
    REFINEMENT MODE — correct and calibrate output to match real market behavior.
    Runs AFTER post_process, BEFORE validate.

    Enforces 8 hard rules:
    1. Role correction (earnings main subject → direct)
    2. Move calibration (high impact → minimum move bands)
    3. Confidence + impact alignment (missing info → reduce)
    4. Sector limit (max 3)
    5. No automatic sector readthrough (conditional phrasing)
    6. Uncertainty expression (incomplete info → hedged language)
    7. Over/under correction (too aggressive → reduce confidence, not logic)
    8. Final sanity check (trader realism)
    """
    event = out.get("event", {})
    analysis = out.get("analysis", {})
    stock_impacts = out.get("stock_impacts", [])
    affected_entities = out.get("affected_entities", {})
    missing_info = out.get("missing_info", [])
    event_type = (event.get("event_type") or "").lower()
    impact_score = int(analysis.get("impact_score", 0))
    confidence = int(analysis.get("confidence", 0))
    surprise = (analysis.get("surprise") or "unknown").lower()

    # ---- RULE 2: MOVE CALIBRATION ----
    # For earnings events:
    #   low surprise → 0-1%, moderate → 1-3%, strong → 3-5%
    #   NEVER allow earnings event → 0-1% by default when impact is meaningful
    # For any event with impact_score >= 7:
    #   intraday MUST be >= 1-3%
    for item in stock_impacts:
        exp = item.get("expected_move", {})
        intraday = exp.get("intraday", "unclear")
        short_term = exp.get("short_term", "unclear")
        bias = item.get("bias", "unclear")

        if bias in {"bullish", "bearish", "mixed"}:
            # Earnings-specific move calibration
            if event_type == "earnings" and item.get("role") == "direct":
                if surprise in {"high"} and intraday in {"0-1%", "unclear"}:
                    intraday = "3-5%"
                elif surprise in {"medium"} and intraday in {"0-1%", "unclear"}:
                    intraday = "1-3%"
                elif surprise in {"low", "unknown"} and intraday == "unclear":
                    intraday = "0-1%"

                if surprise in {"high"} and short_term in {"0-1%", "unclear"}:
                    short_term = "3-5%"
                elif surprise in {"medium"} and short_term in {"0-1%", "unclear"}:
                    short_term = "1-3%"
                elif surprise in {"low", "unknown"} and short_term == "unclear":
                    short_term = "0-1%"

            # High impact → minimum intraday 1-3%
            if impact_score >= 7 and _move_band_rank(intraday) < _move_band_rank("1-3%"):
                intraday = "1-3%"
            if impact_score >= 7 and _move_band_rank(short_term) < _move_band_rank("1-3%"):
                short_term = "1-3%"

        item["expected_move"] = {
            "intraday": intraday if intraday in MOVE_BAND_ENUM else "unclear",
            "short_term": short_term if short_term in MOVE_BAND_ENUM else "unclear",
        }

    # ---- RULE 3: CONFIDENCE + IMPACT ALIGNMENT ----
    # Missing guidance / margin / revenue detail → reduce confidence by 5-15, impact by 1
    missing_lower = " ".join(str(m).lower() for m in missing_info)
    has_missing_guidance = any(w in missing_lower for w in ["guidance", "outlook", "forecast"])
    has_missing_detail = any(w in missing_lower for w in ["margin", "revenue", "breakdown", "detail"])

    if has_missing_guidance and has_missing_detail:
        analysis["confidence"] = max(0, confidence - 15)
        analysis["impact_score"] = max(0, impact_score - 1)
    elif has_missing_guidance or has_missing_detail:
        analysis["confidence"] = max(0, confidence - 5)

    # Strong confirmed event should not have confidence below 60
    if event.get("status") == "confirmed" and impact_score >= 5:
        analysis["confidence"] = max(analysis["confidence"], 60)

    # Re-cap at 85
    analysis["confidence"] = min(analysis["confidence"], 85)
    analysis["impact_score"] = int(_clamp(analysis["impact_score"], 0, 10))

    # ---- RULE 4: SECTOR LIMIT (max 3) ----
    sectors = affected_entities.get("sectors", [])
    if len(sectors) > 3:
        affected_entities["sectors"] = sectors[:3]

    # ---- RULE 5 + 6: CONDITIONAL PHRASING / UNCERTAINTY ----
    # Patch executive_summary with hedged language when info is incomplete
    exec_summary = out.get("executive_summary", "")
    if exec_summary and (has_missing_guidance or has_missing_detail):
        # Replace deterministic language with hedged language
        replacements = [
            ("will move", "likely to move"),
            ("will benefit", "could benefit"),
            ("will drive", "may drive"),
            ("will see", "may see"),
            ("will face", "could face"),
            ("expect a ", "may see a "),
            ("Expect a ", "May see a "),
            ("expect ", "may "),
        ]
        for old, new in replacements:
            exec_summary = exec_summary.replace(old, new)
        out["executive_summary"] = exec_summary

    # ---- RULE 5: NO AUTOMATIC SECTOR READTHROUGH ----
    # If only one stock, do not claim "positive for entire sector"
    # unless guidance or multiple companies support it
    if exec_summary and len(stock_impacts) <= 1:
        sector_readthrough_phrases = [
            "positive for the sector",
            "positive for entire sector",
            "sector-wide rally",
            "sector-wide positive",
            "driving the sector",
            "lift the sector",
        ]
        for phrase in sector_readthrough_phrases:
            if phrase in exec_summary.lower():
                out["executive_summary"] = exec_summary.replace(
                    phrase, f"potentially {phrase} if supported by peer results"
                ).replace(
                    phrase.capitalize(), f"Potentially {phrase} if supported by peer results"
                )

    # ---- RULE 7: OVER/UNDER CORRECTION ----
    # If move band is aggressive relative to impact score → reduce confidence, not logic
    for item in stock_impacts:
        exp = item.get("expected_move", {})
        intraday_rank = _move_band_rank(exp.get("intraday", "unclear"))
        # 3-5% (rank 2) or higher with impact < 5 → too aggressive
        if intraday_rank >= 2 and impact_score < 5:
            item["confidence"] = max(35, item.get("confidence", 50) - 15)
        # 5-8% or higher with impact < 7 → very aggressive
        if intraday_rank >= 3 and impact_score < 7:
            item["confidence"] = max(35, item.get("confidence", 50) - 10)

    # ---- RULE 8: FINAL SANITY CHECK ----
    # Propagate updated confidence cap to stock impacts
    final_conf = analysis["confidence"]
    for item in stock_impacts:
        item["confidence"] = min(item.get("confidence", final_conf), final_conf)

    return out


def run_indian_news_analysis(
    llm_callable,
    title: str,
    summary: str,
    published_iso: str,
    source: str,
    current_news_id: int | None = None,
) -> dict:
    """
    Main orchestrator.

    llm_callable must be a function like:
        llm_callable(prompt_context: dict) -> dict

    Flow:
    1. Build deterministic context
    2. Call LLM
    3. Post-process
    4. Validate
    5. Return final output
    """
    context = build_analysis_context(
        title=title,
        summary=summary,
        published_iso=published_iso,
        source=source,
        current_news_id=current_news_id,
    )

    raw_output = llm_callable(context)
    if not isinstance(raw_output, dict):
        raise ValueError("llm_callable must return a dict")

    # Post-processing and defaults use _internal context
    _ctx = context.get("_internal", context)

    # Fill some missing basics from deterministic context
    raw_output.setdefault("event", {})
    raw_output["event"].setdefault("title", title)
    raw_output["event"].setdefault("source", source)
    raw_output["event"].setdefault("timestamp_utc", _safe_iso(published_iso))
    raw_output["event"].setdefault("event_type", _ctx.get("classification", {}).get("event_family", "other"))
    raw_output["event"].setdefault("status", _ctx.get("classification", {}).get("status_hint", "developing"))
    raw_output["event"].setdefault("scope", _ctx.get("classification", {}).get("market_scope", "sector"))

    raw_output.setdefault("analysis", {})
    raw_output["analysis"].setdefault("market_bias", _ctx.get("classification", {}).get("bias_hint", "unclear"))
    raw_output["analysis"].setdefault("horizon", _ctx.get("classification", {}).get("horizon_hint", "short_term"))
    raw_output["analysis"].setdefault("surprise", _ctx.get("classification", {}).get("surprise_hint", "unknown"))
    raw_output["analysis"].setdefault("impact_score", 0)
    raw_output["analysis"].setdefault("confidence", int(round(_ctx.get("mapping_confidence", 0.0) * 100)))
    raw_output["analysis"].setdefault("summary", "")
    raw_output["analysis"].setdefault("why_it_matters", [])

    raw_output.setdefault("market_logic", {})
    raw_output["market_logic"].setdefault("financial_impact", _ctx.get("signal_hints", {}).get("financial_impacts", []))
    raw_output["market_logic"].setdefault("causal_chain", _ctx.get("signal_hints", {}).get("causal_chain_hint", ""))

    raw_output.setdefault("affected_entities", {"stocks": [], "sectors": []})
    raw_output.setdefault("stock_impacts", [])
    raw_output.setdefault("scenario", {"second_order_insights": [], "invalidation_trigger": ""})
    raw_output.setdefault("evidence", [])
    raw_output.setdefault("missing_info", [])
    raw_output.setdefault("executive_summary", "")

    final_output = post_process_agent_output(raw_output, context)
    final_output = _refinement_calibrate(final_output, context)
    validate_agent_output(final_output)
    return final_output