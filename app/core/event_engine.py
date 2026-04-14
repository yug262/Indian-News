"""
Event Engine v3 — Production-Grade News Clustering & Title Synthesis

Architecture:
  1. Entity extraction (vocabulary-based NER)
  2. Action extraction (keyword → canonical action mapping)
  3. News-to-event ASSIGNMENT (identity overlap + action congruence + temporal decay)
  4. Event TITLE SYNTHESIS (template-based, from structured data across ALL articles)
  5. Title EVOLUTION (re-synthesize when new articles join, title updates automatically)

Title Philosophy:
  - Titles are NEVER extracted from headlines
  - Titles are synthesized from structured components: {Subject} {Action Phrase}
  - Titles evolve as more articles join: "RBI Rate Cut" → "RBI Rate Cut Cycle"
  - Multi-entity titles for conflict/deal: "India-China Trade Tensions"
"""

import re
import os
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from dateutil import parser
from google import genai

# =========================================================
# LOGGING
# =========================================================
logger = logging.getLogger("event_engine")
if not logger.handlers:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  [EVENT_ENGINE] %(message)s", datefmt="%Y-%m-%d %H:%M:%S UTC"))
    logger.addHandler(_ch)
logger.setLevel(logging.INFO)

# =========================================================
# CONFIGURATION
# =========================================================
EVENT_LOOKBACK_HOURS = 48

# =========================================================
# ENTITY VOCABULARY
# =========================================================
ENTITIES = {
    # RULERS & GOVERNMENTS (GLOBAL)
    "fed": "Fed", "federal reserve": "Fed", "powell": "Jerome Powell",
    "ecb": "ECB", "lagarde": "Christine Lagarde",
    "boj": "BOJ", "ueda": "Kazuo Ueda",
    "boe": "BOE", "bailey": "Andrew Bailey",
    "white house": "White House", "biden": "Joe Biden", "trump": "Donald Trump",
    "xi jinping": "Xi Jinping", "putin": "Vladimir Putin",
    "opec": "OPEC", "un": "United Nations",
    
    # NATIONS / REGIONS
    "us": "US", "usa": "US", "united states": "US",
    "china": "China", "iran": "Iran", "israel": "Israel", "hamas": "Hamas",
    "hezbollah": "Hezbollah", "lebanon": "Lebanon", "ukraine": "Ukraine",
    "russia": "Russia", "taiwan": "Taiwan", "red sea": "Red Sea",
    "houthi": "Houthi", "middle east": "Middle East",
    
    # CORPORATES (GLOBAL)
    "apple": "Apple", "nvidia": "Nvidia", "microsoft": "Microsoft",
    "tesla": "Tesla", "google": "Google", "alphabet": "Google",
    "meta": "Meta", "amazon": "Amazon", "tsmc": "TSMC",
    "disney": "Disney", "intel": "Intel", "amd": "AMD",
    
    # INDIAN REGULATORS & GOV
    "rbi": "RBI", "reserve bank of india": "RBI", "das": "Shaktikanta Das",
    "sebi": "SEBI", "nse": "NSE", "bse": "BSE", "modi": "PM Modi",
    "sitharaman": "Nirmala Sitharaman", "finmin": "Finance Ministry",
    "supreme court": "Supreme Court", "election commission": "ECI",
    
    # INDIAN CORPORATES
    "reliance": "Reliance", "adani": "Adani", "tcs": "TCS",
    "infosys": "Infosys", "hdfc": "HDFC Bank", "sbi": "SBI",
    "icici": "ICICI Bank", "airtel": "Bharti Airtel", "itc": "ITC",
    "tata motors": "Tata Motors", "tata steel": "Tata Steel",
    "mahindra": "M&M", "wipro": "Wipro", "axis": "Axis Bank",
    "zomato": "Zomato", "paytm": "Paytm", "hal": "HAL",
    
    # COMMODITIES & FX (India-relevant)
    "gold": "Gold", "oil": "Crude Oil", "brent": "Brent Oil",
    "dollar": "US Dollar", "dxy": "DXY", "rupee": "Rupee", "inr": "INR",
}

ENTITY_TO_CAT = {
    "rbi": "INDIA", "sebi": "INDIA", "nse": "INDIA", "bse": "INDIA", "modi": "INDIA",
    "sitharaman": "INDIA", "reliance": "INDIA", "adani": "INDIA", "tcs": "INDIA",
    "infosys": "INDIA", "hdfc": "INDIA", "sbi": "INDIA", "icici": "INDIA",
    "airtel": "INDIA", "tata motors": "INDIA", "tata steel": "INDIA", "mahindra": "INDIA",
    "wipro": "INDIA", "axis": "INDIA", "zomato": "INDIA", "paytm": "INDIA", "hal": "INDIA",
    "rupee": "INDIA", "inr": "INDIA", "das": "INDIA",
}

