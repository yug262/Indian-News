# app/ind/agent.py
from __future__ import annotations

import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.core.db import execute_query, fetch_one
from app.ind.prompt import INDIAN_SYSTEM_PROMPT, INDIAN_CLASSIFY_PROMPT
from app.ind.tools import get_indian_stock_price, run_indian_news_analysis
from app.ind.schema import SCHEMA_TEMPLATE


load_dotenv()

BASE_DELAY = 5
MAX_RETRIES = 3
MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# =========================================================
# LOGGING / SMALL HELPERS
# =========================================================

def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode(), flush=True)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_iso_utc(ts: str) -> datetime:
    ts = (ts or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _calculate_news_age(published_iso: str) -> tuple[str, str, float]:
    """
    Returns:
        age_label: Fresh / Recent / Old
        age_human: e.g. '2.3h'
        hours_old: float
    """
    try:
        pub_dt = _parse_iso_utc(published_iso)
        hours_old = max(0.0, (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600)

        if hours_old < 6:
            age_label = "Fresh"
        elif hours_old < 24:
            age_label = "Recent"
        else:
            age_label = "Old"

        if hours_old < 1:
            age_human = f"{int(hours_old * 60)}m"
        else:
            age_human = f"{round(hours_old, 1)}h"

        return age_label, age_human, hours_old
    except Exception:
        return "Fresh", "", 0.0


def _safe_json_loads(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty JSON response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return valid JSON")
        return json.loads(match.group(0))


# =========================================================
# CLASSIFICATION LAYER
# =========================================================

def classify_indian_news(title: str, summary: str = "", source: str = "") -> str:
    """
    Lightweight arrival-time classifier.
    Returns one label string.
    """
    if not client or not MODEL_NAME:
        _log("[INDIA CLASSIFY] Missing MODEL_NAME or GEMINI_API_KEY")
        return "unknown"

    prompt = f"""
{INDIAN_CLASSIFY_PROMPT}

Classify this Indian-market news item into one concise label.

Return ONLY the label text, no JSON.

Title: {title}
Source: {source}
Summary: {summary}
""".strip()

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=INDIAN_SYSTEM_PROMPT,
                    temperature=0.0,
                ),
            )
            label = (resp.text or "").strip()
            if not label:
                raise ValueError("Empty classifier response")
            return label
        except Exception as e:
            _log(f"[INDIA CLASSIFY ERROR] {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(BASE_DELAY * (attempt + 1))

    return "unknown"


def classify_indian_news_batch(items: list[tuple[str, str]], max_workers: int = 8) -> dict[int, str]:
    """
    items: [(title, summary), ...]
    returns: {index: label}
    """
    results: dict[int, str] = {}

    def _worker(idx: int, title: str, summary: str) -> tuple[int, str]:
        return idx, classify_indian_news(title=title, summary=summary)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_worker, idx, title, summary): idx
            for idx, (title, summary) in enumerate(items)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                i, label = future.result()
                results[i] = label
            except Exception:
                results[idx] = "unknown"

    return results


def enforce_hard_rules(data: dict) -> dict:
    event = data.get("event", {})
    title = (event.get("title") or "").lower()
    event_type = event.get("event_type")

    stock_impacts = data.get("stock_impacts", [])

    for s in stock_impacts:
        company = (s.get("company_name") or "").lower()

        # 🚨 HARD RULE: earnings main subject → direct
        if event_type == "earnings":
            if company and company in title:
                s["role"] = "direct"

        # fallback if company_name empty but symbol exists
        symbol = (s.get("symbol") or "").lower()
        if event_type == "earnings" and symbol:
            if symbol.lower() in title:
                s["role"] = "direct"

    return data


# =========================================================
# ANALYSIS LAYER
# =========================================================

def _call_indian_llm_compact(context: dict) -> dict:
    """
    LLM call for compact Indian schema.

    Architecture:
        tools → advisory context → LLM dynamic reasoning → compact JSON output

    The INDIAN_SYSTEM_PROMPT is set as system_instruction (full dynamic framework).
    The user prompt provides the event-specific advisory context plus the strict
    schema — ensuring the model reasons dynamically rather than using tool hints
    as hard decision rules.

    Key design principles reinforced in the prompt:
    - Context is SUPPORTIVE, not authoritative
    - Event type / family are output labels, not decision shortcuts
    - Scope / role must be derived from directness, materiality, surprise, breadth
    - Confidence never exceeds 85
    - impact_score is 0-10; confidence is 0-100
    """
    if not client or not MODEL_NAME:
        raise ValueError("Missing MODEL_NAME or GEMINI_API_KEY")

    # Build LLM-facing context: strip _internal metadata (post-processing only)
    llm_context = {k: v for k, v in context.items() if k != "_internal"}

    schema_text = json.dumps(SCHEMA_TEMPLATE, ensure_ascii=False, indent=2)

    compact_prompt = f"""
You are an Indian equities market intelligence agent.

Decide if this news creates a real, tradable impact.

==================================================
CORE ANALYSIS
==================================================

Always evaluate:
- directness (economic linkage)
- materiality (revenue/margin/cost/demand/regulation/valuation)
- surprise (expected vs unexpected)
- breadth (single / peer / sector / market)
- evidence quality (strong / medium / weak)

Weak signal → weak output  
Unclear → say "unclear"  
No validated mapping → no stock output  

==================================================
STOCK MAPPING (STRICT)
==================================================

- Use ONLY validated_mappings.company_matches
- Ignore weak_hints for stock selection
- No clear company → stock_impacts must be EMPTY
- Incidental mention → role = "peer"
- No Indian listed linkage → EMPTY

Max 5 stocks (usually 0–2)

==================================================
SCOPE
==================================================

single_stock → one main company  
peer_group → multiple similar companies  
sector → industry-level impact  
broad_market → multi-sector impact  

Do NOT assign scope from keywords alone.

==================================================
ROLE
==================================================

direct → direct economic impact  
indirect → second-order / macro  
peer → sentiment only  

Hard rule:
If earnings + main company → role = "direct"

==================================================
IMPACT SCORE (0–10)

0–2 → noise  
3–4 → mild  
5–6 → moderate  
7–8 → strong  
9–10 → major shock  

Rules:
- expected event → max 5
- no financial impact → max 6
- no exaggeration

==================================================
MOVE ESTIMATION

Allowed:
0-1%, 1-3%, 3-5%, 5-8%, 8%+, unclear

Weak → 0-1%  
Moderate → 1-3%  
Strong → 3-5%+  

If bias unclear → move = unclear  

==================================================
CONFIDENCE (0–100, max 85)

70–85 → strong  
50–69 → medium  
<50 → weak  

Reduce if:
- weak evidence
- unclear mapping
- assumptions required

==================================================
OUTPUT RULES

- Return ONLY JSON
- Follow schema EXACTLY
- No extra keys
- No hallucination
- No forced stocks
- Use "unclear" when needed

==================================================
OUTPUT SCHEMA

{schema_text}

==================================================
TOOL CONTEXT

{json.dumps(llm_context, ensure_ascii=False, indent=2)}
""".strip()

    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=compact_prompt,
        config=types.GenerateContentConfig(
            system_instruction=INDIAN_SYSTEM_PROMPT,
            temperature=0.25,
            response_mime_type="application/json",
        ),
    )

    result = _safe_json_loads(resp.text or "")
    result = enforce_hard_rules(result)

    return result



