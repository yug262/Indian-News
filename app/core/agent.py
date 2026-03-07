# agent.py
"""
Macro Trading Intelligence Agent (Gemini)
- Takes title + optional summary + published timestamp + source
- Pulls market data via tools
- Measures what's already priced since publish (reaction vs ATR)
- Adds event-context intelligence (continuation / escalation / fatigue)
- Outputs remaining impact from NOW
- Saves agent outputs into DB columns (impact_score, confidence, etc.)
"""

import os
import json
import time
import traceback
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.db import execute_query
from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.core.prompt import SYSTEM_PROMPT
from app.core.prompt import CLASSIFY_PROMPT
from app.core.schema import SCHEMA_TEMPLATE

from app.core.tools import (
    get_crypto_prices,
    get_forex_prices,
    get_global_markets,
    get_market_sentiment,
    search_recent_news,
    get_macro_context,
    get_economic_calendar,
    get_interest_rate_differentials,
    get_news_source_credibility,
    calculate_reaction,
    get_asset_atr,
    classify_reaction_status,
    detect_reaction_headline,
    get_similar_news_counts,
    get_repetition_context,
    get_novelty_label,
    compute_remaining_tradable_impact,
)

load_dotenv()

BASE_DELAY = 5
MAX_RETRIES = 3
MODEL_NAME = os.getenv("MODEL_NAME")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _log(msg: str):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode(), flush=True)


def _calculate_news_age(published_iso: str) -> tuple[str, str, float]:
    try:
        pub_dt = datetime.fromisoformat(published_iso.strip())
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        hours_old = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600.0
        minutes = int(hours_old * 60)

        if minutes < 60:
            human = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif hours_old < 24:
            h = int(hours_old)
            human = f"{h} hour{'s' if h != 1 else ''} ago"
        else:
            d = int(hours_old / 24)
            human = f"{d} day{'s' if d != 1 else ''} ago"

        if hours_old < 1:
            label = "Fresh"
        elif hours_old < 4:
            label = "Recent"
        elif hours_old < 12:
            label = "Stale"
        else:
            label = "Old"

        return label, human, hours_old
    except Exception:
        return "Fresh", "just now", 0.0


MAJOR_FOREX_PAIRS = [
    "EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF",
    "AUD/USD", "USD/CAD", "NZD/USD", "DXY",
]

TOP_CRYPTO = [
    "bitcoin", "ethereum", "solana", "ripple",
    "cardano", "dogecoin", "avalanche-2", "chainlink",
]

_HEADLINE_ASSET_MAP = {
    # Crypto
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "solana": "SOL-USD", "sol": "SOL-USD",
    "ripple": "XRP-USD", "xrp": "XRP-USD",
    "cardano": "ADA-USD", "ada": "ADA-USD",
    "dogecoin": "DOGE-USD", "doge": "DOGE-USD",
    # Macro
    "gold": "GC=F",
    "oil": "CL=F", "crude": "CL=F",
    "dollar": "DX-Y.NYB", "dxy": "DX-Y.NYB",
    "s&p": "^GSPC", "nasdaq": "^IXIC", "dow": "^DJI",
    "nikkei": "^N225", "dax": "^GDAXI",
}


def _detect_assets_from_title(title: str) -> list[str]:
    tl = (title or "").lower()
    found = []
    used = set()
    for k, sym in _HEADLINE_ASSET_MAP.items():
        if k in tl and sym not in used:
            found.append(sym)
            used.add(sym)
    return found[:5]


def fetch_all_market_data() -> dict:
    out = {}
    try:
        out["forex"] = get_forex_prices(MAJOR_FOREX_PAIRS)
    except Exception as e:
        _log(f"⚠️ forex: {e}")
        out["forex"] = {}

    try:
        out["crypto"] = get_crypto_prices(TOP_CRYPTO)
    except Exception as e:
        _log(f"⚠️ crypto: {e}")
        out["crypto"] = {}

    try:
        out["markets"] = get_global_markets()
    except Exception as e:
        _log(f"⚠️ markets: {e}")
        out["markets"] = {}

    try:
        out["sentiment"] = get_market_sentiment()
    except Exception as e:
        _log(f"⚠️ sentiment: {e}")
        out["sentiment"] = {}

    try:
        out["macro"] = get_macro_context()
    except Exception as e:
        _log(f"⚠️ macro: {e}")
        out["macro"] = {}

    return out