BROAD_ENTITIES = {
    "Fed", "ECB", "BOJ", "BOE", "White House", "United Nations", "OPEC",
    "US", "China", "Iran", "Israel", "Ukraine", "Russia", "Taiwan", "Middle East", "Red Sea",
    "Hamas", "Hezbollah", "Lebanon", "Houthi",
    "RBI", "SEBI", "NSE", "BSE", "Supreme Court", "ECI", "Finance Ministry",
    "Gold", "Crude Oil", "Brent Oil", "US Dollar", "DXY", "Rupee", "INR"
}

# =========================================================
# ACTION VOCABULARY
# =========================================================
ACTIONS = {
    # MONETARY / MACRO
    "rate cut": "Interest Rate Cut", "rate hike": "Interest Rate Hike",
    "holds": "Policy Hold", "unchanged": "Policy Hold",
    "inflation": "Inflation Data", "cpi": "CPI Release",
    "gdp": "GDP Update", "payrolls": "Jobs Report", "nfp": "NFP Data",
    
    # CONFLICT / GEOPOLITICAL
    "strike": "Military Strike", "attack": "Attack",
    "missile": "Missile Activity", "explosions": "Explosion",
    "war": "Conflict Escalation", "ceasefire": "Ceasefire Talks",
    "tensions": "Rising Tensions", "sanctions": "Sanctions", "tariffs": "Trade Action",
    
    # MARKET INDICATORS
    "plunge": "Market Pullback", "crash": "Market Crash",
    "surge": "Price Surge", "jump": "Price Spike",
    "soar": "Price Surge", "stable": "Price Stability",
    "volatile": "High Volatility", "forecast": "Market Outlook",
    "outlook": "Impact Forecast", "rebound": "Recovery",
    "edge": "Marginal Shift", "higher": "Upside Momentum",
    "lower": "Downside Pressure", "hits": "Target Achievement",
    "rally": "Price Rally", "slump": "Price Slump",
    "tumble": "Market Tumble", "drop": "Price Drop",
    "gain": "Price Gain", "fall": "Price Fall",
    "rise": "Price Rise", "climb": "Price Climb",
    "decline": "Price Decline", "dip": "Price Dip",
    
    # CORPORATE / MARKET
    "merger": "Strategic Merger", "acquisition": "Acquisition",
    "takeover": "Takeover Bid", "joint venture": "Joint Venture",
    "partnership": "Strategic Partnership", "deal": "Business Deal",
    "pact": "Business Deal", "agreement": "Formal Agreement",
    "results": "Earnings Report", "profit": "Earnings Growth",
    "earnings": "Earnings Release", "losses": "Financial Loss",
    "listing": "New Listing", "ipo": "IPO Launch",
    "probe": "Investigation", "lawsuit": "Legal Action",
    "investigation": "Regulatory Probe", "resign": "Leadership Exit",
    "resigns": "Leadership Exit", "resignation": "Leadership Exit",
    "quits": "Leadership Exit", "steps down": "Leadership Exit",
    "scam": "Fraud Case", "fraud": "Fraud Case",
    "default": "Debt Default", "bankrupt": "Bankruptcy",
    "collapse": "Structural Collapse",
    "buyback": "Share Buyback", "dividend": "Dividend Declaration",
    "bonus": "Bonus Issue", "split": "Stock Split",
    "order win": "Order Win", "contract": "Contract Win",
    "expansion": "Business Expansion", "plant": "Capacity Expansion",
    "layoff": "Workforce Reduction", "layoffs": "Workforce Reduction",
    "appoint": "Leadership Appointment", "appointed": "Leadership Appointment",
}

# Action Congruence Groups — actions that describe the same narrative arc
ACTION_GROUPS = {
    "Earnings": {
        "Earnings Report", "Earnings Growth", "Earnings Release", "Financial Loss",
    },
    "Regulatory": {
        "Investigation", "Regulatory Probe", "Legal Action", "Fraud Case",
    },
    "Corporate": {
        "Strategic Merger", "Acquisition", "Takeover Bid", "Joint Venture",
        "Strategic Partnership", "Business Deal", "Formal Agreement", "IPO Launch",
        "New Listing", "Share Buyback", "Order Win", "Contract Win",
        "Business Expansion", "Capacity Expansion",
    },
    "Policy": {
        "Interest Rate Cut", "Interest Rate Hike", "Policy Hold",
        "Inflation Data", "CPI Release", "GDP Update", "Jobs Report", "NFP Data",
    },
    "Conflict": {
        "Military Strike", "Attack", "Missile Activity", "Explosion",
        "Conflict Escalation", "Ceasefire Talks", "Rising Tensions",
        "Sanctions", "Trade Action",
    },
    "Leadership": {
        "Leadership Exit", "Leadership Appointment",
    },
    "Market": {
        "Market Pullback", "Market Crash", "Price Surge", "Price Spike",
        "Price Stability", "High Volatility", "Market Outlook", "Impact Forecast",
        "Recovery", "Marginal Shift", "Upside Momentum", "Downside Pressure",
        "Target Achievement", "Price Rally", "Price Slump", "Market Tumble",
        "Price Drop", "Price Gain", "Price Fall", "Price Rise", "Price Climb",
        "Price Decline", "Price Dip",
    },
    "Dividend": {
        "Dividend Declaration", "Bonus Issue", "Stock Split",
    },
    "Workforce": {
        "Workforce Reduction",
    },
    "Crisis": {
        "Debt Default", "Bankruptcy", "Structural Collapse",
    },
}

