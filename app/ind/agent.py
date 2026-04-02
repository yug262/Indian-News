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
from app.ind.planner import run_planner
from app.ind.prompt import INDIAN_SYSTEM_PROMPT, build_compact_prompt
from app.ind.schema import SCHEMA_TEMPLATE
from app.ind.tools import (
    map_companies_from_text,
    map_sectors_from_text,
    _compute_event_timing,
    get_indian_market_status,
    get_indian_stock_price,
    normalize_indian_source_credibility,
    get_compact_reaction_context,
    determine_novelty,
)


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
        # Try to find the outermost balanced JSON object
        start = text.find('{')
        if start == -1:
            raise ValueError("LLM did not return valid JSON")
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
        raise ValueError("LLM did not return valid JSON")


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
# SINGLE-PASS ANALYSIS PIPELINE
# =========================================================

def _get_text_response(response) -> str:
    """Extract text content from a Gemini response."""
    if not response or not response.candidates:
        return ""
    texts = []
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            texts.append(part.text)
    return "\n".join(texts).strip()

def _build_noise_result(title: str, summary: str, source: str) -> dict:
    """Return a minimal NOISE result without making an analysis LLM call."""
    return {
        "signal_bucket": "NOISE",
        "event": {
            "title": (title or "")[:200],
            "source": source or "",
            "timestamp_utc": "",
            "event_type": "other",
            "status": "noise",
            "scope": "broad_market",
        },
        "news_summary": {
            "what_happened": (title or "")[:200],
            "what_is_confirmed": [],
            "what_is_unknown": [],
        },
        "core_view": {
            "summary": "No actionable event identified.",
            "market_bias": "neutral",
            "impact_score": 0,
            "surprise_level": "low",
            "primary_horizon": "short_term",
            "overall_confidence": 15,
        },
        "affected_entities": {"stocks": [], "sectors": [], "indices": []},
        "stock_impacts": [],
        "sector_impacts": [],
        "evidence": [],
        "tradeability": {
            "classification": "no_edge",
            "reasoning": "No actionable event — classified as noise by planner.",
            "action_triggers": [],
        },
        "impact_triggers": {"impact_killers": [], "impact_amplifiers": []},
        "executive_summary": "No actionable market event identified in this article.",
    }


