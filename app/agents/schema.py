SCHEMA_TEMPLATE = {
    "signal_bucket": "",            # enum: DIRECT | AMBIGUOUS | WEAK_PROXY | NOISE

    "event": {
        "title": "",
        "event_type": "",           # enum: see below
        "status": "",               # enum: confirmed | developing | rumor | follow_up | noise
        "scope": ""                 # enum: single_stock | peer_group | sector | broad_market
    },

    "core_view": {
        "market_bias": "",          # enum: bullish | bearish | mixed | neutral
        "impact_score": 0,          # integer 0-10
        "confidence": 0,            # integer 0-85, hard-capped by source_context.confidence_cap
        "horizon": ""               # enum: intraday | short_term | medium_term | "" (if no_edge)
    },

    "stock_impacts": [
        {
            "symbol": "",
            "company_name": "",
            "bias": "",             # enum: bullish | bearish | mixed | neutral
            "reaction": "",         # enum: gap_up | gap_down | intraday_rally |
                                    #       intraday_decline | flat_upside_bias |
                                    #       flat_downside_bias | volatile | unclear
            "timing": "",           # enum: intraday | next_session | short_term | unclear
            "why": "",              # max 2 sentences, specific transmission logic
            "confidence": 0         # integer 0-85
        }
    ],

    "sector_impacts": [
        {
            "sector": "",
            "bias": "",             # enum: bullish | bearish | mixed | neutral
            "why": ""               # max 2 sentences, specific mechanism
        }
    ],

    "impact_triggers": {
        "impact_killers": [
            {
                "trigger": "",      # specific observable condition
                "why": ""           # what market effect follows
            }
        ],
        "impact_amplifiers": [
            {
                "trigger": "",
                "why": ""
            }
        ]
    },

    "evidence_quality": {
        "confirmed": [],            # max 4 items, each string max 15 words
        "unknowns_risks": []        # max 3 items, each string, specific gaps only
    },

    "tradeability": {
        "classification": "",
        "time_since_publication_hours": 0.0,  # NEW FIELD
        "remaining_impact_state": "",
        "priced_in_assessment": "",
        "reason": "",
        "what_to_do": ""
    },

    "decision_trace": {
        "event_identification": "",
        "entity_mapping": "",
        "impact_scoring": "",
        "market_reaction_tests": "",  # NEW: "Confirmation: YES/NO, Rejection: YES/NO, Time: Xh"
        "remaining_impact": "",
        "tradeability_reasoning": ""
    },

    "executive_summary": ""         # max 2 sentences, no new conclusions
}

ALLOWED_ENUMS = {
    "signal_bucket": [
        "DIRECT", "AMBIGUOUS", "WEAK_PROXY", "NOISE"
    ],
    "event_type": [
        "Corporate Event", "Government Policy", "Macro Data",
        "Global Macro Impact", "Commodity Macro", "Sector Trend",
        "Institutional Activity", "Sentiment Indicator",
        "Price Action Noise", "Routine Market Update", "Other"
    ],
    "event_status": [
        "confirmed", "developing", "rumor", "follow_up", "noise"
    ],
    "event_scope": [
        "single_stock", "peer_group", "sector", "broad_market"
    ],
    "bias": [
        "bullish", "bearish", "mixed", "neutral"
    ],
    "horizon": [
        "intraday", "short_term", "medium_term", ""
    ],
    "stock_reaction": [
        "gap_up", "gap_down", "intraday_rally", "intraday_decline",
        "flat_upside_bias", "flat_downside_bias", "volatile", "unclear"
    ],
    "stock_timing": [
        "intraday", "next_session", "short_term", "unclear"
    ],
    "tradeability": [
        "actionable_now", "wait_for_confirmation", "no_edge"
    ],
    "remaining_impact_state": [
        "untouched", "early", "partially_absorbed",
        "mostly_absorbed", "exhausted", "not_applicable"
    ]
}