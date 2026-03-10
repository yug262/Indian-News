SUGGESTION_ITEM_TEMPLATE = {
    "asset": "",
    "direction": "",
    "reasoning": "",
    "market_logic": "",
    "time_window": "",
    "expected_move_pct": "",
    "invalidation": "",
    "confidence": 0
}

DIRECTIONAL_BIAS_FOREX_ITEM = {
    "pair": "",
    "direction": "",
    "impact_strength": 0,
    "confidence": 0,
    "expected_move_pct": "",
    "expected_duration": "",
    "reason": ""
}

DIRECTIONAL_BIAS_CRYPTO_ITEM = {
    "asset": "",
    "direction": "",
    "impact_strength": 0,
    "confidence": 0,
    "expected_move_pct": "",
    "expected_duration": "",
    "reason": ""
}

DIRECTIONAL_BIAS_EQUITIES_ITEM = {
    "index": "",
    "direction": "",
    "impact_strength": 0,
    "confidence": 0,
    "expected_move_pct": "",
    "expected_duration": "",
    "reason": ""
}

SCHEMA_TEMPLATE = {
    "event_metadata": {
        "title": "",
        "summary": "",
        "source": "",
        "timestamp_utc": "",
        "analysis_timestamp_utc": ""
    },

    "event_classification": {
        "event_type": "",
        "confirmation_status": "",
        "shock_type": "",
        "geographic_scope": "",
        "affected_asset_classes": []
    },

    "text_signal_analysis": {
        "hawkish_dovish_score": 0,
        "risk_on_off_score": 0,
        "uncertainty_intensity_score": 0
    },

    "core_impact_assessment": {
        "primary_impact_score": 0,
        "perceived_surprise_score": 0,
        "structural_vs_temporary": "",
        "market_category_scores": {
            "forex": 0,
            "crypto": 0,
            "global_equities": 0
        }
    },

    "directional_bias": {
        "forex": [],
        "crypto": [],
        "global_equities": []
    },

    "time_modeling": {
        "reaction_speed": "",
        "impact_duration": "",
        "time_decay_risk": ""
    },

    "probability_and_confidence": {
        "overall_confidence_score": 0,
        "confidence_breakdown": {
            "text_clarity": 0,
            "confirmation_strength": 0,
            "cross_asset_logic_strength": 0
        }
    },

    "risk_guidance": {
        "suggested_exposure_range_pct": "",
        "event_cluster_risk": "",
        "volatility_warning": ""
    },

    "event_fatigue_analysis": {
        "similar_news_last_12h": 0,
        "similar_news_last_24h": 0,
        "fatigue_score": 0,
        "novelty_label": ""
    },

    "scenario_analysis": {
        "if_event_strengthens": "",
        "if_event_fades": "",
        "invalidation_trigger": ""
    },

    "macro_linkage_reasoning": {
        "causal_chain_explanation": ""
    },

    "suggestions": {
        "status": "",
        "summary": "",
        "buy": [],
        "sell": [],
        "watch": [],
        "avoid": []
    },

    "executive_summary": ""
}

REQUIRED_TOP_LEVEL_KEYS = list(SCHEMA_TEMPLATE.keys())