# =========================================================
# TITLE SYNTHESIS SYSTEM
# =========================================================

# Natural action phrases for titles (what appears in the title after the subject)
ACTION_TITLE_PHRASES = {
    # Monetary / Policy
    "Interest Rate Cut":   "Rate Cut",
    "Interest Rate Hike":  "Rate Hike",
    "Policy Hold":         "Policy Hold",
    "Inflation Data":      "Inflation Update",
    "CPI Release":         "CPI Data Release",
    "GDP Update":          "GDP Growth Update",
    "Jobs Report":         "Jobs Data",
    "NFP Data":            "NFP Data Release",
    
    # Conflict
    "Military Strike":     "Military Strike",
    "Attack":              "Attack",
    "Missile Activity":    "Missile Strike",
    "Explosion":           "Explosion",
    "Conflict Escalation": "Conflict Escalation",
    "Ceasefire Talks":     "Ceasefire Talks",
    "Rising Tensions":     "Rising Tensions",
    "Sanctions":           "Sanctions",
    "Trade Action":        "Trade War",
    
    # Market Movement
    "Market Pullback":     "Selloff",
    "Market Crash":        "Market Crash",
    "Price Surge":         "Price Rally",
    "Price Spike":         "Price Spike",
    "Price Stability":     "Price Stability",
    "High Volatility":     "Volatility Spike",
    "Market Outlook":      "Market Outlook",
    "Impact Forecast":     "Market Forecast",
    "Recovery":            "Recovery Rally",
    "Marginal Shift":      "Price Movement",
    "Upside Momentum":     "Upward Move",
    "Downside Pressure":   "Downward Pressure",
    "Target Achievement":  "Hits Key Level",
    "Price Rally":         "Price Rally",
    "Price Slump":         "Price Slump",
    "Market Tumble":       "Market Tumble",
    "Price Drop":          "Price Drop",
    "Price Gain":          "Price Gain",
    "Price Fall":          "Price Decline",
    "Price Rise":          "Price Rise",
    "Price Climb":         "Price Climb",
    "Price Decline":       "Price Decline",
    "Price Dip":           "Price Dip",
    
    # Corporate
    "Strategic Merger":       "Merger Deal",
    "Acquisition":            "Acquisition",
    "Takeover Bid":           "Takeover Bid",
    "Joint Venture":          "Joint Venture",
    "Strategic Partnership":  "Strategic Partnership",
    "Business Deal":          "Business Deal",
    "Formal Agreement":       "Agreement",
    "Earnings Report":        "Earnings Report",
    "Earnings Growth":        "Earnings Beat",
    "Earnings Release":       "Earnings Results",
    "Financial Loss":         "Earnings Miss",
    "New Listing":            "Market Listing",
    "IPO Launch":             "IPO",
    "Share Buyback":          "Buyback",
    "Order Win":              "Order Win",
    "Contract Win":           "Contract Win",
    "Business Expansion":     "Expansion",
    "Capacity Expansion":     "Capacity Expansion",
    
    # Regulatory
    "Investigation":      "Under Investigation",
    "Regulatory Probe":   "Regulatory Probe",
    "Legal Action":       "Legal Battle",
    "Fraud Case":         "Fraud Scandal",
    
    # Leadership
    "Leadership Exit":        "Leadership Change",
    "Leadership Appointment": "New Leadership",
    
    # Dividend / Corporate Actions
    "Dividend Declaration": "Dividend Announcement",
    "Bonus Issue":          "Bonus Issue",
    "Stock Split":          "Stock Split",
    
    # Workforce
    "Workforce Reduction": "Layoffs",
    
    # Crisis
    "Debt Default":         "Default Crisis",
    "Bankruptcy":           "Bankruptcy",
    "Structural Collapse":  "Collapse",
}

