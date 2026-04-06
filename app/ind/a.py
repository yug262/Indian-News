import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.append(os.getcwd())

from app.ind.agent import analyze_indian_news


TEST_CASES = [
    # {
    #     "id": "scenario_1",
    #     "description": "RBI Rate Action (Macro/Broad Market)",
    #     "title": "RBI keeps repo rate unchanged, maintains neutral stance",
    #     "published_iso": datetime.now(timezone.utc).isoformat(),
    #     "summary": "The Reserve Bank of India kept the repo rate unchanged and maintained its stance.",
    #     "source": "RBI Official"
    # },
    # {
    #     "id": "scenario_2",
    #     "description": "SEBI Regulation (Regulatory/Sectoral)",
    #     "title": "SEBI tightens margin rules for F&O segment",
    #     "published_iso": datetime.now(timezone.utc).isoformat(),
    #     "summary": "New SEBI regulations aim to curb excessive speculation in derivative markets.",
    #     "source": "SEBI Gazette"
    # },
    # {
    #     "id": "scenario_3",
    #     "description": "Large Company Order (Order/Stock-Specific)",
    #     "title": "Reliance Industries wins $1.2B green energy contract",
    #     "published_iso": datetime.now(timezone.utc).isoformat(),
    #     "summary": "RIL has been awarded a major contract for solar panel manufacturing under PLI scheme.",
    #     "source": "BSE Filing"
    # },
    {
        "title": "Infosys Q4 net profit rises 18%, beats estimates",
        "published_iso": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "source": "BSE Filing"
    },
    {
        "title": "Tata Motors misses quarterly EBITDA estimates, margin slips",
        "published_iso": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "source": "NSE Filing"
    },
    {
        "title": "Brent crude falls below $75 on demand concerns",
        "published_iso": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "source": "Reuters"
    },
]


def is_valid_result(result: dict) -> bool:
    if not isinstance(result, dict):
        return False

    required_keys = [
        "event",
        "analysis",
        "market_logic",
        "affected_entities",
        "stock_impacts",
        "scenario",
        "executive_summary",
    ]
    if not all(k in result for k in required_keys):
        return False

    event = result.get("event", {})
    analysis = result.get("analysis", {})

    if not isinstance(event, dict) or not isinstance(analysis, dict):
        return False

    event_required = ["title", "source", "timestamp_utc", "event_type", "status", "scope"]
    analysis_required = ["summary", "market_bias", "impact_score", "confidence", "horizon", "surprise"]

    if not all(k in event for k in event_required):
        return False
    if not all(k in analysis for k in analysis_required):
        return False

    return True


def main():
    print("\n" + "=" * 60)
    print("      INDIAN MARKET AGENT PRODUCTION TEST SUITE")
    print("=" * 60 + "\n")

    for i, case in enumerate(TEST_CASES, 1):
        print(f"Title: {case['title']}")
        print(f"Source: {case['source']}")

        try:
            result = analyze_indian_news(
                title=case["title"],
                published_iso=case["published_iso"],
                summary=case["summary"],
                source=case["source"]
            )

            if not result:
                print("Result: FAIL (None returned)")
            elif not is_valid_result(result):
                print("Result: FAIL (Invalid structure)")
                print(json.dumps(result, indent=2))
            else:
                print("Result: PASS")
                print(json.dumps(result, indent=2))

        except Exception as e:
            print(f"Result: ERROR - {str(e)}")
            import traceback
            traceback.print_exc()

        print("-" * 60)
        time.sleep(3)

    print("\n" + "=" * 60)
    print("            TEST SUITE EXECUTION COMPLETE")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()