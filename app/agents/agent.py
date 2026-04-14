# app/ind/agent.py
"""
Indian Equities Analysis Agent — V6

Architecture: Planner → Tool Executor → Analysis LLM → Minimal Post-Processing

Changes vs V5:
- Removed 150+ lines of post-processing overrides. If we need that much correction, the prompt is broken.
- Kept only: symbol verification, confidence cap, NOISE enforcement.
- normalize_to_schema() shrunk from 250→60 lines (matches simplified schema).
- Tool registry updated for merged get_stock_context().
"""
from __future__ import annotations

import json
import os
import time
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Optional, Dict

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.db.db import execute_query, fetch_one
from app.agents.planner import run_planner
from app.agents.prompt import INDIAN_MARKET_CLASSIFY_PROMPT, INDIAN_SYSTEM_PROMPT, build_compact_prompt
from app.agents.schema import SCHEMA_TEMPLATE
from app.agents.tools import (
    get_source_credibility,
    get_market_status,
    get_stock_context,
    classify_novelty,
    get_peer_reaction,
    get_broad_market_snapshot,
    resolve_company,
)


load_dotenv()

BASE_DELAY = 5
MAX_RETRIES = 3
MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# =========================================================
# HELPERS
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


def _safe_json_loads(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty JSON response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        if start == -1:
            raise ValueError("LLM did not return valid JSON")
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
        raise ValueError("LLM did not return valid JSON")


def _validate_nse_symbol(symbol: str) -> bool:
    """Check if a symbol exists in the companies DB."""
    try:
        row = fetch_one("SELECT nse_symbol FROM companies WHERE nse_symbol = %s LIMIT 1", (symbol,))
        return row is not None
    except Exception:
        return False


def _get_text_response(response) -> str:
    if not response or not response.candidates:
        return ""
    texts = []
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            texts.append(part.text)
    return "\n".join(texts).strip()


# =========================================================
# SCHEMA NORMALIZATION (compact — matches V6 schema)
# =========================================================

def normalize_to_schema(data: dict) -> dict:
    """Validate and normalize LLM output to match V6 schema."""
    valid_bias = {"bullish", "bearish", "mixed", "neutral"}
    valid_buckets = {"DIRECT", "AMBIGUOUS", "WEAK_PROXY", "NOISE"}

    # signal_bucket
    bucket = str(data.get("signal_bucket", "") or "").strip().upper()
    data["signal_bucket"] = bucket if bucket in valid_buckets else "AMBIGUOUS"

    # core_view
    cv = data.get("core_view", {}) or {}
    cv["impact_score"] = int(max(0, min(10, int(cv.get("impact_score", 0) or 0))))
    cv["confidence"] = int(max(0, min(85, int(cv.get("confidence", 0) or 0))))

    bias = str(cv.get("market_bias", "neutral") or "neutral").strip().lower()
    cv["market_bias"] = bias if bias in valid_bias else "neutral"

    horizon = str(cv.get("horizon", "short_term") or "short_term").strip().lower()
    if horizon not in {"intraday", "short_term", "medium_term"}:
        horizon = "short_term"
    cv["horizon"] = horizon
    data["core_view"] = cv

    # stock_impacts — basic cleanup
    cleaned_stocks = []
    for item in data.get("stock_impacts", []) or []:
        item = dict(item)
        item["symbol"] = str(item.get("symbol", "") or "").strip().upper()
        item["company_name"] = str(item.get("company_name", "") or "").strip()
        b = str(item.get("bias", "neutral") or "neutral").strip().lower()
        if b == "positive": b = "bullish"
        elif b == "negative": b = "bearish"
        item["bias"] = b if b in valid_bias else "neutral"
        item["confidence"] = int(max(0, min(85, int(item.get("confidence", 0) or 0))))
        item["why"] = str(item.get("why", "") or "").strip()
        item["reaction"] = str(item.get("reaction", "") or "uncertain").strip().lower()
        if item["reaction"] not in {"weak", "moderate", "strong", "uncertain"}:
            item["reaction"] = "uncertain"
        item["timing"] = str(item.get("timing", "") or "short_term").strip().lower()
        if item["timing"] not in {"open", "intraday", "short_term"}:
            item["timing"] = "short_term"

        # Skip empty symbols or headline-as-company-name
        if not item["symbol"] or not item["company_name"] or len(item["company_name"].split()) > 12:
            continue
        cleaned_stocks.append(item)
    data["stock_impacts"] = cleaned_stocks

    # sector_impacts — basic cleanup
    cleaned_sectors = []
    for item in data.get("sector_impacts", []) or []:
        item = dict(item)
        sector_name = str(item.get("sector", "") or "").strip()
        if not sector_name:
            continue
        b = str(item.get("bias", "neutral") or "neutral").strip().lower()
        if b == "positive": b = "bullish"
        elif b == "negative": b = "bearish"
        item["bias"] = b if b in valid_bias else "neutral"
        item["why"] = str(item.get("why", "") or "").strip()
        cleaned_sectors.append({"sector": sector_name, "bias": item["bias"], "why": item["why"]})
    data["sector_impacts"] = cleaned_sectors

    # tradeability — normalize to object
    trade_raw = data.get("tradeability", {})
    if isinstance(trade_raw, str):
        trade_raw = {"classification": trade_raw}
    if not isinstance(trade_raw, dict):
        trade_raw = {}
    classification = str(trade_raw.get("classification", "no_edge") or "no_edge").strip().lower()
    if classification not in {"actionable_now", "wait_for_confirmation", "no_edge"}:
        classification = "no_edge"
    data["tradeability"] = {
        "classification": classification,
        "priced_in_assessment": str(trade_raw.get("priced_in_assessment", "") or "").strip()[:500],
        "remaining_impact_state": str(trade_raw.get("remaining_impact_state", "") or "untouched").strip().lower(),
        "reason": str(trade_raw.get("reason", "") or "").strip()[:500],
        "what_to_do": str(trade_raw.get("what_to_do", "") or "").strip()[:500],
    }
    # Enforce: no_edge means empty assessment
    if classification == "no_edge":
        data["tradeability"]["what_to_do"] = data["tradeability"]["what_to_do"] or "No trade."
        data["tradeability"]["priced_in_assessment"] = ""

    # impact_triggers — validate structure
    triggers = data.get("impact_triggers", {}) or {}
    if not isinstance(triggers, dict):
        triggers = {}
    killers = triggers.get("impact_killers", []) or []
    amplifiers = triggers.get("impact_amplifiers", []) or []

    # Clean killers
    clean_killers = []
    for item in killers:
        if isinstance(item, dict) and str(item.get("trigger", "")).strip():
            clean_killers.append({
                "trigger": str(item["trigger"]).strip()[:300],
                "why": str(item.get("why", "") or "").strip()[:300]
            })
    # Clean amplifiers
    clean_amplifiers = []
    for item in amplifiers:
        if isinstance(item, dict) and str(item.get("trigger", "")).strip():
            clean_amplifiers.append({
                "trigger": str(item["trigger"]).strip()[:300],
                "why": str(item.get("why", "") or "").strip()[:300]
            })

    # Enforce limits by impact_score
    score = data.get("core_view", {}).get("impact_score", 0)
    if score == 0:
        clean_killers, clean_amplifiers = [], []
    elif score <= 2:
        clean_killers = clean_killers[:1]
        clean_amplifiers = clean_amplifiers[:1]
    else:
        clean_killers = clean_killers[:3]
        clean_amplifiers = clean_amplifiers[:3]

    data["impact_triggers"] = {
        "impact_killers": clean_killers,
        "impact_amplifiers": clean_amplifiers
    }

    # evidence_quality — validate structure
    eq = data.get("evidence_quality", {}) or {}
    if not isinstance(eq, dict):
        eq = {}
    confirmed = eq.get("confirmed", []) or []
    unknowns = eq.get("unknowns_risks", []) or []
    clean_confirmed = [str(x).strip()[:200] for x in confirmed if isinstance(x, str) and str(x).strip()][:4]
    clean_unknowns = [str(x).strip()[:200] for x in unknowns if isinstance(x, str) and str(x).strip()][:3]
    data["evidence_quality"] = {"confirmed": clean_confirmed, "unknowns_risks": clean_unknowns}

    # executive_summary
    data["executive_summary"] = str(data.get("executive_summary", "") or "").strip()

    # decision_trace — normalize structure
    dt_raw = data.get("decision_trace", {}) or {}
    data["decision_trace"] = {
        "event_identification": str(dt_raw.get("event_identification", "") or "").strip()[:1000],
        "entity_mapping": str(dt_raw.get("entity_mapping", "") or "").strip()[:1000],
        "impact_scoring": str(dt_raw.get("impact_scoring", "") or "").strip()[:1000],
        "remaining_impact": str(dt_raw.get("remaining_impact", "") or "").strip()[:1000],
        "tradeability_reasoning": str(dt_raw.get("tradeability_reasoning", "") or "").strip()[:1000],
    }

    return data

# =========================================================
# INDIAN NEWS FILTER
# =========================================================
async def filter_indian_news(title: str, description: str = "") -> Optional[Dict[str, Any]]:
    """
    Analyzes Indian news using Gemini and the provided strict prompt.
    """
    logger = logging.getLogger("india_agent")
    logger.setLevel(logging.INFO)
    
    # Suppress noisy INFO logs from dependencies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)

    if not logger.handlers:
        _ch = logging.StreamHandler()
        _ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S UTC"))
        logger.addHandler(_ch)

    if not os.getenv("GEMINI_API_KEY") or not client:
        logger.error("Gemini API key not configured.")
        return None

    try:
        user_msg = f"Headline: {title}\nDescription: {description[:500]}"
        
        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL_NAME,
                    contents=[user_msg],
                    config=types.GenerateContentConfig(
                        system_instruction=INDIAN_MARKET_CLASSIFY_PROMPT,
                        temperature=0.1,
                        max_output_tokens=300,
                        response_mime_type="application/json"
                    )
                )
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    logger.info(f"[TOKEN USAGE - INDIAN_FILTER] In: {response.usage_metadata.prompt_token_count} | Out: {response.usage_metadata.candidates_token_count} | Total: {response.usage_metadata.total_token_count}")
                break
            except Exception as e:
                err_msg = str(e).lower()
                # Retry on transient network errors or Gemini API overloads
                is_transient = any(x in err_msg for x in ["503", "unavailable", "overload", "getaddrinfo", "timeout", "connection", "11001"])
                
                if attempt < 2 and is_transient:
                    logger.warning(f"Transient error during analysis ({type(e).__name__}). Retrying {attempt+1}/3 in 3s...")
                    await asyncio.sleep(3)
                    continue
                else:
                    raise e

        if not response or not response.text:
            logger.warning(f"Empty response from Gemini for: {title[:50]}...")
            return None

        # Clean markdown code blocks if the model wrapped it
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
            
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        raw_text = raw_text.strip()
        logger.info(f"RAW TEXT FROM GEMINI: {raw_text}")

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            import ast
            logger.error("Failed standard JSON parse, attempting literal_eval fallback")
            data = ast.literal_eval(raw_text)
        
        # 1. Validation & Enum Clamping
        ALLOWED_CATEGORIES = {"corporate_event", "government_policy", "macro_data", "global_macro_impact", "commodity_macro", "sector_trend", "institutional_activity", "sentiment_indicator", "price_action_noise", "routine_market_update", "other"}
        ALLOWED_RELEVANCE = {"High Useful", "Useful", "Medium", "Neutral", "Noisy"}

        category = str(data.get("category", "routine_market_update")).strip()
        if category not in ALLOWED_CATEGORIES:
            category = "other"

        relevance = str(data.get("relevance", "Noisy")).strip()
        if relevance not in ALLOWED_RELEVANCE:
            relevance = "Noisy"

        # 2. Strict Parse Mentions
        company_mentions = data.get("company_mentions", [])
        if not isinstance(company_mentions, list):
            company_mentions = []
            
        # 3. Call Resolver safely
        if not company_mentions:
            resolved_symbols = []
        else:
            from app.agents.tools import strict_resolve_symbols
            resolved_symbols = strict_resolve_symbols(company_mentions)
            
        # 4. Final Output Formation
        return {
            "category": category,
            "relevance": relevance,
            "reason": str(data.get("reason", "No specific reason provided.")),
            "symbols": resolved_symbols
        }

    except Exception as e:
        logger.error(f"Error during Indian news analysis: {e}")
        return None