def _check_recent_movements(symbols: list[str], published_iso: str) -> dict:
    movements = {}
    for sym in symbols:
        try:
            reaction = calculate_reaction(sym, published_iso)
            atr = get_asset_atr(sym)

            if not reaction or "reaction_pct" not in reaction:
                continue

            reaction_pct = float(reaction["reaction_pct"])
            atr_pct = float(atr.get("atr_pct_reference") or 1.0)
            status = classify_reaction_status(reaction_pct, atr_pct)

            key = sym.replace("-USD", "").replace("=F", "").replace("^", "")
            movements[key] = {
                "symbol": sym,
                "reaction_pct": round(reaction_pct, 4),
                "news_price": reaction.get("news_price"),
                "current_price": reaction.get("current_price"),
                "atr_pct_reference": round(atr_pct, 4),
                "reaction_status": status,
            }
        except Exception:
            continue
    return movements


# ─────────────────────────────────────────────
# Event context helpers
# ─────────────────────────────────────────────

ESCALATION_KEYWORDS = [
    "nuclear",
    "oil supply",
    "shipping halt",
    "strait of hormuz",
    "hormuz",
    "sanctions",
    "capital controls",
    "bank collapse",
    "banking stress",
    "central bank action",
    "first strike",
    "new front",
    "new geography",
    "new country joins",
    "trade disruption",
    "pipeline attack",
    "refinery attack",
    "missile on oil",
    "blockade",
    "no oil shipments",
    "waterway closed",
    "airspace closed",
    "martial law",
    "default",
    "withdrawal halt",
    "exchange halt",
]

COMMENTARY_HINTS = [
    "says", "said", "commentary", "analysis", "opinion", "reminder",
    "preview", "outlook", "expects", "expect", "forecast", "consensus",
    "interview", "discusses", "speaks", "friendly reminder",
]


def classify_event_fatigue(similar_news_12h: int) -> str:
    """
    Convert repetition count to event-fatigue state.
    """
    if similar_news_12h <= 1:
        return "fresh"
    elif similar_news_12h <= 3:
        return "developing"
    elif similar_news_12h <= 6:
        return "ongoing"
    return "high"


def detect_escalation_keywords(title: str, summary: str = "") -> bool:
    """
    Detect whether the headline/summary introduces materially new market consequences.
    """
    text = f"{title or ''} {summary or ''}".lower()
    return any(keyword in text for keyword in ESCALATION_KEYWORDS)


def infer_event_state_hint(
    title: str,
    summary: str,
    similar_news_12h: int,
    event_fatigue: str,
    escalation_keywords_detected: bool,
    reaction_headline: bool,
) -> str:
    """
    Lightweight state hint passed to the model.
    """
    text = f"{title or ''} {summary or ''}".lower()

    if reaction_headline:
        return "COMMENTARY"

    if any(h in text for h in COMMENTARY_HINTS):
        return "COMMENTARY"

    if escalation_keywords_detected:
        return "ESCALATION"

    if similar_news_12h == 0:
        return "NEW_EVENT"

    if event_fatigue in ("ongoing", "high"):
        return "CONTINUATION"

    return "NEW_EVENT"


def classify_news_relevance(title: str, description: str = "") -> dict:
    """
    Lightweight Gemini call to classify a news article's category, impact_level, and reason
    for forex/crypto trading. Returns a dict with these fields.
    Falls back to a default 'none' impact dict on any error.
    """
    default_resp = {"category": "error", "impact_level": "none", "reason": "Classification failed or skipped"}
    if not os.getenv("GEMINI_API_KEY") or not client:
        return default_resp
    try:
        user_msg = f"Title: {title}"
        if description:
            user_msg += f"\nDescription: {description[:300]}"
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[user_msg],
            config=types.GenerateContentConfig(
                system_instruction=CLASSIFY_PROMPT,
                temperature=0.1,
                max_output_tokens=300,
            ),
        )
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            _log(f"[TOKEN USAGE - CLASSIFY] In: {response.usage_metadata.prompt_token_count} | Out: {response.usage_metadata.candidates_token_count} | Total: {response.usage_metadata.total_token_count}")
        text = (response.text or "").strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)

        result = default_resp.copy()
        if json_match:
            try:
                json_str = json_match.group(0)
                if json_str.count('{') > json_str.count('}'):
                    json_str += '}' * (json_str.count('{') - json_str.count('}'))
                data = json.loads(json_str)
                category = data.get("category", "unclassified")
                raw_impact = data.get("importance") or data.get("impact_level") or "none"
                impact_level = raw_impact.lower()
                reason = data.get("reason", "")
                result = {
                    "category": category,
                    "impact_level": impact_level,
                    "reason": reason,
                }
            except Exception:
                pass

        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/classification.log", "a", encoding="utf-8") as f:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] TITLE: {title}\n")
                f.write(f"[{timestamp}] RAW: {text}\n")
                f.write(f"[{timestamp}] FINAL: {result}\n")
                f.write("-" * 40 + "\n")
        except Exception:
            pass

        return result
    except Exception as e:
        print(f"[CLASSIFY] Error classifying '{title[:50]}...': {e}", flush=True)
        return default_resp