# Title phrases EVOLVE when article count crosses thresholds
# Key: (action_group, min_articles) → evolved phrase
# Checked in reverse order (highest threshold first)
EVOLUTION_RULES = [
    # (action_group, min_articles, evolved_phrase)
    ("Earnings",   10, "Earnings Season"),
    ("Earnings",    5, "Earnings Wave"),
    ("Policy",     10, "Policy Overhaul"),
    ("Policy",      5, "Policy Cycle"),
    ("Corporate",  10, "Corporate Saga"),
    ("Corporate",   5, "Deal Developments"),
    ("Conflict",   10, "Deepening Crisis"),
    ("Conflict",    5, "Escalating Conflict"),
    ("Regulatory", 10, "Regulatory Crackdown"),
    ("Regulatory",  5, "Regulatory Storm"),
    ("Market",     10, "Market Turmoil"),
    ("Market",      5, "Sustained Move"),
    ("Leadership",  5, "Management Overhaul"),
    ("Dividend",    5, "Corporate Actions Wave"),
    ("Workforce",   5, "Restructuring"),
    ("Crisis",      5, "Deepening Crisis"),
]

# Multi-entity title connectors based on action group context
MULTI_ENTITY_GROUPS = {
    "Conflict": "-",      # "India-China Rising Tensions"
    "Corporate": "-",     # "Tata-AirIndia Merger Deal"
    "Policy": " & ",      # "RBI & Fed Rate Cut"  (rare, but possible)
}

STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "for", "with", "by", "of", "and", "or",
    "is", "was", "be", "been", "to", "as", "amid", "after", "before", "over",
    "revives", "fuels", "stirs", "sparks", "jostle", "challenge", "challenges",
    "likely", "report", "despite", "hits", "slides", "slips", "anxiety",
    "shares", "price", "stock", "quarterly", "results", "profit", "loss",
    "update", "hike", "cut", "news", "today", "yesterday", "tomorrow",
    "indian", "market", "says", "could", "may", "will", "would", "should",
    "from", "into", "about", "than", "more", "less", "new", "its", "has",
    "have", "had", "not", "but", "also", "are", "were", "this", "that",
    "which", "what", "how", "all", "can", "up", "down", "out", "off",
}

# =========================================================
# CORE TEXT PROCESSING
# =========================================================

def clean_text(text: str) -> str:
    """Standardizes text for matching."""
    t = (text or "").lower()
    t = t.replace("amp", " ").replace("&", " ")
    return re.sub(r'[^a-zA-Z0-9 ]', ' ', t)


def extract_entities(text: str) -> list[tuple[str, int, str]]:
    """Returns a list of (Display Name, Match Position, Key) for all found entities."""
    found = []
    clean = clean_text(text)
    sorted_keys = sorted(ENTITIES.keys(), key=len, reverse=True)
    used_ranges = []
    
    for key in sorted_keys:
        pattern = r'\b' + re.escape(key) + r'\b'
        for match in re.finditer(pattern, clean):
            start, end = match.span()
            if any(start < ue and end > us for us, ue in used_ranges):
                continue
            found.append((ENTITIES[key], start, key))
            used_ranges.append((start, end))
            
    found.sort(key=lambda x: x[1])
    
    # Deduplicate entities while preserving order
    unique_found = []
    seen = set()
    for e in found:
        if e[0] not in seen:
            seen.add(e[0])
            unique_found.append(e)
            
    return unique_found


def extract_action(text: str) -> str:
    """Returns the earliest significant action found in the text."""
    clean = clean_text(text)
    matches = []
    for action_key, display_name in ACTIONS.items():
        pattern = r'\b' + re.escape(action_key) + r'\b'
        match = re.search(pattern, clean)
        if match:
            matches.append((match.start(), display_name))
            
    if matches:
        matches.sort(key=lambda x: x[0])
        return matches[0][1]
        
    return None


def get_action_group(action_display_name: str) -> str:
    """Maps a display action to a broader congruence group."""
    if not action_display_name:
        return "Other"
    for group, members in ACTION_GROUPS.items():
        if action_display_name in members:
            return group
    return "Other"


# =========================================================
# TITLE SYNTHESIS (CORE NEW LOGIC)
# =========================================================

# Human-readable labels for category codes (used as fallback subject)
CATEGORY_LABELS = {
    "corporate_event": "Corporate",
    "commodity_macro": "Commodity",
    "price_action_noise": "Market",
    "routine_market_update": "Market",
    "government_policy": "Policy",
    "global": "Global",
    "india": "India",
    "other": "Market",
    "none": "Market",
}


def _humanize_category(category: str) -> str:
    """Convert raw category codes to human-readable labels."""
    if not category:
        return "Market"
    return CATEGORY_LABELS.get(category.lower().strip(), category.replace("_", " ").title())


