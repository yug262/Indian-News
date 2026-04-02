"""
Indian Market Intelligence — Minimal Tools Layer (V1)

Purpose:
- Give the agent only the tools it actually needs
- Keep logic deterministic and conservative
- Avoid turning tools.py into a second agent

Included:
- Company mapping (dynamic from DB)
- Sector extraction (dynamic from DB via matched companies)
- Current stock/index price snapshot
- Reaction since news time
- ATR reference
- Reaction classification
- Source credibility helper
- Indian market status helper
- Minimal context builder for the agent
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from app.core.db import fetch_all


# =========================================================
# CONSTANTS
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

# Fixed index map is OK because indices are limited and explicit
INDIAN_INDEX_SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100.NS",
}


# =========================================================
# GENERIC HELPERS
# =========================================================

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _now_ist() -> datetime:
    return datetime.now(IST)


def _safe_history(symbol: str, period: str = "5d", interval: str = "1d"):
    try:
        ticker = yf.Ticker(symbol)
        return ticker.history(period=period, interval=interval)
    except Exception:
        return None


def _normalize_nse_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


def _normalize_symbol_for_market_data(symbol: str) -> str:
    s = (symbol or "").strip()
    if not s:
        return s

    if s in INDIAN_INDEX_SYMBOLS:
        return INDIAN_INDEX_SYMBOLS[s]

    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s

    return _normalize_nse_symbol(s)


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

def _canonicalize_sector_label(label: str) -> str:
    x = _normalize_text(label)

    if not x:
        return ""

    if any(k in x for k in ["telecom", "telecommunication"]):
        return "telecom"

    if any(k in x for k in ["railway", "railways", "rail wagon", "rolling stock"]):
        return "railways"

    if any(k in x for k in ["capital goods", "industrial manufacturing", "engineering"]):
        return "capital_goods"

    if any(k in x for k in ["bank", "banking", "public sector bank", "private sector bank"]):
        return "banking"

    if any(k in x for k in ["financial services", "finance", "nbfc", "insurance", "asset management"]):
        return "financial_services"

    if any(k in x for k in ["software", "information technology", "it services", "technology"]):
        return "it"

    if any(k in x for k in ["pharma", "drug", "formulation", "healthcare", "hospital"]):
        return "pharma"

    if any(k in x for k in ["automobile", "auto", "vehicle", "two wheeler", "truck"]):
        return "auto"

    if any(k in x for k in ["oil", "gas", "petroleum", "refinery", "refineries", "marketing"]):
        return "oil_gas"

    if any(k in x for k in ["power", "electricity", "transmission", "distribution", "utility"]):
        return "power"

    if any(k in x for k in ["metal", "steel", "aluminium", "copper", "zinc", "mining"]):
        return "metals"

    if any(k in x for k in ["real estate", "realty", "property", "housing"]):
        return "realty"

    if any(k in x for k in ["consumer", "fmcg", "packaged foods", "personal care"]):
        return "fmcg"

    if any(k in x for k in ["media", "broadcast", "entertainment"]):
        return "media"

    if any(k in x for k in ["infrastructure", "construction", "roads", "bridges", "ports"]):
        return "infrastructure"

    if any(k in x for k in ["defence", "defense", "aerospace", "military"]):
        return "defence"

    return ""

# =========================================================
# MARKET DATA
# =========================================================

def _build_price_block(symbol: str, display_name: str | None = None) -> dict:
    """
    Returns a simple current snapshot based on recent daily candles.
    Works for:
    - NSE stocks like RELIANCE
    - Yahoo-style symbols like RELIANCE.NS
    - indices like ^NSEI
    """
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

        day_change_pct = None
        if prev_close:
            day_change_pct = ((current - prev_close) / prev_close) * 100

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


def get_indian_stock_price(symbol: str) -> dict:
    """
    Examples:
    - get_indian_stock_price("RELIANCE")
    - get_indian_stock_price("RELIANCE.NS")
    - get_indian_stock_price("^NSEI")
    - get_indian_stock_price("NIFTY 50")
    """
    s = (symbol or "").strip()
    if not s:
        return _build_price_block("", "")

    if s in INDIAN_INDEX_SYMBOLS:
        yf_symbol = INDIAN_INDEX_SYMBOLS[s]
        return _build_price_block(yf_symbol, s)

    if s.startswith("^"):
        return _build_price_block(s, s)

    yf_symbol = _normalize_nse_symbol(s)
    return _build_price_block(yf_symbol, s)


def get_indian_asset_atr(symbol: str, period: int = 14) -> dict:
    """
    Computes ATR and ATR% reference using recent daily candles.
    Useful for judging whether reaction is small, normal, or extreme.
    """
    try:
        yf_symbol = _normalize_symbol_for_market_data(symbol)
        df = yf.Ticker(yf_symbol).history(period="30d", interval="1d")

        if df is None or df.empty or len(df) < period:
            return {}

        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = (df["High"] - df["Close"].shift(1)).abs()
        df["L-PC"] = (df["Low"] - df["Close"].shift(1)).abs()

        tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        price = df["Close"].iloc[-1]

        if not price:
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
    Calculates reaction from the first available candle at/after the news publish time
    to the latest available price.

    First tries 15m intraday candles.
    Falls back to daily candles if needed.
    """
    try:
        yf_symbol = _normalize_symbol_for_market_data(symbol)
        pub_dt = datetime.fromisoformat((published_iso or "").strip().replace("Z", "+00:00"))
        pub_dt = _to_utc(pub_dt)
        now_dt = datetime.now(timezone.utc)

        ticker = yf.Ticker(yf_symbol)

        # Try intraday first
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

                reaction_pct = 0.0
                if news_price:
                    reaction_pct = ((current_price - news_price) / news_price) * 100

                return {
                    "news_price": round(news_price, 6),
                    "current_price": round(current_price, 6),
                    "reaction_pct": round(reaction_pct, 6),
                    "interval_used": "15m",
                }

        # Fallback to daily
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

        reaction_pct = 0.0
        if news_price:
            reaction_pct = ((current_price - news_price) / news_price) * 100

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
            if move < 1.5:
                return "normal_reaction"
            if move < 2.5:
                return "strong_reaction"
            return "overreacted"

        ratio = move / atr

        if ratio < 0.50:
            return "underreacted"
        if ratio <= 1.50:
            return "normal_reaction"
        if ratio <= 2.25:
            return "strong_reaction"
        return "overreacted"
    except Exception:
        return "normal_reaction"