# =========================================================
# TOOL REGISTRY + EXECUTOR
# =========================================================

TOOL_REGISTRY = {
    "source_credibility": get_source_credibility,
    "novelty": classify_novelty,
    "market_snapshot": get_broad_market_snapshot,
    "stock_context": get_stock_context,
    "peer_reaction": get_peer_reaction,
    "resolve_company": resolve_company,
}


def _resolve_args(args: dict, results: dict) -> dict:
    """Resolve dependency chains like symbol_from → resolve_company output."""
    resolved = dict(args)
    if "symbol_from" in resolved:
        ref = resolved.pop("symbol_from", "")
        if ref.startswith("resolve_company:"):
            target_name = ref.split(":", 1)[1].strip().lower()
            for res in results.get("resolved_companies", []):
                if isinstance(res, dict) and res.get("status") == "resolved":
                    if res.get("input_name", "").lower() == target_name:
                        resolved["symbol"] = res.get("symbol")
                        break
    return resolved


def execute_tool_plan(plan: dict, published_iso: str, source: str, title: str, summary: str) -> tuple[dict, list[str]]:
    """Execute the Planner's tool calls. Returns (results_dict, valid_symbols)."""
    results = {}
    valid_symbols = []

    for tool_call in plan.get("tools", []):
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        args = tool_call.get("args", {})

        if name not in TOOL_REGISTRY:
            # Handle legacy tool names from planner
            if name == "price":
                name = "stock_context"
            elif name == "reaction":
                name = "stock_context"
            elif name == "relative_performance":
                name = "stock_context"
            else:
                continue

        # Inject hidden dependencies
        if name == "source_credibility":
            args["source"] = source
        elif name == "novelty":
            args["title"] = title
            args["summary"] = summary
        elif name == "stock_context":
            args["published_iso"] = published_iso
        elif name == "peer_reaction":
            args["published_iso"] = published_iso

        args_resolved = _resolve_args(args, results)

        # Symbol validation gate for stock tools
        if name in {"stock_context", "peer_reaction"}:
            symbol = args_resolved.get("symbol")
            if not symbol or not _validate_nse_symbol(symbol.upper()):
                _log(f"   [TOOL REJECTED] {name}: invalid symbol {symbol}")
                continue

        try:
            result = TOOL_REGISTRY[name](**args_resolved)

            if name == "novelty":
                results["novelty_context"] = result
            elif name == "source_credibility":
                results["source_context"] = result
            elif name == "market_snapshot":
                results["broad_market"] = result
            elif name == "stock_context":
                results.setdefault("stock_context", {})[args_resolved["symbol"].upper()] = result
            elif name == "peer_reaction":
                results.setdefault("peer_reaction", {})[args_resolved["symbol"].upper()] = result
            elif name == "resolve_company":
                results.setdefault("resolved_companies", []).append(result)

            # Track valid symbols
            if name == "resolve_company" and result.get("symbol"):
                valid_symbols.append(result["symbol"].upper())
            elif name in {"stock_context", "peer_reaction"} and args_resolved.get("symbol"):
                valid_symbols.append(args_resolved["symbol"].upper())

        except Exception as e:
            _log(f"   [TOOL ERROR] {name}({args_resolved}) failed: {e}")

    return results, list(set(valid_symbols))