def analyze_indian_news(
    title: str,
    published_iso: str,
    summary: str = "",
    source: str = "",
    current_news_id: int | None = None,
) -> dict | None:
    """
    Compact-schema Indian analysis entrypoint.
    """
    if not client or not MODEL_NAME:
        _log("[INDIA ANALYZE] Missing MODEL_NAME or GEMINI_API_KEY")
        return None

    for attempt in range(MAX_RETRIES):
        try:
            _log(f"[INDIA ATTEMPT {attempt + 1}/{MAX_RETRIES}] {title[:120]}")

            result = run_indian_news_analysis(
                llm_callable=_call_indian_llm_compact,
                title=title,
                summary=summary,
                published_iso=published_iso,
                source=source,
                current_news_id=current_news_id,
            )

            result["_meta"] = {
                "analysis_timestamp_utc": datetime.now(timezone.utc).isoformat()
            }
            return result

        except Exception as e:
            _log(f"[INDIA ERROR] {e}")
            traceback.print_exc()
            if attempt < MAX_RETRIES - 1:
                time.sleep(BASE_DELAY * (attempt + 1))
    return None


# =========================================================
# SAVE FLOW
# =========================================================

def save_indian_analysis(news_id: int, analysis: dict) -> None:
    """
    Save compact Indian agent output.
    """
    event = analysis.get("event", {}) or {}
    analysis_block = analysis.get("analysis", {}) or {}
    scenario = analysis.get("scenario", {}) or {}

    age_label, age_human, hours_old = _calculate_news_age(event.get("timestamp_utc", ""))
    priced_in = hours_old >= 24

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
            execution_window       = %s,
            confidence             = %s,
            conviction_score       = %s,
            volatility_regime      = %s,
            dollar_liquidity_state = %s,
            news_age_label         = %s,
            news_age_human         = %s,
            news_priced_in         = %s,
            suggestions_data       = %s,
            suggestions_status     = %s,
            suggestions_summary    = %s
        WHERE id = %s
    """

    params = (
        json.dumps(analysis),
        int(analysis_block.get("impact_score", 0) or 0),
        (analysis.get("executive_summary", "") or "")[:500],
        json.dumps({
            "stocks": analysis.get("affected_entities", {}).get("stocks", []),
            "sectors": analysis.get("affected_entities", {}).get("sectors", []),
        }),
        (analysis_block.get("horizon", "") or "")[:100],
        (event.get("event_type", "") or "")[:50],
        (analysis_block.get("horizon", "") or "")[:50],
        int(analysis_block.get("confidence", 0) or 0),
        int(round((analysis_block.get("confidence", 0) or 0) / 10)),  # legacy 0-10 column
        (analysis_block.get("surprise", "") or "")[:50],
        (event.get("status", "") or "")[:50],
        age_label[:20],
        age_human[:50],
        bool(priced_in),
        json.dumps({"second_order_insights": scenario.get("second_order_insights", [])}),
        "compact_agent",
        (scenario.get("invalidation_trigger", "") or "")[:500],
        news_id,
    )

    execute_query(query, params)
    _log(f"[INDIA SAVE] news_id={news_id}")

    try:
        create_indian_predictions_compact(news_id, analysis)
    except Exception as pred_err:
        _log(f"[INDIA PRED] Failed for news_id={news_id}: {pred_err}")

    try:
        create_indian_watchlists_compact(news_id, analysis)
    except Exception as sug_err:
        _log(f"[INDIA SUG] Failed for news_id={news_id}: {sug_err}")


# =========================================================
# PREDICTIONS / WATCHLISTS
# =========================================================

def _safe_price_for_symbol(symbol: str, asset_type: str = "stock") -> float | None:
    try:
        if not symbol:
            return None

        if asset_type == "stock":
            raw = symbol.replace(".NS", "").replace(".BO", "")
            data = get_indian_stock_price(raw)
            return _safe_float(data.get("price")) if data else None

        return _safe_float(get_indian_stock_price(symbol).get("price"))
    except Exception:
        return None


_DURATION_MAP = {
    "intraday": 60,
    "short_term": 1440,
    "medium_term": 10080,
    "long_term": 43200,
    "1 day": 1440,
    "2 days": 2880,
    "3 days": 4320,
    "1 week": 10080,
    "2 weeks": 20160,
    "1 month": 43200,
}


def _parse_move_band_to_pct(raw: str | int | float) -> float:
    """
    Convert move band into one representative midpoint pct.
    """
    if isinstance(raw, (int, float)):
        return abs(float(raw))

    s = str(raw or "").strip().lower().replace("%", "")
    band_map = {
        "0-1": 0.5,
        "1-3": 2.0,
        "3-5": 4.0,
        "5-8": 6.5,
        "8+": 8.0,
        "unclear": 0.0,
    }

    if s in band_map:
        return band_map[s]

    if s.endswith("+"):
        try:
            return abs(float(s[:-1]))
        except Exception:
            return 8.0

    if "-" in s:
        try:
            parts = [float(p.strip()) for p in s.split("-") if p.strip()]
            if parts:
                return sum(parts) / len(parts)
        except Exception:
            pass

    try:
        return abs(float(s))
    except Exception:
        return 0.0


def _parse_compact_duration_minutes(label: str) -> int:
    return _DURATION_MAP.get((label or "").strip().lower(), 1440)


def _normalize_prediction_direction(direction: str) -> str:
    d = (direction or "").strip().lower()
    if d in {"bullish", "positive", "up"}:
        return "bullish"
    if d in {"bearish", "negative", "down"}:
        return "bearish"
    if d in {"mixed", "neutral", "unclear"}:
        return d
    return "unclear"


def _pick_prediction_move(item: dict, horizon: str) -> tuple[str, float]:
    """
    Prefer short_term for predictions; fallback to intraday.
    """
    expected_move = item.get("expected_move", {}) or {}
    if horizon == "intraday":
        label = expected_move.get("intraday", "unclear")
    else:
        label = expected_move.get("short_term", "unclear")
        if not label or label == "unclear":
            label = expected_move.get("intraday", "unclear")
    return label, _parse_move_band_to_pct(label)


def create_indian_predictions_compact(news_id: int, analysis: dict) -> None:
    """
    Create rows in predictions table from compact schema:
    - analysis.stock_impacts[]
    - analysis.analysis.horizon
    """
    stock_impacts = analysis.get("stock_impacts", []) or []
    analysis_block = analysis.get("analysis", {}) or {}
    horizon = analysis_block.get("horizon", "short_term") or "short_term"

    if not stock_impacts:
        _log(f"[INDIA PRED] No stock_impacts for news_id={news_id}")
        return

    news_row = fetch_one("SELECT analyzed_at FROM news WHERE id = %s", (news_id,))
    now_dt = news_row["analyzed_at"] if news_row and news_row.get("analyzed_at") else datetime.now(timezone.utc)

    try:
        execute_query("DELETE FROM predictions WHERE news_id = %s", (news_id,))
    except Exception:
        pass

    created = 0

    for item in stock_impacts[:5]:
        try:
            symbol = (item.get("symbol") or "").strip().upper()
            company_name = (item.get("company_name") or symbol).strip()
            direction = _normalize_prediction_direction(item.get("bias", "unclear"))
            confidence = int(item.get("confidence", 0) or 0)

            if not symbol or direction in {"neutral", "unclear"} or confidence < 40:
                continue

            duration_label = horizon
            duration_minutes = _parse_compact_duration_minutes(duration_label)

            _, predicted_move = _pick_prediction_move(item, horizon)
            if predicted_move <= 0:
                continue

            # Conservative clipping on weaker confidence
            if confidence < 55:
                predicted_move = min(predicted_move, 2.0)
            elif confidence < 70:
                predicted_move = min(predicted_move, 4.0)

            start_price = _safe_price_for_symbol(symbol)
            if not start_price:
                continue

            if direction == "bullish":
                target_price = start_price * (1 + predicted_move / 100)
            elif direction == "bearish":
                target_price = start_price * (1 - predicted_move / 100)
            else:
                target_price = start_price

            execute_query(
                """
                INSERT INTO predictions
                    (news_id, asset, asset_display_name, asset_class, direction,
                     predicted_move_pct, expected_duration_label, expected_duration_minutes,
                     start_time, start_price, target_price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    news_id,
                    symbol,
                    company_name,
                    "stock",
                    direction,
                    round(predicted_move, 4),
                    duration_label,
                    duration_minutes,
                    now_dt,
                    start_price,
                    round(target_price, 6),
                ),
            )
            created += 1

        except Exception as e:
            _log(f"[INDIA PRED] Error creating compact prediction for {item.get('symbol', '')}: {e}")
            continue

    _log(f"[INDIA PRED] Created {created} compact prediction(s) for news_id={news_id}")