def classify_batch(items: list[tuple[str, str]]) -> list[dict]:
    """
    Classify a batch of (title, description) pairs in parallel.
    Returns list of relevance dicts in the same order.
    """
    if not items:
        return []

    default_resp = {"category": "error", "impact_level": "none", "reason": "Classification failed or skipped"}
    results = [default_resp] * len(items)

    def _classify(idx, title, desc):
        return idx, classify_news_relevance(title, desc)

    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as executor:
        futures = {
            executor.submit(_classify, i, title, desc): i
            for i, (title, desc) in enumerate(items)
        }
        for future in as_completed(futures):
            try:
                idx, label = future.result()
                results[idx] = label
            except Exception:
                pass
    return results


def analyze_news(title: str, published_iso: str, summary: str = "", source: str = "") -> dict | None:
    """
    Returns JSON matching schema template.
    """
    analysis_time = datetime.now(timezone.utc).isoformat()
    age_label, age_human, hours_old = _calculate_news_age(published_iso)

    # priced-in duplicate check using DB
    news_check = search_recent_news(title, hours_back=48)
    priced_in_by_history = bool(news_check.get("priced_in", False))

    for attempt in range(MAX_RETRIES):
        try:
            _log(f"[ATTEMPT {attempt+1}/{MAX_RETRIES}] {title[:80]}")

            market_data = fetch_all_market_data()

            # movement since publish (dynamic)
            symbols = _detect_assets_from_title(title)
            movements = _check_recent_movements(symbols, published_iso) if symbols else {}

            # choose a dominant movement (largest abs move)
            dominant = None
            for _, mv in movements.items():
                if dominant is None or abs(mv["reaction_pct"]) > abs(dominant["reaction_pct"]):
                    dominant = mv

            reaction_pct = dominant["reaction_pct"] if dominant else 0.0
            atr_pct_reference = dominant["atr_pct_reference"] if dominant else 0.0
            reaction_status = dominant["reaction_status"] if dominant else "normal_reaction"

            # reaction-headline override
            rh = detect_reaction_headline(title)
            reaction_headline = rh["reaction_headline"]
            headline_move_pct = rh["headline_move_pct"]
            has_new_catalyst = rh["has_new_catalyst"]

            if reaction_headline and headline_move_pct is not None and headline_move_pct >= 3 and not has_new_catalyst:
                reaction_status = "fully_priced"

            # source credibility
            source_cred = get_news_source_credibility(source)

            # extra data
            extra_data = {}
            tl = title.lower()
            if any(w in tl for w in ["rate", "cpi", "inflation", "nfp", "jobs", "gdp", "pmi", "fed", "ecb", "boj", "rbi"]):
                extra_data["economic_calendar"] = get_economic_calendar()
                extra_data["rate_differentials"] = get_interest_rate_differentials()

            # ✅ event context
            rep = get_repetition_context(title)
            similar_news_12h = rep["similar_news_12h"]
            similar_news_24h = rep["similar_news_24h"]
            theme_news_12h = rep["theme_news_12h"]
            theme_news_24h = rep["theme_news_24h"]
            theme = rep["theme"]
            fatigue_score = rep["fatigue_score"]
            repetition_level = rep["repetition_level"]

            event_fatigue = classify_event_fatigue(similar_news_12h)
            escalation_keywords_detected = detect_escalation_keywords(title, summary) or rep["has_escalation_words"]

            event_state_hint = infer_event_state_hint(
                title=title,
                summary=summary,
                similar_news_12h=similar_news_12h,
                event_fatigue=event_fatigue,
                escalation_keywords_detected=escalation_keywords_detected,
                reaction_headline=reaction_headline,
            )

            novelty_label = get_novelty_label(title)
            remaining_impact_context = compute_remaining_tradable_impact(
                base_event_impact=6,   # placeholder baseline for model guidance only
                published_at=datetime.fromisoformat(published_iso.replace("Z", "+00:00")),
                title=title,
            )

            _log(
                f"event_context => theme={theme}, sim12={similar_news_12h}, sim24={similar_news_24h}, "
                f"theme12={theme_news_12h}, theme24={theme_news_24h}, "
                f"fatigue_score={fatigue_score}, repetition={repetition_level}, "
                f"escalation={escalation_keywords_detected}, novelty={novelty_label}, "
                f"state={event_state_hint}"
            )

            schema_text = json.dumps(SCHEMA_TEMPLATE, indent=2)

            movement_text = "None"
            if movements:
                lines = []
                for name, mv in movements.items():
                    direction = "down" if mv["reaction_pct"] < 0 else "up"
                    lines.append(
                        f"{name}: {direction} {abs(mv['reaction_pct']):.2f}% since publish "
                        f"(ATR {mv['atr_pct_reference']:.2f}%, status {mv['reaction_status']})"
                    )
                movement_text = "\n".join(lines)

            prompt = f"""
                Return JSON matching this exact template (all keys must exist, unknown = "" or 0 or []):
                {schema_text}

                NEWS:
                - title: {title}
                - summary: {summary}
                - source: {source}
                - timestamp_utc: {published_iso}
                - analysis_timestamp_utc: {analysis_time}

                EVENT CONTEXT:
                - theme: {theme}
                - similar_news_last_12h: {similar_news_12h}
                - similar_news_last_24h: {similar_news_24h}
                - theme_news_last_12h: {theme_news_12h}
                - theme_news_last_24h: {theme_news_24h}
                - fatigue_score: {fatigue_score}
                - repetition_level: {repetition_level}
                - novelty_label: {novelty_label}
                - event_fatigue: {event_fatigue}
                - event_state_hint: {event_state_hint}
                - escalation_keywords_detected: {escalation_keywords_detected}

                DYNAMIC REACTION INPUTS:
                - reaction_pct: {reaction_pct}
                - atr_pct_reference: {atr_pct_reference}
                - reaction_status: {reaction_status}
                - already_priced_in_by_history: {priced_in_by_history}
                - db_duplicate_check: {json.dumps(news_check, default=str)}

                LIVE MARKET DATA (use these; do NOT fabricate):
                - forex: {json.dumps(market_data.get("forex", {}), default=str)}
                - crypto: {json.dumps(market_data.get("crypto", {}), default=str)}
                - global_markets: {json.dumps(market_data.get("markets", {}), default=str)}
                - sentiment: {json.dumps(market_data.get("sentiment", {}), default=str)}
                - macro: {json.dumps(market_data.get("macro", {}), default=str)}
                - source_credibility: {json.dumps(source_cred, default=str)}
                {f"- economic_calendar: {json.dumps(extra_data.get('economic_calendar', {}), default=str)}" if "economic_calendar" in extra_data else ""}
                {f"- rate_differentials: {json.dumps(extra_data.get('rate_differentials', {}), default=str)}" if "rate_differentials" in extra_data else ""}

                RECENT MOVEMENTS SINCE PUBLISH:
                {movement_text}

                REACTION HEADLINE OVERRIDE:
                - reaction_headline: {reaction_headline}
                - headline_move_pct: {headline_move_pct}
                - has_new_catalyst: {has_new_catalyst}

                RULE REMINDER:
                - impact_score must represent REMAINING impact from NOW onward.
                - If reaction_status=fully_priced -> cap impact <= 4 unless crisis.
                - If event_fatigue is high and no escalation -> treat as continuation, not a fresh shock.
                - If repetition_level is high and novelty_label = repetition_only -> strongly reduce impact.
                - If theme has appeared many times in the last 12h/24h, do not treat it as a fresh macro shock unless escalation exists.
                - If event_state_hint is COMMENTARY -> keep impact low unless policy/action exists.
                - If news age >12h -> cap impact <= 3.

                Return STRICT JSON only.
                """

            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )
            if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                _log(f"[TOKEN USAGE - ANALYZE] In: {resp.usage_metadata.prompt_token_count} | Out: {resp.usage_metadata.candidates_token_count} | Total: {resp.usage_metadata.total_token_count}")
            text = resp.text

            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if not m:
                    continue
                result = json.loads(m.group(0))

            # inject timing info
            result.setdefault("event_metadata", {})
            result["event_metadata"]["analysis_timestamp_utc"] = analysis_time

            # tag meta
            result["_meta"] = {
                "news_age_label": age_label,
                "news_age_human": age_human,
                "news_hours_old": round(hours_old, 2),
                "priced_in_by_history": priced_in_by_history,
                "reaction_status": reaction_status,
                "reaction_pct": reaction_pct,
                "atr_pct_reference": atr_pct_reference,
                "reaction_headline": reaction_headline,
                "headline_move_pct": headline_move_pct,
                "has_new_catalyst": has_new_catalyst,
                "similar_news_12h": similar_news_12h,
                "similar_news_24h": similar_news_24h,
                "theme_news_12h": theme_news_12h,
                "theme_news_24h": theme_news_24h,
                "theme": theme,
                "fatigue_score": fatigue_score,
                "repetition_level": repetition_level,
                "novelty_label": novelty_label,
                "event_fatigue": event_fatigue,
                "event_state_hint": event_state_hint,
                "escalation_keywords_detected": escalation_keywords_detected,
            }

            return result

        except Exception as e:
            _log(f"ERROR: {e}")
            traceback.print_exc()
            time.sleep(BASE_DELAY * (attempt + 1))

    return None


