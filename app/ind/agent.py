# app/ind/agent.py
from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.core.db import execute_query, fetch_one
from app.ind.prompt import INDIAN_SYSTEM_PROMPT, build_compact_prompt
from app.ind.schema import SCHEMA_TEMPLATE
from app.ind.tools import build_analysis_context


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
# RULES AND ENFORCEMENT
# =========================================================

def normalize_to_schema(data: dict) -> dict:
    valid_bias = {"bullish", "bearish", "mixed", "neutral", "unclear"}
    valid_surprise = {"low", "medium", "high", "unknown"}
    valid_strength = {"low", "medium", "high"}

    # -------------------------
    # core_view
    # -------------------------
    core_view = data.get("core_view", {}) or {}

    surprise = str(core_view.get("surprise_level", "unknown")).strip().lower()
    surprise_map = {
        "positive": "high",
        "negative": "high",
        "significant": "high",
        "moderate": "medium",
        "expected": "low",
        "minimal": "low",
    }
    if surprise not in valid_surprise:
        core_view["surprise_level"] = surprise_map.get(surprise, "unknown")

    bias = str(core_view.get("market_bias", "unclear")).strip().lower()
    if bias not in valid_bias:
        core_view["market_bias"] = "unclear"

    core_view["impact_score"] = int(max(0, min(10, int(core_view.get("impact_score", 0) or 0))))
    core_view["overall_confidence"] = int(max(0, min(100, int(core_view.get("overall_confidence", 0) or 0))))
    core_view["overall_confidence"] = min(core_view["overall_confidence"], 85)
    data["core_view"] = core_view

    # -------------------------
    # signal_bucket
    # -------------------------
    valid_buckets = {"DIRECT", "AMBIGUOUS", "WEAK_PROXY", "NOISE"}

    bucket = str(data.get("signal_bucket", "") or "").strip().upper()
    if bucket not in valid_buckets:
        data["signal_bucket"] = ""
    else:
        data["signal_bucket"] = bucket

    # -------------------------
    # stock_impacts
    # -------------------------
    cleaned_stock_impacts = []

    for item in data.get("stock_impacts", []) or []:
        item = dict(item)

        item["symbol"] = str(item.get("symbol", "") or "").strip().upper()
        item["company_name"] = str(item.get("company_name", "") or "").strip()
        item["role"] = str(item.get("role", "direct") or "direct").strip().lower()
        valid_roles = {"direct", "indirect", "peer", "beneficiary", "risk"}
        if item["role"] not in valid_roles:
            item["role"] = "direct"

        bias = str(item.get("bias", "unclear") or "unclear").strip().lower()
        if bias == "positive":
            bias = "bullish"
        elif bias == "negative":
            bias = "bearish"
        if bias not in valid_bias:
            bias = "unclear"
        item["bias"] = bias

        exp = item.get("expected_move", {}) or {}
        if isinstance(exp, str):
            exp = {
                "intraday": exp,
                "short_term": "unclear",
                "medium_term": "unclear",
            }

        item["expected_move"] = {
            "intraday": exp.get("intraday", "unclear"),
            "short_term": exp.get("short_term", "unclear"),
            "medium_term": exp.get("medium_term", "unclear"),
        }

        item["why"] = str(item.get("why", "") or "").strip()
        item["risk"] = str(item.get("risk", "") or "").strip()
        item["invalidation"] = str(item.get("invalidation", "") or "").strip()
        item["confidence"] = int(max(0, min(100, int(item.get("confidence", 0) or 0))))

        title_val = ((data.get("event", {}) or {}).get("title", "") or "").lower()
        company_name_val = item["company_name"].lower()

        company_is_headline = (
            len(item["company_name"].split()) > 6
            or (company_name_val and company_name_val == title_val)
        )

        # only hard reject on truly bad mapping
        if not item["company_name"] or company_is_headline:
            continue

        if not item["symbol"]:
            continue

        if not item["why"] and item["confidence"] < 50:
            continue

        cleaned_stock_impacts.append(item)

    data["stock_impacts"] = cleaned_stock_impacts

    # -------------------------
    # sector_impacts
    # -------------------------
    cleaned_sector_impacts = []
    for item in data.get("sector_impacts", []) or []:
        item = dict(item)

        bias = str(item.get("bias", item.get("impact", "unclear"))).strip().lower()
        if bias == "positive":
            bias = "bullish"
        elif bias == "negative":
            bias = "bearish"
        elif bias not in valid_bias:
            bias = "unclear"

        strength = str(item.get("strength", "medium")).strip().lower()
        if strength not in valid_strength:
            # crude fallback from impact_score
            score = int(item.get("impact_score", 0) or 0)
            if score >= 7:
                strength = "high"
            elif score >= 4:
                strength = "medium"
            else:
                strength = "low"

        sector_name = str(item.get("sector", "") or "").strip()
        why_text = str(item.get("why", item.get("reasoning", "")) or "").strip()
        confidence_val = int(max(0, min(100, int(item.get("confidence", 50) or 50))))

        if not sector_name:
            continue

        if not why_text and confidence_val < 50:
            continue

        cleaned_sector_impacts.append({
            "sector": sector_name,
            "bias": bias,
            "strength": strength,
            "time_horizon": item.get("time_horizon", data.get("core_view", {}).get("primary_horizon", "short_term")),
            "why": why_text,
            "confidence": confidence_val,
        })

    data["sector_impacts"] = cleaned_sector_impacts

    # -------------------------
    # evidence
    # -------------------------
    cleaned_evidence = []
    for item in data.get("evidence", []) or []:
        item = dict(item)

        strength_val = str(item.get("strength", "medium") or "medium").strip().lower()
        if strength_val not in valid_strength:
            strength_val = "medium"

        evidence_type = str(item.get("type", "inference") or "inference").strip().lower()
        valid_evidence_types = {
            "confirmed_fact",
            "management_commentary",
            "historical_pattern",
            "inference",
            "market_structure",
        }
        if evidence_type not in valid_evidence_types:
            evidence_type = "inference"

        cleaned_evidence.append({
            "type": evidence_type,
            "detail": item.get("detail", item.get("description", "")),
            "strength": strength_val,
            "confidence": int(max(0, min(100, int(item.get("confidence", 50) or 50)))),
        })

    data["evidence"] = cleaned_evidence

    # -------------------------
    # tradeability
    # -------------------------
    tradeability = data.get("tradeability", {}) or {}
    classification = str(tradeability.get("classification", "") or "").strip().lower()
    valid_tradeability = {"actionable_now", "wait_for_confirmation", "no_edge"}

    if classification not in valid_tradeability:
        classification = "no_edge"

    action_triggers = tradeability.get("action_triggers", []) or []
    if not isinstance(action_triggers, list):
        action_triggers = []

    data["tradeability"] = {
        "classification": classification,
        "reasoning": str(tradeability.get("reasoning", "") or "").strip(),
        "action_triggers": [str(x).strip() for x in action_triggers if str(x).strip()]
    }

    # -------------------------
    # impact_triggers
    # -------------------------
    impact_triggers = data.get("impact_triggers", {}) or {}

    def _clean_trigger_items(items):
        cleaned = []
        for item in items or []:
            item = dict(item)
            trigger = str(item.get("trigger", "") or "").strip()
            why = str(item.get("why_it_kills_the_impact", item.get("why_it_amplifies_the_impact", "")) or "").strip()
            effect = str(item.get("resulting_market_effect", "") or "").strip()
            time_sensitivity = str(item.get("time_sensitivity", "") or "").strip().lower()
            valid_time_sensitivity = {"immediate", "intraday", "short_term", "medium_term", "long_term", "high", "medium", "low"}
            if time_sensitivity not in valid_time_sensitivity:
                time_sensitivity = "short_term"
            confidence = int(max(0, min(100, int(item.get("confidence", 0) or 0))))

            if not trigger:
                continue
            if not why and confidence < 50:
                continue

            cleaned.append({
                "trigger": trigger,
                "why_it_kills_the_impact": str(item.get("why_it_kills_the_impact", "") or "").strip(),
                "why_it_amplifies_the_impact": str(item.get("why_it_amplifies_the_impact", "") or "").strip(),
                "resulting_market_effect": effect,
                "time_sensitivity": time_sensitivity,
                "confidence": confidence,
            })
        return cleaned

    data["impact_triggers"] = {
        "impact_killers": _clean_trigger_items(impact_triggers.get("impact_killers", [])),
        "impact_amplifiers": _clean_trigger_items(impact_triggers.get("impact_amplifiers", [])),
    }

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
    compact_prompt = build_compact_prompt(llm_context, str(SCHEMA_TEMPLATE))

    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=compact_prompt,
        config=types.GenerateContentConfig(
            system_instruction=INDIAN_SYSTEM_PROMPT,
            temperature=0.25,
            response_mime_type="application/json",
        ),
    )

    usage = resp.usage_metadata
    if usage:
        _log(f"   [TOKENS] In: {usage.prompt_token_count} | Out: {usage.candidates_token_count} | Total: {usage.total_token_count}")

    result = _safe_json_loads(resp.text or "")
    result = normalize_to_schema(result)

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

            # 1. Build deterministic context
            
            context = build_analysis_context(
                title=title,
                summary=summary,
                published_iso=published_iso,
                source=source,
            )

            # 2. Call LLM (which includes normalization)
            result = _call_indian_llm_compact(context)

            result["_meta"] = {
                "analysis_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "orchestrator": "compact_v2"
            }

            event = result.get("event", {}) or {}
            event_type = str(event.get("event_type", "") or "").lower()

            if "price_action" in event_type:
                event["event_type"] = "other"

            result["event"] = event

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
    Save consolidated Indian agent output.
    All rich data is stored in analysis_data (v4 schema).
    Filtering columns (score, bias, bucket) are kept separate.
    """
    event = analysis.get("event", {}) or {}
    core_view = analysis.get("core_view", {}) or {}
    stock_impacts = analysis.get("stock_impacts", [])
    primary_symbol = stock_impacts[0].get("symbol", "")[:50] if stock_impacts else None

    query = """
        UPDATE indian_news SET
            analyzed          = TRUE,
            analyzed_at       = NOW(),
            analysis_data     = %s,
            impact_score      = %s,
            market_bias       = %s,
            signal_bucket     = %s,
            news_category     = %s,
            news_relevance    = %s,
            primary_symbol    = %s,
            executive_summary = %s
        WHERE id = %s
    """

    # Extract core metrics for indexing/filtering
    impact_score_val = int(core_view.get("impact_score", 0) or 0)
    market_bias_val = (core_view.get("market_bias") or "neutral").lower()
    bucket_val = (analysis.get("signal_bucket") or "unclassified").upper()
    
    params = (
        json.dumps(analysis),
        impact_score_val,
        market_bias_val[:20],
        bucket_val[:20],
        (event.get("event_type", "general"))[:100],
        "High" if impact_score_val >= 6 else "Medium" if impact_score_val >= 3 else "Low",
        primary_symbol,
        (analysis.get("executive_summary", "") or "")[:2000],
        news_id,
    )

    execute_query(query, params)
    _log(f"[INDIA SAVE] news_id={news_id} (Consolidated)")

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
    Prefefor predictions; fallback to intraday.
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
    """
    stock_impacts = analysis.get("stock_impacts", []) or []
    core_view = analysis.get("core_view", {}) or {}
    horizon = core_view.get("primary_horizon", "short_term") or "short_term"

    tradeability = analysis.get("tradeability", {}) or {}
    if tradeability.get("classification") != "actionable_now":
        _log(f"[INDIA PRED] Skipping prediction creation for news_id={news_id} because tradeability is not actionable_now")
        return

    if not stock_impacts:
        _log(f"[INDIA PRED] No stock_impacts for news_id={news_id}")
        return

    news_row = fetch_one("SELECT analyzed_at FROM indian_news WHERE id = %s", (news_id,))
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
    core_view = analysis.get("core_view", {}) or {}
    horizon = str(core_view.get("primary_horizon", "short_term") or "short_term").strip()

    tradeability = analysis.get("tradeability", {}) or {}
    if tradeability.get("classification") == "no_edge":
        _log(f"[INDIA SUG] Skipping watchlist creation for news_id={news_id} because tradeability is no_edge")
        return

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
                    (invalidation)[:1000],
                    confidence
                ),
            )
            created += 1

        except Exception as e:
            _log(f"[INDIA SUG] Error inserting compact watchlist for {item.get('symbol', '')}: {e}")
            continue

    _log(f"[INDIA SUG] Created {created} compact suggestion/watchlist row(s) for news_id={news_id}")