def create_indian_watchlists_compact(news_id: int, analysis: dict) -> None:
    """
    Create rows in suggestions table from compact schema.
    This is watchlist-style output mapped onto your legacy suggestions table.
    """
    stock_impacts = analysis.get("stock_impacts", []) or []
    analysis_block = analysis.get("analysis", {}) or {}
    scenario = analysis.get("scenario", {}) or {}
    horizon = (analysis_block.get("horizon", "") or "").strip()

    if not stock_impacts:
        _log(f"[INDIA SUG] No stock_impacts for news_id={news_id}")
        return

    try:
        execute_query("DELETE FROM suggestions WHERE news_id = %s", (news_id,))
    except Exception:
        pass

    created = 0

    for item in stock_impacts[:5]:
        try:
            asset = (item.get("symbol") or "").strip().upper()
            if not asset:
                continue

            direction = _normalize_prediction_direction(item.get("bias", "unclear"))
            confidence = int(item.get("confidence", 0) or 0)
            role = (item.get("role") or "").strip().lower()
            why = (item.get("why") or "").strip()
            risk = (item.get("risk") or "").strip()
            invalidation = (item.get("invalidation") or "").strip()
            move_block = item.get("expected_move", {}) or {}

            intraday_label = move_block.get("intraday", "unclear")
            short_term_label = move_block.get("short_term", "unclear")
            expected_move_label = short_term_label if short_term_label != "unclear" else intraday_label

            if direction == "bullish" and confidence >= 70 and role in {"direct", "indirect"}:
                suggestion_type = "buy"   # UI can render as bullish_watchlist
            elif direction == "bearish" and confidence >= 70 and role in {"direct", "indirect"}:
                suggestion_type = "sell"  # UI can render as bearish_watchlist
            elif direction in {"bullish", "bearish", "mixed"} and confidence >= 40:
                suggestion_type = "watch"
            else:
                suggestion_type = "avoid"

            reasoning_parts = [p for p in [why, risk] if p]
            reasoning = " ".join(reasoning_parts).strip()
            if not reasoning:
                reasoning = "Signal exists but conviction is limited from the available headline summary."

            market_logic_text = (
                f"Move band: {expected_move_label}. "
                f"Role: {role or 'unknown'}. "
                f"Horizon: {horizon or 'short_term'}."
            )

            execute_query(
                """
                INSERT INTO suggestions
                    (news_id, suggestion_type, asset, direction, reasoning, market_logic,
                     time_window, invalidation, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    news_id,
                    suggestion_type,
                    asset,
                    direction if direction in {"bullish", "bearish", "mixed", "neutral"} else "",
                    reasoning[:1000],
                    market_logic_text[:1000],
                    (horizon or "short_term")[:100],
                    (invalidation or scenario.get("invalidation_trigger", ""))[:1000],
                    confidence
                ),
            )
            created += 1

        except Exception as e:
            _log(f"[INDIA SUG] Error inserting compact watchlist for {item.get('symbol', '')}: {e}")
            continue

    _log(f"[INDIA SUG] Created {created} compact suggestion/watchlist row(s) for news_id={news_id}")