def save_analysis(news_id: int, analysis: dict):
    """
    Saves the agent's output into existing DB columns your UI reads.
    """
    core = analysis.get("core_impact_assessment", {})
    regime = analysis.get("market_regime_context", {})
    prob = analysis.get("probability_and_confidence", {})
    time_mod = analysis.get("time_modeling", {})
    directional = analysis.get("directional_bias", {})
    meta = analysis.get("_meta", {})

    forex_items = directional.get("forex", []) or []
    crypto_items = directional.get("crypto", []) or []

    query = """
        UPDATE news SET
            analyzed               = TRUE,
            analyzed_at            = NOW(),
            analysis_data          = %s,
            impact_score           = %s,
            impact_summary         = %s,
            affected_markets       = %s,
            impact_duration        = %s,
            market_mode            = %s,
            usd_bias               = %s,
            crypto_bias            = %s,
            execution_window       = %s,
            confidence             = %s,
            conviction_score       = %s,
            volatility_regime      = %s,
            dollar_liquidity_state = %s,
            news_age_label         = %s,
            news_age_human         = %s,
            news_priced_in         = %s
        WHERE id = %s
    """

    params = (
        json.dumps(analysis),
        int(core.get("primary_impact_score", 0) or 0),
        (analysis.get("executive_summary", "") or "")[:500],
        json.dumps(core.get("market_category_scores", {}) or {}),
        (time_mod.get("impact_duration", "") or "")[:100],
        (regime.get("dominant_market_regime", "") or "")[:50],
        (forex_items[0].get("direction", "") if forex_items else "")[:20],
        (crypto_items[0].get("direction", "") if crypto_items else "")[:20],
        (time_mod.get("reaction_speed", "") or "")[:50],
        str(prob.get("overall_confidence_score", ""))[:50],
        int(prob.get("direction_probability_pct", 0) or 0),
        (regime.get("volatility_expectation", "") or "")[:50],
        (regime.get("liquidity_condition_assumption", "") or "")[:50],
        (meta.get("news_age_label", "Fresh") or "")[:20],
        (meta.get("news_age_human", "") or "")[:50],
        bool(meta.get("priced_in_by_history", False) or (meta.get("reaction_status") == "fully_priced")),
        news_id,
    )

    execute_query(query, params)
    _log(f"[SAVE] news_id={news_id}")

    # Auto-create predictions from directional bias
    try:
        create_predictions(news_id, analysis)
    except Exception as pred_err:
        _log(f"[PRED] Failed to create predictions for news_id={news_id}: {pred_err}")