def _extract_context_noun(article_titles: list[str]) -> str:
    """
    When no action is detected, extract a dominant context noun from titles
    to replace the generic 'Developments' fallback.
    Scans for recurring meaningful nouns across all titles.
    """
    CONTEXT_NOUNS = {
        "holiday": "Market Holiday",
        "closed": "Market Holiday",
        "budget": "Budget Update",
        "election": "Election Watch",
        "sector": "Sector Update",
        "stock": "Stock Watch",
        "ipo": "IPO Watch",
        "fii": "FII Activity",
        "dii": "DII Activity",
        "mutual fund": "Mutual Fund Update",
        "nifty": "Index Movement",
        "sensex": "Index Movement",
        "quarterly": "Quarterly Update",
        "q1": "Q1 Update", "q2": "Q2 Update", "q3": "Q3 Update", "q4": "Q4 Update",
        "target": "Price Target Update",
        "upgrade": "Rating Upgrade",
        "downgrade": "Rating Downgrade",
        "buy": "Buy Call",
        "sell": "Sell Call",
        "expansion": "Expansion Plans",
        "launch": "Product Launch",
        "approval": "Regulatory Approval",
        "ban": "Regulatory Ban",
        "penalty": "Penalty Action",
        "order": "Order Win",
        "contract": "Contract Win",
    }
    
    combined = " ".join(article_titles).lower()
    
    # Check for context nouns in priority order
    for keyword, label in CONTEXT_NOUNS.items():
        if keyword in combined:
            return label
    
    return "Update"


def synthesize_event_title(article_titles: list[str], category: str = "GLOBAL") -> str:
    """
    Synthesizes a professional event title from ALL articles in the cluster.
    
    Process:
      1. Generates title using GenAI if available and configured.
      2. Falls back to deterministic extraction if LLM fails:
           - Count entity frequency across ALL titles -> primary subject
           - Count action frequency across ALL titles -> dominant action
           - Select action phrase (with evolution based on article count)
           - Build title: "{Subject} {Action Phrase}"
    """
    if not article_titles:
        return "Market Update"
    
    article_count = len(article_titles)

    # --- 1. LLM-Based Title Synthesis ---
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("MODEL_NAME", "gemini-2.5-flash")
    
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            prompt = (
                "You are an expert financial news editor. Create a single, proper, "
                "and highly professional event headline (maximum 8 words) that summarizes "
                "the core event described in the following news article titles.\n"
                "Focus on the underlying event (e.g., 'Tata Motors Shares Plunge Over Q3 Earnings Miss').\n"
                "Do NOT use clickbait. Be deterministic and objective. "
                "Output ONLY the title string, no quotes or surrounding text.\n\n"
                "Article Titles:\n" + "\n".join(f"- {t}" for t in article_titles)
            )
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            import time
            time.sleep(1.5)  # Avoid hitting rate limits
            if response and response.text:
                title = response.text.strip().strip('"').strip("'").replace('\n', ' ')
                if title:
                    return title
        except Exception as e:
            logger.warning(f"LLM Title Synthesis failed: {e}. Falling back to rule-based.")
    
    # --- 2. Determine PRIMARY SUBJECT (Fallback) ---
    entity_counter = Counter()
    entity_positions = {}  # display_name -> list of positions
    
    for title in article_titles:
        found = extract_entities(title)
        for display_name, pos, key in found:
            entity_counter[display_name] += 1
            if display_name not in entity_positions:
                entity_positions[display_name] = []
            entity_positions[display_name].append(pos)
    
    # Primary = most frequent entity. Tie-break by earliest average position.
    primary_subject = None
    secondary_subject = None
    
    if entity_counter:
        ranked = sorted(
            entity_counter.items(),
            key=lambda x: (-x[1], sum(entity_positions[x[0]]) / len(entity_positions[x[0]]))
        )
        primary_subject = ranked[0][0]
        if len(ranked) >= 2:
            secondary_subject = ranked[1][0]
    
    # Fallback: use human-readable category label (not raw code)
    if not primary_subject:
        primary_subject = _humanize_category(category)
    
    # --- 2. Determine DOMINANT ACTION (most frequent action across all titles) ---
    action_counter = Counter()
    for title in article_titles:
        action = extract_action(title)
        if action:
            action_counter[action] += 1
    
    dominant_action = None
    if action_counter:
        dominant_action = action_counter.most_common(1)[0][0]
    
    # --- 3. Select ACTION PHRASE ---
    if dominant_action:
        action_phrase = ACTION_TITLE_PHRASES.get(dominant_action, dominant_action)
    else:
        # No action detected — use context-aware fallback instead of generic "Developments"
        action_phrase = _extract_context_noun(article_titles)
    
    # --- 4. Apply EVOLUTION based on article count ---
    action_group = get_action_group(dominant_action) if dominant_action else "Other"
    
    for group, min_articles, evolved_phrase in sorted(EVOLUTION_RULES, key=lambda x: -x[1]):
        if action_group == group and article_count >= min_articles:
            action_phrase = evolved_phrase
            break
    
    # --- 5. Build TITLE ---
    if secondary_subject and secondary_subject != primary_subject:
        secondary_frequency = entity_counter[secondary_subject] / article_count
        
        if secondary_frequency >= 0.3 and action_group in MULTI_ENTITY_GROUPS:
            connector = MULTI_ENTITY_GROUPS[action_group]
            return f"{primary_subject}{connector}{secondary_subject} {action_phrase}"
    
    return f"{primary_subject} {action_phrase}"