def _run_single_pass_analysis(
    title: str,
    summary: str,
    published_iso: str,
    source: str,
) -> dict:
    """
    Planner-driven single-pass pipeline.
    
    1. Cheap pre-compute (timing, source, market, companies)
    2. Planner LLM (~350 tokens) classifies route + selects tools
    3. NOISE → short-circuit, no analysis call
    4. Otherwise → execute planner-selected tools → analysis LLM call
    """
    if not client or not MODEL_NAME:
        raise ValueError("Missing MODEL_NAME or GEMINI_API_KEY")

    # -------------------------
    # CHEAP PRE-COMPUTE (always runs)
    # -------------------------
    timing = _compute_event_timing(published_iso)
    market = get_indian_market_status()
    cred = normalize_indian_source_credibility(source)
    
    companies_data = {"matches": []}
    text_content = f"{title} {summary}".strip()
    
    if len(text_content) > 10:
        companies_data = map_companies_from_text(title, summary, max_results=3)
        
    valid_matches = companies_data.get("matches", [])
    novelty = determine_novelty(text_content)

    _log(f"   [PRE-COMPUTE] entities={len(valid_matches)} | novelty={novelty} | source={cred.get('source_type')} | elapsed={timing.get('elapsed_minutes')}min")

    # -------------------------
    # PLANNER LLM CALL
    # -------------------------
    plan = run_planner(
        title=title,
        summary=summary,
        source_type=cred.get("source_type", "unknown"),
        entity_matches=valid_matches,
    )

    route = plan["route"]
    requested_tools = set(plan.get("tools", []))
    skip_analysis = plan.get("skip_analysis", False)

    # -------------------------
    # NOISE SHORT-CIRCUIT
    # -------------------------
    if skip_analysis and route == "NOISE":
        _log(f"   [SKIP] NOISE short-circuit — no analysis LLM call")
        result = _build_noise_result(title, summary, source)
        result["_meta"] = {
            "planner_route": route,
            "planner_output": plan,
            "orchestrator": "planner_noise_skip",
        }
        return result

    # -------------------------
    # PLANNER-DRIVEN TOOL EXECUTION
    # -------------------------
    reaction_data = {}
    valid_symbols = set()
    price_snapshots = {}
    sector_hints = []
    broad_market = {}

    # Extract symbols from pre-computed company matches
    for match in valid_matches:
        sym = match.get("symbol")
        if sym:
            valid_symbols.add(sym)

    # Tool: reaction
    if "reaction" in requested_tools and valid_symbols:
        for sym in valid_symbols:
            _log(f"   [TOOL] get_compact_reaction_context({sym})")
            reaction_data[sym] = get_compact_reaction_context(sym, published_iso, novelty=novelty)
            rd = reaction_data[sym]
            if rd:
                _log(f"          -> reaction_pct={rd.get('reaction_pct')} | reaction_vs_atr={rd.get('reaction_vs_atr')} | quality={rd.get('reaction_quality')} | absorption={rd.get('absorption_strength')}")
            else:
                _log(f"          -> (no reaction data)")

    # Tool: price
    if "price" in requested_tools and valid_symbols:
        for sym in valid_symbols:
            _log(f"   [TOOL] get_indian_stock_price({sym})")
            price_data = get_indian_stock_price(sym)
            if price_data and price_data.get("price") is not None:
                price_snapshots[sym] = {
                    "price": price_data.get("price"),
                    "day_change_pct": price_data.get("day_change_pct"),
                }
                _log(f"          -> price={price_data.get('price')} | day_change={price_data.get('day_change_pct')}%")
            else:
                _log(f"          -> (no price data)")

    # Tool: index_snapshot
    if "index_snapshot" in requested_tools:
        try:
            _log(f"   [TOOL] get_indian_stock_price(NIFTY 50)")
            nifty = get_indian_stock_price("NIFTY 50")
            _log(f"          -> price={nifty.get('price')} | day_change={nifty.get('day_change_pct')}%")
            
            _log(f"   [TOOL] get_indian_stock_price(SENSEX)")
            sensex = get_indian_stock_price("SENSEX")
            _log(f"          -> price={sensex.get('price')} | day_change={sensex.get('day_change_pct')}%")
            
            if nifty and nifty.get("day_change_pct") is not None:
                broad_market["nifty_change_pct"] = nifty["day_change_pct"]
            if sensex and sensex.get("day_change_pct") is not None:
                broad_market["sensex_change_pct"] = sensex["day_change_pct"]
        except Exception as e:
            _log(f"   [TOOL] index_snapshot failed: {e}")

    # Tool: sectors
    if "sectors" in requested_tools and valid_symbols:
        _log(f"   [TOOL] map_sectors_from_text(precomputed)")
        sector_hints = map_sectors_from_text(title, summary, precomputed_symbols=list(valid_symbols))
        _log(f"          -> sectors={sector_hints}")

    _log(f"   [TOOL] _compute_event_timing() -> elapsed={timing.get('elapsed_minutes')}min | decay={timing.get('decay_curve')}")
    _log(f"   [TOOL] normalize_source_credibility() -> type={cred.get('source_type')} | strength={cred.get('source_strength')}")
    _log(f"   [TOOL] get_market_status() -> equities={market.get('indian_equities')}")
    _log(f"   [TOOL] determine_novelty() -> {novelty}")

    # -------------------------
    # BUILD CONTEXT
    # -------------------------
    tool_context_obj = {
        "timing_context": timing,
        "source_context": cred,
        "market_status": market,
        "catalyst_type": {
            "novelty": novelty
        },
    }

    if valid_matches:
        tool_context_obj["entities_identified"] = valid_matches
    if reaction_data:
        tool_context_obj["market_absorption"] = {"reaction_data": reaction_data}
    if price_snapshots:
        tool_context_obj["current_prices"] = price_snapshots
    if sector_hints:
        tool_context_obj["sector_hints"] = sector_hints
    if broad_market:
        tool_context_obj["broad_market"] = broad_market
    
    hard_facts = {
        "title": title or "",
        "summary": summary or "",
        "published_iso": published_iso or "",
        "source": source or "",
    }
    
    schema_text = str(SCHEMA_TEMPLATE)
    user_prompt = build_compact_prompt(hard_facts, schema_text)
    
    injected_str = (
        "\n\nSUPPORTING MARKET DATA:\n"
        f"```json\n{json.dumps(tool_context_obj, indent=2)}\n```\n"
    )
    user_prompt += injected_str

    config = types.GenerateContentConfig(
        system_instruction=INDIAN_SYSTEM_PROMPT,
        temperature=0.25,
        response_mime_type="application/json",
    )

    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=user_prompt)],
        )
    ]

    _log(f"   [AGENT] Executing Single-Pass LLM Call...")
    total_input_tokens = 0
    total_output_tokens = 0
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=config,
    )

    usage = response.usage_metadata
    if usage:
        total_input_tokens += usage.prompt_token_count or 0
        total_output_tokens += usage.candidates_token_count or 0

    _log(f"   [TOKENS] In: {total_input_tokens} | Out: {total_output_tokens} | Total: {total_input_tokens + total_output_tokens}")

    text = _get_text_response(response)
    result = _safe_json_loads(text)
    
    result = normalize_to_schema(result)
    
    impact_score = result.get("core_view", {}).get("impact_score", 0)
    bucket = result.get("signal_bucket", "")
    
    if bucket == "NOISE":
        result["tradeability"]["classification"] = "no_edge"
        if impact_score > 1:
            result["core_view"]["impact_score"] = 1
            impact_score = 1
            
    # -------------------------
    # POST-PROCESSING RULES
    # -------------------------
    elapsed = timing.get("elapsed_minutes", 0)
    decay_curve = timing.get("decay_curve", "UNKNOWN")
    
    absorption_strength = "UNKNOWN"
    reaction_quality = "UNKNOWN"
    reaction_vs_atr = 0.0
    reaction_pct_max = 0.0
    if reaction_data:
        statuses = [v.get("absorption_strength", "UNKNOWN") for k, v in reaction_data.items() if v]
        if "EXHAUSTED" in statuses:
            absorption_strength = "EXHAUSTED"
        elif "STRONG_ABSORPTION" in statuses:
            absorption_strength = "STRONG_ABSORPTION"
        elif "MODERATE_ABSORPTION" in statuses:
            absorption_strength = "MODERATE_ABSORPTION"
        elif "WEAK_ABSORPTION" in statuses:
            absorption_strength = "WEAK_ABSORPTION"
        elif statuses:
            absorption_strength = statuses[0]
            
        qualities = [v.get("reaction_quality", "UNKNOWN") for k, v in reaction_data.items() if v]
        if "OVERREACTION" in qualities:
            reaction_quality = "OVERREACTION"
        elif "UNDERREACTION" in qualities:
            reaction_quality = "UNDERREACTION"
        elif "NORMAL_REACTION" in qualities:
            reaction_quality = "NORMAL_REACTION"
        elif qualities:
            reaction_quality = qualities[0]
            
        atrs = [v.get("reaction_vs_atr", 0.0) for k, v in reaction_data.items() if v]
        if atrs:
            reaction_vs_atr = max(atrs)
            
        pcts = [abs(v.get("reaction_pct", 0.0) or 0.0) for k, v in reaction_data.items() if v]
        if pcts:
            reaction_pct_max = max(pcts)

    tradeability_class = result.get("tradeability", {}).get("classification", "")
    
    # NEW ACTIONABLE NOW GATE
    if tradeability_class == "actionable_now":
        # Check if it meets the rigorous mathematical gate
        meets_gate = True
        
        if impact_score < 6:
            meets_gate = False
        elif reaction_quality != "UNDERREACTION":
            meets_gate = False
        elif absorption_strength == "EXHAUSTED":
            meets_gate = False
        elif novelty == "EXPECTED_CONTINUITY" and impact_score < 7:
            meets_gate = False
            
        if not meets_gate:
            result["tradeability"]["classification"] = "wait_for_confirmation"
            tradeability_class = "wait_for_confirmation"
            
    # Hard Big-Move Blocker
    if reaction_pct_max > 0.05 and tradeability_class == "actionable_now":
        result["tradeability"]["classification"] = "wait_for_confirmation"
        tradeability_class = "wait_for_confirmation"
        
    # Overreaction / Squeeze Downgrade
    if reaction_quality == "OVERREACTION" and tradeability_class == "actionable_now":
        result["tradeability"]["classification"] = "wait_for_confirmation"
        tradeability_class = "wait_for_confirmation"

    # Routine Continuity Dead End
    if novelty == "EXPECTED_CONTINUITY" and reaction_quality != "UNDERREACTION" and impact_score < 7:
        result["tradeability"]["classification"] = "no_edge"
        tradeability_class = "no_edge"
            
    cleaned_stocks = []
    for st in result.get("stock_impacts", []):
        st_sym = st.get("symbol", "").upper()
        if st_sym in valid_symbols:
            cleaned_stocks.append(st)
    result["stock_impacts"] = cleaned_stocks
    
    if not valid_symbols:
        result["stock_impacts"] = []
        conf = result.get("core_view", {}).get("overall_confidence", 0)
        if conf > 40:
            result["core_view"]["overall_confidence"] = 40

    if impact_score < 4:
        result["stock_impacts"] = []
        result["sector_impacts"] = []

    # Store planner decision for debugging
    result["_meta"] = {
        "planner_route": route,
        "planner_output": plan,
        "orchestrator": "planner_pipeline",
    }

    return result