# ── Asset normalization for predictions ──────────────────────

_CRYPTO_SYMBOL_MAP = {
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "solana": "SOL-USD", "sol": "SOL-USD",
    "ripple": "XRP-USD", "xrp": "XRP-USD",
    "cardano": "ADA-USD", "ada": "ADA-USD",
    "dogecoin": "DOGE-USD", "doge": "DOGE-USD",
    "avalanche": "AVAX-USD", "avax": "AVAX-USD",
    "chainlink": "LINK-USD", "link": "LINK-USD",
    "polkadot": "DOT-USD", "dot": "DOT-USD",
    "litecoin": "LTC-USD", "ltc": "LTC-USD",
    "uniswap": "UNI-USD", "uni": "UNI-USD",
    "shiba inu": "SHIB-USD", "shib": "SHIB-USD",
    "polygon": "MATIC-USD", "matic": "MATIC-USD",
}

_FOREX_SYMBOL_MAP = {
    "eur/usd": "EURUSD=X", "eurusd": "EURUSD=X",
    "usd/jpy": "USDJPY=X", "usdjpy": "USDJPY=X",
    "gbp/usd": "GBPUSD=X", "gbpusd": "GBPUSD=X",
    "usd/chf": "USDCHF=X", "usdchf": "USDCHF=X",
    "aud/usd": "AUDUSD=X", "audusd": "AUDUSD=X",
    "usd/cad": "USDCAD=X", "usdcad": "USDCAD=X",
    "nzd/usd": "NZDUSD=X", "nzdusd": "NZDUSD=X",
    "dxy": "DX-Y.NYB", "dollar index": "DX-Y.NYB",
    "dollar": "DX-Y.NYB", "usd": "DX-Y.NYB",
    "myr/usd": "MYRUSD=X", "usd/myr": "USDMYR=X",
}

