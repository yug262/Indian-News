STOCK_IMPACT_ITEM = {
    "symbol": "",
    "company_name": "",
    "role": "",                    # direct / indirect / peer
    "bias": "",                    # bullish / bearish / mixed / neutral / unclear
    "expected_move": {
        "intraday": "",            # 0-1% / 1-3% / 3-5% / 5-8% / 8%+ / unclear
        "short_term": ""           # 1-3 sessions
    },
    "confidence": 0,               # 0-100
    "why": "",                     # short plain-English reason
    "risk": "",                    # biggest risk to the view
    "invalidation": ""             # what breaks the view
}

SECOND_ORDER_INSIGHT_ITEM = {
    "if": "",
    "then": "",
    "confidence": 0
}

SCHEMA_TEMPLATE = {
    "event": {
        "title": "",
        "source": "",
        "timestamp_utc": "",
        "event_type": "",          # order_win / earnings / policy / macro / sector / disruption / other
        "status": "",              # confirmed / developing / rumor / follow_up / noise
        "scope": ""                # single_stock / peer_group / sector / broad_market
    },

    "analysis": {
        "summary": "",             # one-line summary
        "market_bias": "",         # bullish / bearish / mixed / neutral / unclear
        "impact_score": 0,         # 0-10
        "confidence": 0,           # 0-100
        "horizon": "",             # intraday / short_term / medium_term / long_term
        "surprise": "",            # low / medium / high / unknown
        "why_it_matters": []       # 1-3 key points
    },

    "market_logic": {
        "financial_impact": [],    # revenue / margin / demand / cost / order_book / regulation / sentiment / valuation
        "causal_chain": ""         # short WHAT -> WHY -> MOVE chain
    },

    "affected_entities": {
        "stocks": [],
        "sectors": [],
    },

    "stock_impacts": [],

    "scenario": {
        "second_order_insights": [],
        "invalidation_trigger": ""
    },

    "evidence": [],                # 1-3 short evidence lines
    "missing_info": [],            # what is still unknown
    "executive_summary": ""        # final crisp conclusion
}

REQUIRED_TOP_LEVEL_KEYS = list(SCHEMA_TEMPLATE.keys())