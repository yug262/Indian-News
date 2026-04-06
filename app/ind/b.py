import json
from app.ind.agent import run_indian_classify_agent


TEST_CASES = [
    {
        "title": "RBI keeps repo rate unchanged, maintains neutral stance",
        "summary": "The Reserve Bank of India kept the repo rate unchanged and maintained its stance.",
        "expected_category": "macro_policy",
        "expected_relevance": "Medium",
    },
    {
        "title": "RBI hikes repo rate by 50 bps unexpectedly",
        "summary": "The central bank surprised markets with a larger-than-expected rate hike.",
        "expected_category": "macro_policy",
        "expected_relevance": "Very High",
    },
    {
        "title": "Infosys reports 20% rise in quarterly profit, beats estimates",
        "summary": "Infosys posted strong earnings with profit and margin improvement.",
        "expected_category": "earnings_results",
        "expected_relevance": "High",
    },
    {
        "title": "Larsen & Toubro wins Rs 10000 crore defence contract",
        "summary": "The company received a large confirmed order from the defence ministry.",
        "expected_category": "order_contract",
        "expected_relevance": "High",
    },
    {
        "title": "Government raises import duty on steel products",
        "summary": "The move is expected to affect domestic steel producers and the wider sector.",
        "expected_category": "regulation_policy",
        "expected_relevance": "High",
    },
    {
        "title": "FII sell Rs 3200 crore worth of equities, DII buy Rs 2800 crore",
        "summary": "Institutional flow data showed foreign selling and domestic support.",
        "expected_category": "market_data_flow",
        "expected_relevance": "Medium",
    },
    {
        "title": "SEBI bans entity from securities market over fraud case",
        "summary": "The regulator passed an order restricting market access.",
        "expected_category": "regulation_policy",
        "expected_relevance": "High",
    },
    {
        "title": "Brokerage says banking stocks may rally in next quarter",
        "summary": "A research note expects stronger credit growth and valuation rerating.",
        "expected_category": "analysis_opinion",
        "expected_relevance": "Low",
    },
    {
        "title": "Nifty rises 200 points in early trade",
        "summary": "Markets opened higher led by gains in financial stocks.",
        "expected_category": "other",
        "expected_relevance": "Noisy",
    },
    {
        "title": "XYZ Ltd launches new customer mobile app",
        "summary": "The company announced a new digital app for customer engagement.",
        "expected_category": "business_update",
        "expected_relevance": "Low",
    },
    {
        "title": "Promoter sells 3 percent stake in smallcap company via open market",
        "summary": "The transaction reduced promoter holding in the company.",
        "expected_category": "stake_sale_investment",
        "expected_relevance": "High",
    },
    {
        "title": "Company announces board meeting to consider fundraising",
        "summary": "The board will meet next week to evaluate fundraising options.",
        "expected_category": "fundraising_capital",
        "expected_relevance": "Low",
    },
    {
        "title": "US Fed signals prolonged higher rates, Asian markets fall",
        "summary": "Global risk sentiment weakened after hawkish commentary from the Federal Reserve.",
        "expected_category": "global_macro",
        "expected_relevance": "Medium",
    },
    {
        "title": "Supreme Court admits insolvency plea against major infrastructure company",
        "summary": "The legal development could materially affect the company and lenders.",
        "expected_category": "legal_compliance",
        "expected_relevance": "High",
    },
    {
        "title": "Celebrity endorses new beverage campaign by consumer brand",
        "summary": "The campaign aims to improve visibility among younger customers.",
        "expected_category": "other",
        "expected_relevance": "Noisy",
    },
]


def is_match(actual: dict, expected_category: str, expected_relevance: str) -> bool:
    return (
        actual.get("category") == expected_category
        and actual.get("relevance") == expected_relevance
    )


def main():
    total = len(TEST_CASES)
    passed = 0

    print("\n========== FILTER AGENT TEST START ==========\n")

    for i, case in enumerate(TEST_CASES, start=1):
        result = run_indian_classify_agent(
            title=case["title"],
            summary=case["summary"],
        )

        ok = is_match(
            result,
            case["expected_category"],
            case["expected_relevance"],
        )

        if ok:
            passed += 1

        print(f"Test #{i}")
        print(f"Title              : {case['title']}")
        print(f"Expected Category  : {case['expected_category']}")
        print(f"Expected Relevance : {case['expected_relevance']}")
        print("Actual Output      :")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Result             : {'PASS' if ok else 'FAIL'}")
        print("-" * 60)

    print("\n========== FINAL SUMMARY ==========")
    print(f"Total Tests : {total}")
    print(f"Passed      : {passed}")
    print(f"Failed      : {total - passed}")
    print(f"Accuracy    : {(passed / total * 100):.2f}%")
    print("===================================\n")


if __name__ == "__main__":
    main()