# =========================================================
# SINGLE-PASS ANALYSIS
# =========================================================

def _run_analysis(title: str, summary: str, published_iso: str, source: str) -> dict:
    """Planner → Tools → LLM → Minimal Post-Processing."""
    if not client or not MODEL_NAME:
        raise ValueError("Missing MODEL_NAME or GEMINI_API_KEY")

    # 1. Planner
    plan = run_planner(title=title, summary=summary)

    # 2. Execute tools
    tool_results, valid_symbols = execute_tool_plan(plan, published_iso, source, title, summary)
    tool_results["_market_status"] = get_market_status()
    _log(f"[EXECUTOR] Valid symbols: {valid_symbols}")

    # 3. Build prompt — inject analysis time context
    analysis_now = datetime.now(timezone.utc)
    analysis_ist = analysis_now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))
    time_elapsed_minutes = None
    if published_iso:
        try:
            pub_dt = datetime.fromisoformat(published_iso.replace('Z', '+00:00'))
            time_elapsed_minutes = int((analysis_now - pub_dt).total_seconds() / 60)
        except Exception:
            pass

    hard_facts = {
        "title": title or "",
        "summary": summary or "",
        "published_iso": published_iso or "",
        "source": source or "",
        "analysis_time_ist": analysis_ist.strftime("%Y-%m-%d %H:%M IST"),
        "time_elapsed_minutes": time_elapsed_minutes,
    }
    schema_text = str(SCHEMA_TEMPLATE)
    user_prompt = build_compact_prompt(hard_facts, schema_text)
    user_prompt += f"\n\nEVIDENCE BUNDLE:\n```json\n{json.dumps(tool_results, indent=2, default=str)}\n```\n"

    # 4. LLM call
    config = types.GenerateContentConfig(
        system_instruction=INDIAN_SYSTEM_PROMPT,
        temperature=0.25,
        response_mime_type="application/json",
    )
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]

    _log("[AGENT] Single-pass LLM call...")
    response = client.models.generate_content(model=MODEL_NAME, contents=contents, config=config)

    usage = response.usage_metadata
    if usage:
        p_in = usage.prompt_token_count or 0
        p_out = usage.candidates_token_count or 0
        _log(f"[ANALYSIS TOKENS] In: {p_in} | Out: {p_out} | Total: {p_in + p_out}")

    text = _get_text_response(response)
    result = _safe_json_loads(text)
    result = normalize_to_schema(result)

    # 5. MINIMAL post-processing (only 3 rules)

    # Rule 1: NOISE enforcement
    if result.get("signal_bucket") == "NOISE":
        result["core_view"]["impact_score"] = 0
        result["tradeability"] = {
            "classification": "no_edge", "priced_in_assessment": "",
            "reason": "No actionable event.",
            "what_to_do": "No trade.", "entry_trigger": "",
            "exit_trigger": "", "invalidation": ""
        }
        result["stock_impacts"] = []
        result["sector_impacts"] = []
        result["impact_triggers"] = {"impact_killers": [], "impact_amplifiers": []}
        result["evidence_quality"] = {"confirmed": [], "unknowns_risks": []}

    # Rule 2: Symbol hallucination check
    valid_set = set(valid_symbols)
    verified_stocks = []
    for st in result.get("stock_impacts", []):
        sym = st.get("symbol", "").upper()
        if not sym:
            continue
        if sym in valid_set or _validate_nse_symbol(sym):
            verified_stocks.append(st)
        else:
            _log(f"[REJECTED] Hallucinated symbol: {sym}")
    result["stock_impacts"] = verified_stocks

    # Rule 3: Confidence cap
    result["core_view"]["confidence"] = min(result["core_view"].get("confidence", 0), 85)

    # Rule 4: Low impact cleanup
    if result["core_view"]["impact_score"] < 4:
        # Keep at most 1 stock if DIRECT with capped confidence
        if result.get("signal_bucket") == "DIRECT" and result.get("stock_impacts"):
            result["stock_impacts"] = result["stock_impacts"][:1]
            result["stock_impacts"][0]["confidence"] = min(result["stock_impacts"][0].get("confidence", 0), 30)
        else:
            result["stock_impacts"] = []
        result["sector_impacts"] = []

    result["_meta"] = {"planner_output": plan, "orchestrator": "v6"}
    return result


