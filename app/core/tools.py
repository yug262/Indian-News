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

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import requests
import yfinance as yf
from bs4 import BeautifulSoup

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
    Repeated theme should not always reduce impact if there is escalation.
    """
    t = (title or "").lower()

    escalation_words = [
        "unexpectedly",
        "emergency",
        "cuts rates",
        "raises rates",
        "misses estimates",
        "beats estimates",
        "far above estimates",
        "far below estimates",
        "sanctions",
        "attack",
        "missile",
        "ceasefire",
        "default",
        "bankruptcy",
        "approval",
        "approved",
        "lawsuit",
        "etf approved",
        "intervention",
        "surprise",
        "record high",
        "record low",
        "collapses",
        "plunges",
        "explodes",
    ]

    return any(w in t for w in escalation_words)


def detect_reaction_headline(title: str) -> dict:
    """
    Detect headlines that describe an already-realized move.
    Example: 'Bitcoin surges 8%' or 'Stocks tumble 3%'.
    """
    t = (title or "").lower()

    move_verbs = [
        "surge", "soar", "jump", "rally", "climb", "rise", "gain", "advance",
        "drop", "tumble", "slump", "fall", "sink", "slide", "plunge", "selloff",
    ]

    has_move_verb = any(v in t for v in move_verbs)
    percents = re.findall(r"(\d+(?:\.\d+)?)\s*%", t)
    headline_move_pct = max([float(x) for x in percents], default=None)

    reaction_headline = bool(has_move_verb and headline_move_pct is not None)

    new_catalyst_words = [
        "rate hike", "rate cut", "raises rates", "cuts rates",
        "sanctions", "imposes", "strike", "attack", "missile",
        "approved", "approval", "etf", "ban", "lawsuit", "sec",
        "bankruptcy", "defaults", "bailout", "emergency"
    ]
    has_new_catalyst = any(w in t for w in new_catalyst_words)

    return {
        "reaction_headline": reaction_headline,
        "headline_move_pct": headline_move_pct,
        "has_new_catalyst": has_new_catalyst,
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


def get_asset_atr(symbol: str, period: int = 14) -> dict:
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

    resp = _http_get(
        f"{COINGECKO_URL}/simple/price",
        params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
    )
    if resp is None:
        return {}

    try:
        data = resp.json()
        return {k: float(v["usd"]) for k, v in data.items() if isinstance(v, dict) and "usd" in v}
    except Exception:
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

def search_recent_news(title: str, hours_back: int = 48, similarity_threshold: float = 0.88) -> dict:
    """
    Best-match duplicate / priced-in check.
    Returns priced_in=True if a very similar story existed and is old enough.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        rows = fetch_all(
            """
            SELECT title, published
            FROM news
            WHERE published >= %s
            ORDER BY published DESC
            LIMIT 300
            """,
            (since,),
        )

        best = {"score": 0.0, "title": None, "published": None}
        base = _normalize_event_title(title)

        for row in rows:
            other_title_raw = row.get("title") or ""
            other_title = _normalize_event_title(other_title_raw)
            score = SequenceMatcher(None, base, other_title).ratio()

            if score > best["score"]:
                best = {
                    "score": score,
                    "title": other_title_raw,
                    "published": row.get("published"),
                }

        if best["title"] is None:
            return {"priced_in": False, "match_score": 0.0}

        older_than_hrs = None
        if best["published"]:
            older_than_hrs = (datetime.now(timezone.utc) - _to_utc(best["published"])).total_seconds() / 3600.0

        priced_in = bool(
            best["score"] >= similarity_threshold
            and older_than_hrs is not None
            and older_than_hrs >= 6
        )

        return {
            "priced_in": priced_in,
            "match_score": round(best["score"], 3),
            "matched_title": best["title"],
            "matched_hours_ago": round(older_than_hrs, 2) if older_than_hrs is not None else None,
        }

    except Exception as e:
        return {"priced_in": False, "note": f"db_check_failed: {e}"}


def get_similar_news_counts(title: str, current_news_id: int | None = None) -> dict:
    """
    Count repetition in the last 24h.
    Returns:
    - similar_news_* : wording-level / near-duplicate repetition
    - theme_news_*   : same macro theme repetition
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
        base = _normalize_event_title(title)
        base_theme = detect_theme(title)

        similar_12h = 0
        similar_24h = 0
        theme_12h = 0
        theme_24h = 0

        for row in rows:
            row_id = row.get("id")
            other_title_raw = row.get("title", "")
            other_title = _normalize_event_title(other_title_raw)
            published = row.get("published")

            if current_news_id is not None and row_id == current_news_id:
                continue

            if not other_title or not published:
                continue

            published_utc = _to_utc(published)
            age_seconds = (now - published_utc).total_seconds()

            other_theme = detect_theme(other_title_raw)
            sim_score = SequenceMatcher(None, base, other_title).ratio()

            # Near duplicate
            if sim_score >= 0.72:
                similar_24h += 1
                if age_seconds <= 12 * 3600:
                    similar_12h += 1

            # Theme repetition
            if base_theme != "general" and other_theme == base_theme:
                theme_24h += 1
                if age_seconds <= 12 * 3600:
                    theme_12h += 1

        return {
            "similar_news_12h": similar_12h,
            "similar_news_24h": similar_24h,
            "theme_news_12h": theme_12h,
            "theme_news_24h": theme_24h,
            "theme": base_theme,
        }

    except Exception:
        return {
            "similar_news_12h": 0,
            "similar_news_24h": 0,
            "theme_news_12h": 0,
            "theme_news_24h": 0,
            "theme": "general",
        }


def compute_fatigue_score(similar_12h: int, similar_24h: int, theme_12h: int, theme_24h: int) -> int:
    """
    Weighted fatigue score from 0 to 10.
    """
    score = 0

    score += min(similar_12h * 2, 4)   # near duplicates matter most
    score += min(similar_24h, 2)
    score += min(theme_12h, 2)
    score += min(theme_24h // 2, 2)

    return min(score, 10)


def get_repetition_context(title: str, current_news_id: int | None = None) -> dict:
    counts = get_similar_news_counts(title, current_news_id=current_news_id)

    fatigue_score = compute_fatigue_score(
        counts["similar_news_12h"],
        counts["similar_news_24h"],
        counts["theme_news_12h"],
        counts["theme_news_24h"],
    )

    repetition_level = "low"
    if fatigue_score >= 7:
        repetition_level = "high"
    elif fatigue_score >= 4:
        repetition_level = "medium"

    return {
        "theme": counts["theme"],
        "similar_news_12h": counts["similar_news_12h"],
        "similar_news_24h": counts["similar_news_24h"],
        "theme_news_12h": counts["theme_news_12h"],
        "theme_news_24h": counts["theme_news_24h"],
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
    Simple novelty label:
    - true_new_event
    - update_to_existing_theme
    - repetition_only
    """
    rep = get_repetition_context(title, current_news_id=current_news_id)

    if rep["similar_news_24h"] == 0 and rep["theme_news_24h"] == 0:
        return "true_new_event"

    if rep["has_escalation_words"]:
        return "update_to_existing_theme"

    return "repetition_only"


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