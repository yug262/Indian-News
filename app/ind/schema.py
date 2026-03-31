# schema.py

SCHEMA_TEMPLATE = {
    "signal_bucket": "",  # DIRECT / AMBIGUOUS / WEAK_PROXY / NOISE

    "event": {
        "title": "",
        "source": "",
        "timestamp_utc": "",
        "event_type": "",      # earnings / policy / order_win / macro / regulation / disruption / corporate_action / other
        "status": "",          # confirmed / developing / rumor / follow_up / noise
        "scope": ""            # single_stock / peer_group / sector / broad_market
    },

    "news_summary": {
        "what_happened": "",
        "what_is_confirmed": [],
        "what_is_unknown": []
    },

    "core_view": {
        "summary": "",
        "market_bias": "",     # bullish / bearish / mixed / neutral / unclear
        "impact_score": 0,     # 0-10
        "surprise_level": "",  # low / medium / high / unknown
        "primary_horizon": "", # intraday / short_term / medium_term / long_term
        "overall_confidence": 0
    },

    "affected_entities": {
        "stocks": [],
        "sectors": [],
        "indices": []
    },

    "stock_impacts": [
        {
            "symbol": "",
            "company_name": "",
            "role": "",        # direct / indirect / peer / beneficiary / risk
            "bias": "",        # bullish / bearish / mixed / neutral / unclear
            "expected_move": {
                "intraday": "",
                "short_term": "",
                "medium_term": ""
            },
            "why": "",
            "confidence": 0,
            "risk": "",
            "invalidation": ""
        }
    ],

    "sector_impacts": [
        {
            "sector": "",
            "bias": "",
            "strength": "",    # low / medium / high
            "time_horizon": "",
            "why": "",
            "confidence": 0
        }
    ],

    "evidence": [
        {
            "type": "",        # confirmed_fact / management_commentary / historical_pattern / inference / market_structure
            "detail": "",
            "strength": "",    # low / medium / high
            "confidence": 0
        }
    ],

    "tradeability": {
        "classification": "",  # actionable_now / wait_for_confirmation / no_edge
        "reasoning": "",
        "action_triggers": []
    },

    "impact_triggers": {
        "impact_killers": [
            {
            "trigger": "",
            "why_it_kills_the_impact": "",
            "resulting_market_effect": "",
            "time_sensitivity": "",
            "confidence": 0
            }
        ],
        "impact_amplifiers": [
            {
            "trigger": "",
            "why_it_amplifies_the_impact": "",
            "resulting_market_effect": "",
            "time_sensitivity": "",
            "confidence": 0
            }
        ]
    },

    "executive_summary": ""
}

REQUIRED_TOP_LEVEL_KEYS = list(SCHEMA_TEMPLATE.keys())

ALLOWED_ENUMS = {
    "signal_bucket": [
        "DIRECT",
        "AMBIGUOUS",
        "WEAK_PROXY",
        "NOISE"
    ],

    "event_type": [
        "earnings",
        "policy",
        "order_win",
        "macro",
        "regulation",
        "disruption",
        "corporate_action",
        "other"
    ],

    "event_status": [
        "confirmed",
        "developing",
        "rumor",
        "follow_up",
        "noise"
    ],

    "event_scope": [
        "single_stock",
        "peer_group",
        "sector",
        "broad_market"
    ],

    "bias": [
        "bullish",
        "bearish",
        "mixed",
        "neutral",
        "unclear"
    ],

    "strength": [
        "low",
        "medium",
        "high"
    ],

    "surprise_level": [
        "low",
        "medium",
        "high",
        "unknown"
    ],

    "primary_horizon": [
        "intraday",
        "short_term",
        "medium_term",
        "long_term"
    ],

    "tradeability_classification": [
        "actionable_now",
        "wait_for_confirmation",
        "no_edge"
    ],

    "stock_role": [
        "direct",
        "indirect",
        "peer",
        "beneficiary",
        "risk"
    ],

    "evidence_type": [
        "confirmed_fact",
        "management_commentary",
        "historical_pattern",
        "inference",
        "market_structure"
    ]
}