# =========================================================
# COMPANY MAPPING
# =========================================================

def map_companies_from_text(title: str, summary: str = "", max_results: int = 5) -> dict:
    """
    Conservative company mapping from your companies table.

    Fixes:
    - Avoid false positives like "Bank of India" inside "Reserve Bank of India"
    - Stricter handling of generic company names
    - Weak fuzzy matches are heavily guarded
    """
    raw_text = f"{title} {summary}".strip()
    text = _normalize_text(raw_text)

    if not text:
        return {"matches": [], "mapping_confidence": 0.0}

    # Protected macro / regulator phrases.
    # If a company name appears only as part of one of these phrases,
    # do not treat it as a listed-company mapping.
    protected_phrases = [
        "reserve bank of india",
        "state bank of india act",
        "government of india",
        "ministry of finance",
        "securities and exchange board of india",
        "insurance regulatory and development authority of india",
        "competition commission of india",
    ]
    protected_hits = [p for p in protected_phrases if _contains_phrase(text, p)]

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
        cname_words = cname.split()
        sym = symbol.lower()

        score = 0.0
        match_text = ""
        tier = "rejected"

        # ------------------------------------------------------------------
        # SAFETY 1: If company phrase is embedded inside a protected phrase,
        # reject it unless the company itself is explicitly present separately.
        #
        # Example:
        # text contains "reserve bank of india"
        # company_name = "bank of india"
        # old system matched it incorrectly.
        # ------------------------------------------------------------------
        blocked_by_protected_context = False
        for protected in protected_hits:
            if cname != protected and cname in protected:
                # company phrase is a substring of protected macro phrase
                # reject unless the company phrase appears elsewhere too
                text_without_protected = text.replace(protected, " ")
                if not _contains_phrase(text_without_protected, cname):
                    blocked_by_protected_context = True
                    break

        if blocked_by_protected_context:
            continue

        # ------------------------------------------------------------------
        # SAFETY 2: Generic multi-word company names need stricter rules.
        # Especially names with very common words like india, bank, power, etc.
        # ------------------------------------------------------------------
        generic_tokens = {
            "india", "indian", "bank", "power", "finance", "financial",
            "services", "industries", "limited", "ltd", "corporation", "corp"
        }
        generic_overlap_count = sum(1 for w in cname_words if w in generic_tokens)
        generic_name = generic_overlap_count >= max(2, len(cname_words) // 2)

        # --- Tier: exact full company phrase ---
        if _contains_phrase(text, cname):
            # exact full name is OK, but generic names get a slight penalty
            score = 0.98 if not generic_name else 0.90
            match_text = company_name
            tier = "exact"

        # --- Tier: exact symbol phrase ---
        elif _contains_phrase(text, sym):
            score = 0.96
            match_text = symbol
            tier = "exact_symbol"

        # --- Tier: strong shortened company name ---
        elif len(cname_words) >= 4:
            shortened = " ".join(cname_words[:-1])
            if _contains_phrase(text, shortened):
                score = 0.88 if not generic_name else 0.78
                match_text = company_name
                tier = "strong"

        # --- Tier: weak fuzzy match (much stricter now) ---
        if tier == "rejected":
            # Only allow fuzzy if the company name is reasonably distinctive
            # and not too generic.
            if (
                len(cname) >= 18
                and len(cname_words) >= 3
                and not generic_name
            ):
                ratio_name = SequenceMatcher(None, cname, text).ratio()
                if ratio_name >= 0.84:
                    score = round(ratio_name * 0.82, 4)
                    match_text = company_name
                    tier = "weak"

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

    tradable_matches = [m for m in matches if m["tier"] in {"exact", "exact_symbol", "strong"}]
    top_conf = tradable_matches[0]["confidence"] if tradable_matches else 0.0

    return {
        "matches": matches,
        "mapping_confidence": round(_clamp(top_conf, 0.0, 1.0), 4),
    }


# =========================================================
# SECTOR EXTRACTION (DYNAMIC, DB-FIRST)
# =========================================================

def map_sectors_from_text(title: str, summary: str = "", max_company_matches: int = 5, precomputed_symbols: list[str] | None = None) -> list[str]:
    """
    Extract sector labels from DB for companies found in text.
    If precomputed_symbols is provided, skip the company re-scan.
    """
    if precomputed_symbols is not None:
        symbols = list(precomputed_symbols)
    else:
        company_map = map_companies_from_text(title, summary, max_results=max_company_matches)
        strong_matches = [
            m for m in company_map.get("matches", [])
            if m.get("tier") in {"exact", "exact_symbol", "strong"} and m.get("symbol")
        ]
        symbols = [m["symbol"] for m in strong_matches]

    if not symbols:
        return []

    placeholders = ", ".join(["%s"] * len(symbols))

    try:
        rows = fetch_all(
            f"""
            SELECT
                nse_symbol,
                sector,
                industry,
                basic_industry
            FROM companies
            WHERE nse_symbol IN ({placeholders})
            """,
            tuple(symbols),
        ) or []
    except Exception:
        rows = []

    found = []
    seen = set()

    for row in rows:
        raw_labels = [
            (row.get("sector") or "").strip(),
            (row.get("basic_industry") or "").strip(),
            (row.get("industry") or "").strip(),
        ]

        for raw in raw_labels:
            canon = _canonicalize_sector_label(raw)
            if canon and canon not in seen:
                seen.add(canon)
                found.append(canon)

    return found


# =========================================================
# SOURCE / MARKET CONTEXT HELPERS
# =========================================================

def normalize_indian_source_credibility(source: str) -> dict:
    """
    Simple source normalization.
    This is supportive context, not final truth.
    """
    s = (source or "").strip().lower()

    if any(x in s for x in ["rbi", "sebi", "nse", "bse", "regulator", "exchange"]):
        return {"source_type": "regulator", "source_strength": 1.0}

    if any(x in s for x in ["government", "pib", "ministry", "cabinet"]):
        return {"source_type": "government", "source_strength": 0.95}

    if any(x in s for x in ["company filing", "exchange filing", "results", "investor update", "investor presentation"]):
        return {"source_type": "company_filing", "source_strength": 0.9}

    if any(x in s for x in [
        "reuters",
        "bloomberg",
        "economic times",
        "moneycontrol",
        "mint",
        "business standard",
        "cnbc",
        "trade brains",
        "tradebrains",
    ]):
        return {"source_type": "financial_media", "source_strength": 0.8}

    if any(x in s for x in ["broker", "brokerage", "research report", "jefferies", "goldman", "morgan stanley"]):
        return {"source_type": "broker", "source_strength": 0.7}

    return {"source_type": "unknown", "source_strength": 0.5}


def get_indian_market_status() -> dict:
    """
    Lightweight market session helper.
    """
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
# TIME DECAY / EVENT TIMING HELPERS
# =========================================================

def _compute_event_timing(published_iso: str) -> dict:
    try:
        now_dt = _to_utc(datetime.now(timezone.utc))
        if not published_iso:
            return {
                "elapsed_minutes": 0,
                "decay_curve": "UNKNOWN",
                "analysis_time": now_dt.isoformat()
            }
        
        pub_dt = datetime.fromisoformat(published_iso.strip().replace("Z", "+00:00"))
        pub_dt = _to_utc(pub_dt)
        
        delta = now_dt - pub_dt
        minutes = int(delta.total_seconds() / 60)
        
        if minutes < 5:
            decay_curve = "FRESH"
        elif minutes <= 30:
            decay_curve = "MATURING"
        elif minutes <= 60:
            decay_curve = "AGING"
        else:
            decay_curve = "STALE"
            
        return {
            "elapsed_minutes": minutes,
            "decay_curve": decay_curve,
            "analysis_time": now_dt.isoformat()
        }
    except Exception:
        now_dt = _to_utc(datetime.now(timezone.utc))
        return {
            "elapsed_minutes": 0,
            "decay_curve": "UNKNOWN",
            "analysis_time": now_dt.isoformat()
        }


# =========================================================
# MINIMAL AGENT CONTEXT BUILDER
# =========================================================

def get_compact_reaction_context(symbol: str, published_iso: str, novelty: str = "UNKNOWN_NOVELTY") -> dict:
    """
    Returns a compact dict for the LLM exposing only decision-relevant fields.
    Raw payloads remain internal to Python for logging/debugging.
    """
    reaction = calculate_indian_reaction(symbol, published_iso)
    if not reaction:
        return {}

    atr = get_indian_asset_atr(symbol)
    reaction_status = "normal_reaction"
    
    reaction_pct = reaction.get("reaction_pct", 0.0)
    atr_pct_reference = 0.0
    if atr:
        atr_pct_reference = atr.get("atr_pct_reference", 0.0)
        reaction_status = classify_indian_reaction_status(
            reaction_pct,
            atr_pct_reference,
        )

    reaction_vs_atr = 0.0
    if atr_pct_reference > 0:
        reaction_vs_atr = round(abs(reaction_pct) / atr_pct_reference, 2)

    # Expected Move Proxy
    expected_move_proxy = 1.0 # Default fallback
    if novelty == "TRUE_CATALYST":
        expected_move_proxy = 1.5
    elif novelty == "EXPECTED_SURPRISE":
        expected_move_proxy = 1.2
    elif novelty == "EXPECTED_ROUTINE":
        expected_move_proxy = 0.6

    # Reaction Quality Model
    reaction_quality = "NORMAL_REACTION"
    if reaction_vs_atr < (0.5 * expected_move_proxy):
        reaction_quality = "UNDERREACTION"
    elif reaction_vs_atr > (1.2 * expected_move_proxy):
        reaction_quality = "OVERREACTION"

    # Absorption Gradient Model
    absorption_strength = "WEAK_ABSORPTION"
    if reaction_vs_atr >= 1.2:
        absorption_strength = "EXHAUSTED"
    elif reaction_vs_atr >= 0.8:
        absorption_strength = "STRONG_ABSORPTION"
    elif reaction_vs_atr >= 0.3:
        absorption_strength = "MODERATE_ABSORPTION"

    return {
        "reaction_pct": round(reaction_pct, 2) if reaction_pct is not None else None,
        "status": reaction_status,
        "absorption_strength": absorption_strength,
        "reaction_quality": reaction_quality,
        "atr_pct_reference": round(atr_pct_reference, 4) if atr_pct_reference else None,
        "reaction_vs_atr": reaction_vs_atr,
    }


def determine_novelty(news_text: str, metadata: str = "") -> str:
    """
    Keyword heuristic catching anticipated events vs novel surprises.
    """
    text = f"{news_text} {metadata}".lower()
    routine_keywords = ["in line with", "meets estimates", "as expected", "in-line"]
    expected_keywords = ["updates", "q1", "q2", "q3", "q4", "results", "expected", "guidance", "continuity", "earnings"]
    surprise_keywords = ["surprise", "sudden", "unexpected", "shock", "unforeseen", "breakout", "plunge", "unplanned", "beats", "misses", "unexpectedly"]

    has_routine = any(word in text for word in routine_keywords)
    has_expected = any(word in text for word in expected_keywords)
    has_surprise = any(word in text for word in surprise_keywords)

    if has_routine and not has_surprise:
        return "EXPECTED_ROUTINE"
    if has_expected and has_surprise:
        return "EXPECTED_SURPRISE"
    if has_expected and not has_surprise:
        return "EXPECTED_ROUTINE"
        
    return "TRUE_CATALYST"