_EQUITIES_SYMBOL_MAP = {
    "gold": "GC=F", "xau": "GC=F",
    "oil": "CL=F", "crude": "CL=F", "wti": "CL=F",
    "silver": "SI=F", "xag": "SI=F",
    "s&p": "^GSPC", "s&p 500": "^GSPC", "s&p500": "^GSPC", "spx": "^GSPC",
    "nasdaq": "^IXIC", "qqq": "^IXIC",
    "dow": "^DJI", "dow jones": "^DJI",
    "nikkei": "^N225",
    "dax": "^GDAXI",
    "ftse": "^FTSE",
}

_DURATION_MAP = {
    "intraday": 60,
    "short-term": 360,
    "medium-term": 2880,
    "long-term": 10080,
    "hours": 60,
    "days": 1440,
    "weeks": 10080,
    "1 hour": 60,
    "2 hours": 120,
    "4 hours": 240,
    "6 hours": 360,
    "8 hours": 480,
    "12 hours": 720,
    "24h": 1440,
    "48h": 2880,
    "72h": 4320,
    "1 day": 1440,
    "2 days": 2880,
    "3 days": 4320,
    "1 week": 10080,
    "2 weeks": 20160,
    "1 month": 43200,
    "day": 1440,
    "week": 10080,
    "month": 43200,
    "hour": 60,
    "minute": 1,
}


def _normalize_asset_symbol(asset_name: str, asset_class: str) -> str | None:
    """Convert human-readable asset name to a yfinance-compatible symbol or dynamic identifier."""
    name = (asset_name or "").strip().lower()
    if not name:
        return None

    if name.endswith("-usd") or "=" in name or name.startswith("^"):
        return asset_name.upper()

    if asset_class == "crypto":
        return _CRYPTO_SYMBOL_MAP.get(name) or f"CRYPTO:{name}"
    elif asset_class == "forex":
        res = _FOREX_SYMBOL_MAP.get(name)
        if res:
            return res
        clean = name.replace("/", "").replace(" ", "").upper()
        if len(clean) == 6:
            return f"{clean}=X"
        return f"FOREX:{name}"
    elif asset_class == "global_equities":
        return _EQUITIES_SYMBOL_MAP.get(name) or name.upper()

    return (
        _CRYPTO_SYMBOL_MAP.get(name)
        or _FOREX_SYMBOL_MAP.get(name)
        or _EQUITIES_SYMBOL_MAP.get(name)
        or name.upper()
    )


