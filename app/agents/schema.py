# app/ind/schema.py
"""
Indian Equities Analysis Schema — V6.1

Compact schema with impact triggers (killers + amplifiers).
"""

SCHEMA_TEMPLATE = {
    "signal_bucket": "",        # DIRECT | AMBIGUOUS | WEAK_PROXY | NOISE

    "event": {
        "title": "",
        "event_type": "",       # earnings | policy | order_win | macro | regulation | disruption | corporate_action | other
        "status": "",           # confirmed | developing | rumor | noise
        "scope": ""             # single_stock | sector | broad_market
    },

    "core_view": {
        "market_bias": "",      # bullish | bearish | mixed | neutral
        "impact_score": 0,      # 0-10
        "confidence": 0,        # 0-85
        "horizon": ""           # intraday | short_term | medium_term
    },

    "stock_impacts": [
        {
            "symbol": "",           # NSE ticker (e.g. RELIANCE, INFY)
            "company_name": "",
            "bias": "",             # bullish | bearish | mixed | neutral
            "reaction": "",         # weak | moderate | strong | uncertain
            "timing": "",           # open | intraday | short_term
            "why": "",
            "confidence": 0         # 0-85
        }
    ],

    "sector_impacts": [
        {
            "sector": "",
            "bias": "",             # bullish | bearish | mixed | neutral
            "why": ""
        }
    ],

    "impact_triggers": {
        "impact_killers": [         # events that would NEGATE the current thesis
            {
                "trigger": "",      # what to watch (specific, observable)
                "why": ""           # why it breaks the thesis
            }
        ],
        "impact_amplifiers": [      # events that would STRENGTHEN the current thesis
            {
                "trigger": "",      # what to watch (specific, observable)
                "why": ""           # why it amplifies the thesis
            }
        ]
    },

    "evidence_quality": {
        "confirmed": [],            # list of strings: facts explicitly stated/verified in the news
        "unknowns_risks": []        # list of strings: missing info, assumptions, or risks not yet confirmed
    },

    "tradeability": {
        "classification": "",       # actionable_now | wait_for_confirmation | no_edge
        "priced_in_assessment": "", # REMAINING IMPACT: Has the move already happened? What % of impact is left? What to expect NOW.
        "remaining_impact_state": "",   # untouched | early | partially_absorbed | mostly_absorbed | exhausted
        "reason": "",               # why this classification — 1-2 sentences
        "what_to_do": "",           # plain-English action plan for RIGHT NOW, given the time elapsed since news broke
    },

    "decision_trace": {
        "event_identification": "",
        "entity_mapping": "",
        "impact_scoring": "",
        "remaining_impact": "",
        "tradeability_reasoning": ""
    },

    "executive_summary": ""
}

REQUIRED_TOP_LEVEL_KEYS = list(SCHEMA_TEMPLATE.keys())

ALLOWED_ENUMS = {
    "signal_bucket": ["DIRECT", "AMBIGUOUS", "WEAK PROXY", "NOISE"],
    "event_type": ["Corporate Event", "Government Policy", "Macro Data", "Global Macro Impact", "Commodity Macro", "Sector Trend", "Institutional Activity", "Sentiment Indicator", "Price Action Noise", "Routine Market Update", "Other"],
    "bias": ["bullish", "bearish", "mixed", "neutral"],
    "horizon": ["intraday", "short term", "medium term"],
    "tradeability": ["Actionable Now", "Wait For Confirmation", "No Edge"],
}