# =========================================================
# TITLE EVOLUTION (Called when new articles join an event)
# =========================================================

def evolve_event_title(event_id: str, table_name: str = 'indian_news'):
    """
    Re-synthesizes the event title from ALL articles currently in the event.
    
    Called every time a new article joins an existing event.
    If the synthesized title differs from the current one, it updates all rows.
    """
    from app.db.db import fetch_all, execute_query
    
    # 1. Fetch all articles in this event
    rows = fetch_all(
        f"SELECT id, title, news_category, event_title FROM {table_name} WHERE event_id = %s",
        (event_id,)
    )
    
    if not rows:
        return
    
    all_titles = [r['title'] for r in rows]
    current_title = rows[0].get('event_title', '')
    
    # Determine category from the majority of articles
    cat_counter = Counter()
    for r in rows:
        cat = (r.get('news_category') or 'other').upper()
        cat_counter[cat] += 1
    category = cat_counter.most_common(1)[0][0] if cat_counter else "GLOBAL"
    
    # 2. Synthesize new title
    new_title = synthesize_event_title(all_titles, category)
    
    # 3. Update if changed
    if new_title != current_title:
        execute_query(
            f"UPDATE {table_name} SET event_title = %s WHERE event_id = %s",
            (new_title, event_id)
        )
        logger.info(f"Title evolved: [{current_title}] → [{new_title}] (event={event_id}, articles={len(all_titles)})")


# =========================================================
# LEGACY SINGLE-ARTICLE RESOLVER (backward compat)
# =========================================================

def resolve_event(news_title: str, published_date_str_or_obj=None):
    """
    Legacy entry point: Resolves a unique event bucket for a single article.
    Kept for backward compatibility.
    """
    date_obj = datetime.now(timezone.utc)
    if published_date_str_or_obj:
        if isinstance(published_date_str_or_obj, str):
            try:
                date_obj = parser.parse(published_date_str_or_obj)
            except: pass
        elif isinstance(published_date_str_or_obj, datetime):
            date_obj = published_date_str_or_obj
    
    time_bucket = date_obj.strftime("%Y_%m")
    found_entities = extract_entities(news_title)
    action = extract_action(news_title)
    
    category = "GLOBAL"
    for _, _, key in found_entities:
        if key in ENTITY_TO_CAT:
            category = ENTITY_TO_CAT[key]
            break

    SYSTEMIC_ACTIONS = {
        "Interest Rate Cut", "Interest Rate Hike", "Military Strike", "Attack", 
        "Missile Activity", "Explosion", "Conflict Escalation", "Ceasefire Talks", 
        "Rising Tensions", "Sanctions", "Trade Action",
        "Market Pullback", "Market Crash", "Price Surge", "Price Spike", 
        "High Volatility", "Fraud Case", "Debt Default", 
        "Bankruptcy", "Structural Collapse"
    }

    is_specific_event = False
    if found_entities and action:
        all_broad = all(e[0] in BROAD_ENTITIES for e in found_entities)
        if (not all_broad) or (action in SYSTEMIC_ACTIONS):
            is_specific_event = True

    if is_specific_event:
        entity_ids = sorted([e[0].upper().replace(" ", "_").replace("&", "").replace("-", "") for e in found_entities[:2]])
        action_id = action.upper().replace(" ", "_").replace("/", "_")
        entity_part = "_".join(entity_ids)
        event_id = f"{category}_{entity_part}_{action_id}_{time_bucket}"
        event_title = synthesize_event_title([news_title], category)
        
        return {
            "event_id": event_id,
            "event_title": event_title,
            "actor": found_entities[0][0],
            "secondary_actor": found_entities[1][0] if len(found_entities) > 1 else None,
            "situation": action,
            "category": category,
            "time_bucket": time_bucket
        }
    else:
        return {
            "event_id": None,
            "event_title": None,
            "actor": found_entities[0][0] if found_entities else "GENERAL",
            "secondary_actor": found_entities[1][0] if len(found_entities) > 1 else None,
            "situation": action or "GENERAL",
            "category": category,
            "time_bucket": time_bucket
        }


# =========================================================
# PRODUCTION-GRADE EVENT GROUPING ENGINE
# =========================================================

from app.agents.tools import resolve_identity

STRONG_CATALYST_TOKENS = {
    "buyback", "merger", "acquisition", "acquires", "acquire", "takeover", "penalty", "raid", 
    "sebi action", "order win", "contract awarded", "block deal", "stake sale",
    "fired", "explosion", "scam", "fraud", "default", "bankruptcy",
    "inflation", "repo", "rate", "divest", "disinvest"
}