def _parse_move_pct(raw: str | int | float) -> float:
    """Parse expected_move_pct from string like '0.5%' or '1-2%' to float."""
    if isinstance(raw, (int, float)):
        return abs(float(raw))
    s = str(raw).strip().replace("%", "")
    if "-" in s:
        parts = s.split("-")
        try:
            return abs(sum(float(p.strip()) for p in parts) / len(parts))
        except ValueError:
            pass
    try:
        return abs(float(s))
    except ValueError:
        return 0.5


def _parse_duration_minutes(label: str) -> int:
    """Map duration label to minutes with regex fallback."""
    key = (label or "").strip().lower()

    if key in _DURATION_MAP:
        return _DURATION_MAP[key]

    unit_to_min = {"minute": 1, "hour": 60, "day": 1440, "week": 10080, "month": 43200}

    m = re.search(r"([\d]+)\s*[-–to]+\s*([\d]+)\s*(minute|hour|day|week|month)", key)
    if m:
        low, high, unit = int(m.group(1)), int(m.group(2)), m.group(3)
        return int((low + high) / 2 * unit_to_min.get(unit, 60))

    m = re.search(r"([\d.]+)\s*(minute|hour|day|week|month)", key)
    if m:
        qty, unit = float(m.group(1)), m.group(2)
        return int(qty * unit_to_min.get(unit, 60))

    return 360


def create_predictions(news_id: int, analysis: dict):
    """
    Parse directional_bias from analysis and insert prediction rows.
    Called automatically after save_analysis.
    """
    from app.core.db import execute_query as _exec, fetch_one as _fetch_one
    from app.core.tools import _safe_last_close

    directional = analysis.get("directional_bias", {})
    if not directional:
        return

    time_mod = analysis.get("time_modeling", {})
    default_duration_label = time_mod.get("impact_duration", "Short-term") or "Short-term"

    news_row = _fetch_one("SELECT analyzed_at FROM news WHERE id = %s", (news_id,))
    now = (news_row["analyzed_at"] if news_row and news_row.get("analyzed_at")
           else datetime.now(timezone.utc))
    created = 0

    for asset_class in ("crypto", "forex", "global_equities"):
        items = directional.get(asset_class, []) or []
        for item in items:
            try:
                raw_asset = item.get("asset") or item.get("pair") or item.get("index") or ""
                direction = item.get("direction", "Neutral") or "Neutral"

                raw_move = item.get("expected_move_pct", "0.5%")
                predicted_move = _parse_move_pct(raw_move)
                if predicted_move == 0 and direction.lower() == "neutral":
                    continue

                symbol = _normalize_asset_symbol(raw_asset, asset_class)
                if not symbol:
                    _log(f"[PRED] Could not normalize asset '{raw_asset}' ({asset_class}), skipping")
                    continue

                duration_label = item.get("expected_duration") or default_duration_label
                duration_minutes = _parse_duration_minutes(duration_label)

                start_price = _safe_last_close(symbol)
                if start_price is None:
                    if asset_class == "crypto":
                        try:
                            cg_name = raw_asset.strip().lower()
                            prices = get_crypto_prices([cg_name])
                            if cg_name in prices:
                                start_price = float(prices[cg_name])
                        except Exception:
                            pass
                    if start_price is None:
                        _log(f"[PRED] No price for {symbol}, skipping")
                        continue

                if direction.lower() in ("positive", "bullish", "up"):
                    target_price = start_price * (1 + predicted_move / 100)
                elif direction.lower() in ("negative", "bearish", "down"):
                    target_price = start_price * (1 - predicted_move / 100)
                else:
                    target_price = start_price

                _exec(
                    """INSERT INTO predictions
                        (news_id, asset, asset_display_name, asset_class, direction,
                         predicted_move_pct, expected_duration_label, expected_duration_minutes,
                         start_time, start_price, target_price)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        news_id, symbol, raw_asset, asset_class, direction,
                        predicted_move, duration_label, duration_minutes,
                        now, start_price, round(target_price, 6),
                    ),
                )
                created += 1
                _log(
                    f"[PRED] Created: {symbol} {direction} {predicted_move}% "
                    f"({duration_label}) start={start_price}"
                )

            except Exception as e:
                _log(f"[PRED] Error creating prediction for {raw_asset}: {e}")
                continue

    _log(f"[PRED] Created {created} prediction(s) for news_id={news_id}")   