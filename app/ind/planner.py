# app/ind/planner.py
"""
Planner — Lightweight LLM-powered news routing classifier.

Purpose:
- Classify incoming news into a route (NOISE, STOCK, MACRO, AMBIGUOUS)
- Decide which tools to fetch
- Decide whether analysis LLM call is needed at all
- Output is ~40 tokens. No analysis, no scores, no bias.

This replaces the brittle Python keyword gate with semantic LLM classification.
"""

from __future__ import annotations

import json
import os
from typing import Any

from google import genai
from google.genai import types


MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

_planner_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# =========================================================
# CONSTANTS
# =========================================================

ALLOWED_TOOLS = {"reaction", "price", "index_snapshot", "sectors"}
MAX_TOOLS = 4

VALID_ROUTES = {"NOISE", "STOCK", "MACRO", "AMBIGUOUS"}

# Route-based allowed tools — Python enforces final tool selection
ROUTE_TOOL_WHITELIST = {
    "NOISE": set(),
    "STOCK": {"reaction", "price", "sectors"},
    "MACRO": {"index_snapshot"},
    "AMBIGUOUS": {"reaction", "price"},
}

FALLBACK_PLAN = {
    "route": "AMBIGUOUS",
    "tools": ["reaction", "price"],
    "skip_analysis": False,
    "reason": "planner_fallback",
}

# Words that indicate a real event — override NOISE skip
NOISE_SAFETY_KEYWORDS = {
    "earnings", "results", "order", "contract", "acquisition", "merger",
    "approval", "guidance", "penalty", "rbi", "sebi", "policy",
    "stake", "buyback", "dividend", "default", "downgrade", "upgrade",
}


# =========================================================
# PLANNER PROMPT
# =========================================================

PLANNER_SYSTEM_PROMPT = """You are a news routing classifier for an Indian equities analysis system.

Your ONLY job: classify the news and decide what market data to fetch.
You do NOT analyze impact, score importance, or judge tradeability.

ROUTES (pick exactly one):
- NOISE: opinion, commentary, listicle, explainer, no actionable event, already-known information
- STOCK: a specific Indian listed company is clearly affected by a confirmed event (earnings, order, M&A, regulation, etc.)
- MACRO: RBI, SEBI, government policy, rates, inflation, currency, budget, trade policy, GDP, broad market — no specific company but affects Indian markets
- AMBIGUOUS: real event exists but unclear who/what is affected, or mixed signals, or sector-wide theme without a dominant company

TOOLS you can suggest (pick 0-3):
- "reaction": fetch price reaction since news time for mapped companies
- "price": fetch current stock price snapshot for mapped companies
- "index_snapshot": fetch Nifty 50 and Sensex current day change
- "sectors": fetch sector labels for mapped companies from DB

RULES:
- NOISE → tools must be [], skip_analysis must be true
- STOCK → suggest ["reaction", "price"], optionally "sectors"
- MACRO → suggest ["index_snapshot"]
- AMBIGUOUS → suggest ["reaction", "price"] as safe default
- skip_analysis: true ONLY for clear NOISE (opinion, no event). When in doubt, set false.
- reason: max 15 words explaining your classification

Return ONLY valid JSON:
{"route": "", "tools": [], "skip_analysis": false, "reason": ""}"""


def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode(), flush=True)


# =========================================================
# PLANNER EXECUTION
# =========================================================

def run_planner(
    title: str,
    summary: str,
    source_type: str,
    entity_matches: list[dict],
) -> dict:
    """
    Run the planner LLM to classify news and decide tool requirements.
    
    Returns a validated plan dict. On any failure, returns FALLBACK_PLAN.
    """
    if not _planner_client or not MODEL_NAME:
        _log("[PLANNER] No client/model — using fallback")
        return dict(FALLBACK_PLAN)

    # Build compact planner input
    entities_summary = "none"
    if entity_matches:
        names = [f"{m.get('symbol', '?')} ({m.get('tier', '?')})" for m in entity_matches[:3]]
        entities_summary = ", ".join(names)

    user_prompt = (
        f"Headline: {title}\n"
        f"Summary: {(summary or '')[:300]}\n"
        f"Source type: {source_type}\n"
        f"Mapped entities: {entities_summary}\n\n"
        f"Classify this news. Return JSON only."
    )

    try:
        config = types.GenerateContentConfig(
            system_instruction=PLANNER_SYSTEM_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
        )

        response = _planner_client.models.generate_content(
            model=MODEL_NAME,
            contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
            config=config,
        )

        # Token logging
        usage = response.usage_metadata
        p_in = usage.prompt_token_count or 0 if usage else 0
        p_out = usage.candidates_token_count or 0 if usage else 0
        _log(f"   [PLANNER TOKENS] In: {p_in} | Out: {p_out} | Total: {p_in + p_out}")

        # Parse response
        raw_text = ""
        if response and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    raw_text += part.text

        if not raw_text.strip():
            _log("[PLANNER] Empty response — using fallback")
            return dict(FALLBACK_PLAN)

        plan = json.loads(raw_text.strip())
        plan = _validate_plan(plan, title, summary)

        _log(f"   [PLANNER] route={plan['route']} | tools={plan['tools']} | skip={plan['skip_analysis']} | reason={plan.get('reason', '')}")
        return plan

    except Exception as e:
        _log(f"[PLANNER] Error: {e} — using fallback")
        return dict(FALLBACK_PLAN)


def _validate_plan(plan: dict, title: str = "", summary: str = "") -> dict:
    """Validate and sanitize planner output. Enforce hard rules."""

    # Route
    route = str(plan.get("route", "")).strip().upper()
    if route not in VALID_ROUTES:
        route = "AMBIGUOUS"
    plan["route"] = route

    # Skip analysis
    skip = bool(plan.get("skip_analysis", False))

    # HARD NOISE SAFETY OVERRIDE
    # If planner says NOISE+skip but headline contains strong event words, override
    if skip and route == "NOISE":
        text_lower = f"{title} {summary}".lower()
        if any(kw in text_lower for kw in NOISE_SAFETY_KEYWORDS):
            route = "AMBIGUOUS"
            skip = False
            plan["route"] = route
            plan["reason"] = f"safety_override: {plan.get('reason', '')}"[:80]
            _log(f"   [PLANNER SAFETY] NOISE overridden to AMBIGUOUS — event keyword detected")

    # Only allow skip for NOISE
    if skip and route != "NOISE":
        skip = False
    plan["skip_analysis"] = skip

    # Tools — intersect planner suggestion with route whitelist
    raw_tools = plan.get("tools", [])
    if not isinstance(raw_tools, list):
        raw_tools = []
    
    route_allowed = ROUTE_TOOL_WHITELIST.get(route, set())
    validated_tools = [t for t in raw_tools if t in route_allowed][:MAX_TOOLS]
    
    # If planner gave empty tools but route expects some, use route defaults
    if not validated_tools and route_allowed and not skip:
        validated_tools = list(route_allowed)[:MAX_TOOLS]

    # NOISE + skip → force empty tools
    if route == "NOISE" and skip:
        validated_tools = []

    plan["tools"] = validated_tools

    # Reason
    plan["reason"] = str(plan.get("reason", ""))[:80]

    # Strip removed fields if planner returned them
    plan.pop("confidence", None)
    plan.pop("entity_strength", None)

    return plan