# =========================================================
# PUBLIC ENTRYPOINT
# =========================================================

def analyze_indian_news(
    title: str,
    published_iso: str,
    summary: str = "",
    source: str = "",
    current_news_id: int | None = None,
) -> dict | None:
    """Main entrypoint for Indian news analysis."""
    if not client or not MODEL_NAME:
        _log("[INDIA ANALYZE] Missing MODEL_NAME or GEMINI_API_KEY")
        return None

    for attempt in range(MAX_RETRIES):
        try:
            _log(f"\n[INDIA ATTEMPT {attempt + 1}/{MAX_RETRIES}]")
            result = _run_analysis(title=title, summary=summary, published_iso=published_iso, source=source)

            # Timestamp
            meta = result.get("_meta", {})
            meta["analysis_timestamp_utc"] = datetime.now(timezone.utc).isoformat()
            result["_meta"] = meta

            # Sanitize event_type
            event = result.get("event", {}) or {}
            if "price_action" in str(event.get("event_type", "")).lower():
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
    """Save analysis to DB. All rich data in analysis_data JSONB."""
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
            primary_symbol    = %s,
            executive_summary = %s,
            decision_trace    = %s
        WHERE id = %s
    """

    impact_score_val = int(core_view.get("impact_score", 0) or 0)
    market_bias_val = (core_view.get("market_bias") or "neutral").lower()
    bucket_val = (analysis.get("signal_bucket") or "unclassified").upper()

    params = (
        json.dumps(analysis),
        impact_score_val,
        market_bias_val[:20],
        bucket_val[:20],
        (event.get("event_type", "general"))[:100],
        primary_symbol,
        (analysis.get("executive_summary", "") or "")[:2000],
        json.dumps(analysis.get("decision_trace", {})),
        news_id,
    )

    execute_query(query, params)
    _log(f"\n[INDIA SAVE] news_id={news_id}")
