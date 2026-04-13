# app/ind/planner.py
"""
Planner — Lightweight LLM-powered tool selection agent.

Purpose:
- Decide which tools to fetch for a given news headline
- Output is ~40 tokens. No analysis, no scores, no bias.
- Replaces brittle Python keyword gate with semantic LLM classification.

V6 changes:
- Updated tool list: price/reaction/relative_performance → stock_context
- Simplified system prompt from 80→40 lines
"""

from __future__ import annotations

import json
import os
import copy
from typing import Any

from google import genai
from google.genai import types


MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

_planner_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# =========================================================
# CONSTANTS
# =========================================================

ALLOWED_TOOLS = {
    "source_credibility", "novelty", "market_snapshot",
    "resolve_company", "stock_context", "peer_reaction"
}
MAX_TOOLS = 6

FALLBACK_PLAN = {
    "tools": [
        {"name": "source_credibility", "args": {}},
        {"name": "novelty", "args": {}}
    ]
}


PLANNER_SYSTEM_PROMPT = """You are the TOOL PLANNING AGENT for an Indian equities news system.

Your ONLY job: decide which tools to execute before final analysis. Return a JSON tool list.

You do NOT analyze news. You do NOT decide bias or impact. You only select tools.

AVAILABLE TOOLS:
* source_credibility (args: {}) — always useful
* novelty (args: {}) — always useful
* market_snapshot (args: {}) — when macro/broad market context matters
* resolve_company (args: {"name": "company name"}) — when a named Indian company appears
* stock_context (args: {"symbol": "NSE symbol"}) — when article is about a listed company
* peer_reaction (args: {"symbol": "NSE symbol", "sector": "sector name"}) — for sector stories

RULES:

1. Always include source_credibility and novelty.
2. Use resolve_company when unsure of exact NSE symbol.
3. Use stock_context when a specific listed company is the subject.
4. Do NOT add stock tools for vague/macro articles with no named company.
5. For dependency chains: use symbol_from to reference resolve_company output.
   Example: {"name": "stock_context", "args": {"symbol_from": "resolve_company:Indian Bank"}}

OUTPUT FORMAT (JSON only):
{"tools": [{"name": "source_credibility", "args": {}}, ...]}
"""


def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode(), flush=True)


def run_planner(title: str, summary: str) -> dict:
    """Run the planner LLM. Returns validated tool plan or FALLBACK_PLAN."""
    if not _planner_client or not MODEL_NAME:
        _log("[PLANNER] No client/model — using fallback")
        return copy.deepcopy(FALLBACK_PLAN)

    user_prompt = (
        f"Headline: {title}\n"
        f"Summary: {(summary or '')[:500]}\n\n"
        f"Determine the required tool calls. Return JSON only."
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

        usage = response.usage_metadata
        p_in = usage.prompt_token_count or 0 if usage else 0
        p_out = usage.candidates_token_count or 0 if usage else 0
        _log(f"   [PLANNER TOKENS] In: {p_in} | Out: {p_out} | Total: {p_in + p_out}")

        raw_text = ""
        if response and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    raw_text += part.text

        if not raw_text.strip():
            _log("[PLANNER] Empty response — using fallback")
            return copy.deepcopy(FALLBACK_PLAN)

        plan = json.loads(raw_text.strip())
        plan = _validate_plan(plan)
        _log(f"   [PLANNER] Executing {len(plan['tools'])} tools.")
        return plan

    except Exception as e:
        _log(f"[PLANNER] Error: {e} — using fallback")
        return copy.deepcopy(FALLBACK_PLAN)


def _validate_plan(plan: dict) -> dict:
    """Validate and sanitize planner output."""
    raw_tools = plan.get("tools", [])
    if not isinstance(raw_tools, list):
        raw_tools = []

    validated = []
    seen = set()

    for t in raw_tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name or name not in ALLOWED_TOOLS:
            # Handle legacy names from cached planner behavior
            if name == "price":
                name = "stock_context"
            elif name == "reaction":
                name = "stock_context"
            elif name == "relative_performance":
                name = "stock_context"
            else:
                continue

        args = t.get("args", {})
        if not isinstance(args, dict):
            args = {}

        sig = f"{name}_" + "_".join(str(v) for _, v in sorted(args.items()))
        if sig in seen:
            continue

        seen.add(sig)
        validated.append({"name": name, "args": args})

        if len(validated) >= MAX_TOOLS:
            break

    if not validated:
        validated = copy.deepcopy(FALLBACK_PLAN["tools"])

    return {"tools": validated}