WEAK_TOPIC_TOKENS = {
    "results", "profit", "loss", "quarterly", "earnings", "ebitda", "revenue", 
    "guidance", "outlook", "dividend", "bonus", "split", "policy", "update"
}


def get_meaningful_tokens(text: str, exclude_ids: set[str]) -> set[str]:
    """Extracts topic-defining keywords, excluding IDs and generic finance stop words."""
    clean = clean_text(text)
    words = clean.split()
    
    meaningful = set()
    for w in words:
        if len(w) < 3: continue
        if w in STOPWORDS: continue
        if w.upper() in exclude_ids: continue
        meaningful.add(w)
    return meaningful


def are_titles_related(
    title1: str, title2: str,
    ids1: list[str], ids2: list[str],
    cat1: str = None, cat2: str = None,
    time1: datetime = None, time2: datetime = None,
) -> bool:
    """
    Production news clustering logic with category and temporal awareness.
    
    Primary filter: Shared Canonical Identity (symbols).
    Secondary: Topic weighting + Action Congruence.
    Guards: Category mismatch penalty, temporal decay.
    """
    set1 = set(ids1 or [])
    set2 = set(ids2 or [])
    
    shared_ids = set1.intersection(set2)
    if not shared_ids:
        return False

    # --- CATEGORY GUARD ---
    # Categories that are compatible and should not be penalized for cross-clustering
    COMPATIBLE_CATEGORIES = [
        # Market-related categories are all compatible with each other
        {"PRICE_ACTION_NOISE", "COMMODITY_MACRO", "ROUTINE_MARKET_UPDATE", "MARKET"},
        # Corporate categories
        {"CORPORATE_EVENT", "EARNINGS", "CORPORATE"},
        # Policy categories  
        {"GOVERNMENT_POLICY", "POLICY", "REGULATION"},
    ]
    
    category_penalty = 0.0
    if cat1 and cat2:
        c1 = cat1.upper().strip()
        c2 = cat2.upper().strip()
        # Allow "None" / "other" as wildcards
        if c1 not in ('NONE', 'OTHER', '') and c2 not in ('NONE', 'OTHER', ''):
            if c1 != c2:
                # Check if categories are compatible
                is_compatible = any(
                    c1 in group and c2 in group
                    for group in COMPATIBLE_CATEGORIES
                )
                if not is_compatible:
                    category_penalty = -3.0  # Strong penalty for truly different categories

    # --- TEMPORAL DECAY ---
    # Articles far apart in time need stronger evidence
    time_penalty = 0.0
    if time1 and time2:
        try:
            hours_apart = abs((time1 - time2).total_seconds()) / 3600
            if hours_apart > 36:
                time_penalty = -2.0  # Strong penalty for very old articles
            elif hours_apart > 24:
                time_penalty = -1.0  # Moderate penalty
            elif hours_apart > 12:
                time_penalty = -0.5  # Slight penalty
        except:
            pass

    # --- IDENTITY & BREADTH ---
    is_broad = len(set1) > 3 or len(set2) > 3
    
    # --- KEYWORD OVERLAP ---
    tokens1 = get_meaningful_tokens(title1, set1)
    tokens2 = get_meaningful_tokens(title2, set2)
    overlap = tokens1.intersection(tokens2)
    
    keyword_score = 0
    for t in overlap:
        if t in STRONG_CATALYST_TOKENS:
            keyword_score += 2.0
        elif t in WEAK_TOPIC_TOKENS:
            keyword_score += 0.5
        else:
            keyword_score += 1.0

    # --- ACTION CONGRUENCE ---
    action1 = extract_action(title1)
    action2 = extract_action(title2)
    group1 = get_action_group(action1)
    group2 = get_action_group(action2)
    
    action_score = 0
    if group1 == group2 and group1 != "Other":
        action_score = 2.0
    elif group1 == "Other" or group2 == "Other":
        action_score = 1.0
    elif group1 != group2:
        # Reaction arcs allowed
        if (group1 == "Corporate" and group2 == "Market") or \
           (group1 == "Market" and group2 == "Corporate"):
            action_score = 0.5  # Weak allowed arc (earnings → price move)
        elif (group1 == "Policy" and group2 == "Market") or \
             (group1 == "Market" and group2 == "Policy"):
            action_score = 0.5  # Policy → market reaction
        else:
            action_score = -2.0  # Conflicting context

    # --- FINAL SCORE ---
    total_score = keyword_score + action_score + category_penalty + time_penalty
    
    # Thresholds
    required_score = 3.0
    if is_broad:
        required_score = 5.0
    
    # Macro entities: lower threshold but still require category match
    macro_ids = {"RBI", "SEBI", "NSE", "BSE", "FED", "ECB", "UNION_BUDGET", "BRENT"}
    if any(s in macro_ids for s in shared_ids):
        required_score = 2.0

    is_match = total_score >= required_score
    
    if is_match:
        logger.info(
            f"MATCH: IDs {shared_ids} | Score {total_score:.1f} (kw={keyword_score:.1f} act={action_score:.1f} "
            f"cat={category_penalty:.1f} time={time_penalty:.1f}) | Topics {overlap}"
        )
    else:
        if total_score > 1.0:
            logger.debug(
                f"REJECT: IDs {shared_ids} | Score {total_score:.1f} | Threshold {required_score}"
            )
            
    return is_match