def analyze_indian_news(
    title: str,
    published_iso: str,
    summary: str = "",
    source: str = "",
    current_news_id: int | None = None,
) -> dict | None:
    """
    Planner-driven Indian analysis entrypoint.

    1. Pre-compute cheap context
    2. Planner LLM classifies route + selects tools
    3. NOISE -> skip analysis call entirely
    4. Otherwise -> execute tools -> analysis LLM call
    """
    if not client or not MODEL_NAME:
        _log("[INDIA ANALYZE] Missing MODEL_NAME or GEMINI_API_KEY")
        return None

    for attempt in range(MAX_RETRIES):
        try:
            _log(f"[INDIA ATTEMPT {attempt + 1}/{MAX_RETRIES}] {title[:120]}")

            # Run the planner-driven pipeline
            result = _run_single_pass_analysis(
                title=title,
                summary=summary,
                published_iso=published_iso,
                source=source,
            )

            # Merge _meta (planner may have already set it for NOISE skip)
            existing_meta = result.get("_meta", {})
            existing_meta["analysis_timestamp_utc"] = datetime.now(timezone.utc).isoformat()
            if "orchestrator" not in existing_meta:
                existing_meta["orchestrator"] = "planner_pipeline"
            result["_meta"] = existing_meta

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
    except Exception as e:
        _log(f"[INDIA PRED] Failed to delete existing predictions for news_id={news_id}: {e}")
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
    except Exception as e:
        _log(f"[INDIA SUG] Failed to delete existing suggestions for news_id={news_id}: {e}")
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