def process_event_grouping(news_id: int, title: str, category: str, table_name: str = 'news', ai_symbols: list[str] = None):
    """
    Main stateful clustering entry point.
    
    1. Resolve identity (symbols) for the new article
    2. Fetch recent candidates from DB
    3. Find best match using are_titles_related() with category & time guards
    4. Join existing event OR create new cluster
    5. Synthesize/evolve the event title from ALL articles in the cluster
    """
    from app.db.db import fetch_all, execute_query
    
    # 1. Identity Resolution
    new_ids = ai_symbols or []
    if not new_ids:
        entities = extract_entities(title)
        new_ids = [resolve_identity(e[0]) for e in entities if resolve_identity(e[0])]
    
    new_ids = sorted(list(set(new_ids)))

    logger.info(f"Grouping Analysis: news_id={news_id} | Identity: {new_ids}")

    # 2. Fetch Stateful Candidates (with category and published time for guards)
    recent_news = fetch_all(f"""
        SELECT id, title, event_id, event_title, symbols as ids, published, news_category
        FROM {table_name}
        WHERE published >= NOW() - (%s * INTERVAL '1 hour')
        AND id != %s
        ORDER BY published DESC
    """, (EVENT_LOOKBACK_HOURS, news_id))
    
    if not recent_news:
        return False
        
    # Get the new article's published time and category from DB
    from app.db.db import fetch_one
    new_article_row = fetch_one(
        f"SELECT published, news_category FROM {table_name} WHERE id = %s", (news_id,)
    )
    new_published = new_article_row['published'] if new_article_row else None
    new_category = (new_article_row.get('news_category') if new_article_row else category) or category

    matched_candidates = []
    
    for old in recent_news:
        old_ids = old.get('ids') or []
        if not old_ids:
            old_entities = extract_entities(old['title'])
            old_ids = [resolve_identity(e[0]) for e in old_entities if resolve_identity(e[0])]
        
        old_category = old.get('news_category') or 'other'
        old_published = old.get('published')
            
        if are_titles_related(
            title, old['title'],
            new_ids, old_ids,
            cat1=new_category, cat2=old_category,
            time1=new_published, time2=old_published,
        ):
            score = len(
                get_meaningful_tokens(title, set(new_ids)).intersection(
                    get_meaningful_tokens(old['title'], set(old_ids))
                )
            )
            matched_candidates.append({
                "article": old,
                "score": score
            })
            
    if not matched_candidates:
        return False

    # 3. Join Existing or Form New Event
    matched_candidates.sort(key=lambda x: (x['score'], x['article']['published']), reverse=True)
    
    existing_event = next((c['article'] for c in matched_candidates if c['article']['event_id']), None)
    
    if existing_event:
        # --- JOIN existing event ---
        event_id = existing_event['event_id']
        logger.info(f"news_id={news_id} joining event [{event_id}]")
        
        # Assign the new article to this event (with temporary title)
        execute_query(
            f"UPDATE {table_name} SET event_id = %s, event_title = %s WHERE id = %s", 
            (event_id, existing_event['event_title'], news_id)
        )
        
        # EVOLVE the title — re-synthesize from ALL articles now in this event
        evolve_event_title(event_id, table_name)
        return True
        
    # --- Brand new cluster formation ---
    rep = matched_candidates[0]['article']
    new_event_id = f"EV_{rep['id']}_{category}"
    
    # Assign all matched articles + the new article to this event
    all_ids_to_update = [c['article']['id'] for c in matched_candidates] + [news_id]
    format_strings = ','.join(['%s'] * len(all_ids_to_update))
    
    # Collect all titles for synthesis
    all_titles = [c['article']['title'] for c in matched_candidates] + [title]
    new_event_title = synthesize_event_title(all_titles, category)
    
    logger.info(f"news_id={news_id} sired new cluster [{new_event_title}] with {len(all_ids_to_update)} articles")
    
    execute_query(f"""
        UPDATE {table_name}
        SET event_id = %s, event_title = %s
        WHERE id IN ({format_strings})
    """, [new_event_id, new_event_title] + all_ids_to_update)
    
    return True


# =========================================================
# BACKWARD COMPATIBILITY EXPORTS
# =========================================================

def generate_dynamic_title(news_title: str, entities: list, action: str, category: str) -> str:
    """Legacy wrapper — redirects to synthesize_event_title."""
    return synthesize_event_title([news_title], category)
