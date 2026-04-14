import re
from datetime import datetime, timezone
from dateutil import parser

# ---------------------------
# DYNAMIC VOCABULARY (SARP)
# ---------------------------

# Categorized Entities
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

# Mapping entities to categories for ID generation
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

SYSTEMIC_ACTIONS = {
    "Interest Rate Cut", "Interest Rate Hike", "Military Strike", "Attack", 
    "Missile Activity", "Explosion", "Conflict Escalation", "Ceasefire Talks", 
    "Rising Tensions", "Sanctions", "Trade Action",
    "Market Pullback", "Market Crash", "Price Surge", "Price Spike", 
    "Price Surge", "High Volatility", "Fraud Case", "Debt Default", 
    "Bankruptcy", "Structural Collapse"
}

# Key Action Verbs that define the event type
import logging

logger = logging.getLogger("event_engine")
if not logger.handlers:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s  [EVENT_ENGINE] %(message)s", datefmt="%Y-%m-%d %H:%M:%S UTC"))
    logger.addHandler(_ch)
logger.setLevel(logging.INFO)

EVENT_LOOKBACK_HOURS = 48

ACTIONS = {
    # MONETARY / MACRO
    "rate cut": "Interest Rate Cut",
    "rate hike": "Interest Rate Hike",
    "holds": "Policy Hold",
    "unchanged": "Policy Hold",
    "inflation": "Inflation Data",
    "cpi": "CPI Release",
    "gdp": "GDP Update",
    "payrolls": "Jobs Report",
    "nfp": "NFP Data",
    
    # CONFLICT / GEOPOLITICAL
    "strike": "Military Strike",
    "attack": "Attack",
    "missile": "Missile Activity",
    "explosions": "Explosion",
    "war": "Conflict Escalation",
    "ceasefire": "Ceasefire Talks",
    "tensions": "Rising Tensions",
    "sanctions": "Sanctions",
    "tariffs": "Trade Action",
    
    # MARKET INDICATORS
    "plunge": "Market Pullback",
    "crash": "Market Crash",
    "surge": "Price Surge",
    "jump": "Price Spike",
    "soar": "Price Surge",
    "stable": "Price Stability",
    "volatile": "High Volatility",
    "forecast": "Market Outlook",
    "outlook": "Impact Forecast",
    "rebound": "Recovery",
    "edge": "Marginal Shift",
    "higher": "Upside Momentum",
    "lower": "Downside Pressure",
    "hits": "Target Achievement",
    
    # CORPORATE / MARKET
    "merger": "Strategic Merger",
    "acquisition": "Acquisition",
    "takeover": "Takeover Bid",
    "joint venture": "Joint Venture",
    "partnership": "Strategic Partnership",
    "deal": "Business Deal",
    "pact": "Business Deal",
    "agreement": "Formal Agreement",
    "results": "Earnings Report",
    "profit": "Earnings Growth",
    "earnings": "Earnings Release",
    "losses": "Financial Loss",
    "listing": "New Listing",
    "ipo": "IPO Launch",
    "probe": "Investigation",
    "lawsuit": "Legal Action",
    "investigation": "Regulatory Probe",
    "resign": "Leadership Exit",
    "resigns": "Leadership Exit",
    "resignation": "Leadership Exit",
    "quits": "Leadership Exit",
    "steps down": "Leadership Exit",
    "scam": "Fraud Case",
    "fraud": "Fraud Case",
    "default": "Debt Default",
    "bankrupt": "Bankruptcy",
    "collapse": "Structural Collapse",
}

# Action Congruence Groups
ACTION_GROUPS = {
    "Earnings": {"Earnings Report", "Earnings Growth", "Earnings Release", "Financial Loss", "profit", "results", "losses"},
    "Regulatory": {"Investigation", "Regulatory Probe", "Legal Action", "Probe", "Fraud Case", "scam", "fraud", "probe", "investigation"},
    "Corporate": {"Strategic Merger", "Acquisition", "Takeover Bid", "Joint Venture", "Strategic Partnership", "Business Deal", "Formal Agreement", "IPO Launch", "New Listing"},
    "Policy": {"Interest Rate Cut", "Interest Rate Hike", "Policy Hold", "Inflation Data", "CPI Release", "GDP Update"},
    "Conflict": {"Military Strike", "Attack", "Missile Activity", "Explosion", "Conflict Escalation", "Ceasefire Talks", "Rising Tensions", "Sanctions", "Trade Action"},
    "Leadership": {"Leadership Exit"},
}

STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "for", "with", "by", "of", "and", "or", "is", "was", "be", "been", "to", "as", "amid",
    "revives", "fuels", "stirs", "sparks", "jostle", "challenge", "challenges", "likely", "report", "despite", "hits", "slides", "slips", "anxiety",
    "shares", "price", "stock", "quarterly", "results", "profit", "loss", "update", "hike", "cut", "news", "today", "yesterday", "tomorrow", "indian", "market"
}

def get_action_group(action_display_name: str) -> str:
    """Maps a display action to a broader congruence group."""
    if not action_display_name: return "Other"
    for group, members in ACTION_GROUPS.items():
        if action_display_name in members:
            return group
    return "Other"

# ---------------------------
# CORE ENGINE COMPONENTS
# ---------------------------

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
            if any(start < ue and end > us for us, ue in used_ranges): continue
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

def extract_signature_phrase(title: str, entities: list[tuple[str, int, str]]) -> str:
    """
    Extracts the most descriptive part of the headline that isn't an entity.
    Now prioritizes numbers (percentages) and key context nouns.
    """
    clean_low = clean_text(title)
    
    # Pull out percentages or large numbers specifically
    numbers = re.findall(r'\b\d+%\b|\b\d+bn\b|\b\d+m\b|\b\d+\s?bln\b', title.lower())
    
    # Remove entity keywords and common headline verbs
    entity_keys = [e[2] for e in entities]
    words = clean_low.split()
    filtered_words = []
    
    for w in words:
        if w in entity_keys: continue
        if w in STOPWORDS: continue
        if len(w) <= 2: continue
        filtered_words.append(w.title())
        
    context = " ".join(filtered_words[:2]) # Take only top 2 words for extreme brevity
    
    if numbers:
        num_str = " ".join([n.upper() for n in numbers])
        return f"{num_str} - {context}" if context else num_str
        
    return context if context else "Recent Developments"

def generate_dynamic_title(news_title: str, entities: list[tuple[str, int, str]], action: str, category: str) -> str:
    """Synthesizes a proper market event title: [Subject]: [Action] ([Context])"""
    entity_names = [e[0] for e in entities]
    
    if len(entity_names) >= 2:
        subject = f"{entity_names[0]} & {entity_names[1]}"
    elif len(entity_names) == 1:
        subject = entity_names[0]
    else:
        subject = category.title()
        
    signature = extract_signature_phrase(news_title, entities)
    
    # Build formal title
    if action and signature and signature != "Recent Developments":
        if action.lower() in signature.lower():
            return f"{subject}: {signature}"
        return f"{subject}: {action} ({signature})"
    elif action:
        return f"{subject}: {action}"
    elif signature and signature != "Recent Developments":
        return f"{subject}: {signature}"
    else:
        return f"{subject}: Market Update"

def resolve_event(news_title: str, published_date_str_or_obj=None):
    """
    Main entry point: Resolves a unique, professional event bucket.
    Keep for backward compatibility with components using the single-article resolver.
    """
    # 1. Date Handling
    date_obj = datetime.now(timezone.utc)
    if published_date_str_or_obj:
        if isinstance(published_date_str_or_obj, str):
            try:
                date_obj = parser.parse(published_date_str_or_obj)
            except: pass
        elif isinstance(published_date_str_or_obj, datetime):
            date_obj = published_date_str_or_obj
    
    time_bucket = date_obj.strftime("%Y_%m")

    # 2. Extract Components
    found_entities = extract_entities(news_title)
    action = extract_action(news_title)
    
    # 3. Determine Category
    category = "GLOBAL"
    for _, _, key in found_entities:
        if key in ENTITY_TO_CAT:
            category = ENTITY_TO_CAT[key]
            break

    # 4. Filter out generic "events"
    is_specific_event = False
    
    if found_entities and action:
        # Check if ALL entities are broad
        all_broad = all(e[0] in BROAD_ENTITIES for e in found_entities)
        
        # An event is real IF there's at least one specific entity OR the action is massive/systemic
        if (not all_broad) or (action in SYSTEMIC_ACTIONS):
            is_specific_event = True

    # Assign event only if it's truly specific
    if is_specific_event:
        entity_ids = sorted([e[0].upper().replace(" ", "_").replace("&", "").replace("-", "") for e in found_entities[:2]])
        action_id = action.upper().replace(" ", "_").replace("/", "_")
        entity_part = "_".join(entity_ids)
        
        # Precise Topic Grouping: Entity + Action (no dynamic_target to prevent plural duplication)
        event_id = f"{category}_{entity_part}_{action_id}_{time_bucket}"
        
        # 5. Generate Professional Context-Aware Title
        event_title = generate_dynamic_title(news_title, found_entities, action, category)
        
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
        # Not a specific event -> Return None for event_id and event_title
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
        # Also exclude parts of the resolved IDs if they are common words
        if w.upper() in exclude_ids: continue
        meaningful.add(w)
    return meaningful

def are_titles_related(title1: str, title2: str, ids1: list[str], ids2: list[str]) -> bool:
    """
    State-of-the-art news clustering logic.
    Primary filter: Shared Canonical Identity.
    Secondary: Topic weighting + Action Congruence.
    """
    set1 = set(ids1 or [])
    set2 = set(ids2 or [])
    
    shared_ids = set1.intersection(set2)
    if not shared_ids:
        return False

    # 1. Classification & Broad Story Check
    is_broad = len(set1) > 3 or len(set2) > 3
    
    # 2. Extract and Weight Keywords
    tokens1 = get_meaningful_tokens(title1, set1)
    tokens2 = get_meaningful_tokens(title2, set2)
    overlap = tokens1.intersection(tokens2)
    
    # Calculate weighted score
    keyword_score = 0
    for t in overlap:
        if t in STRONG_CATALYST_TOKENS:
            keyword_score += 2.0
        elif t in WEAK_TOPIC_TOKENS:
            keyword_score += 0.5
        else:
            keyword_score += 1.0

    # 3. Action Congruence Scoring
    action1 = extract_action(title1)
    action2 = extract_action(title2)
    group1 = get_action_group(action1)
    group2 = get_action_group(action2)
    
    action_score = 0
    if group1 == group2 and group1 != "Other":
        action_score = 2.0  # Boost for same segment (e.g. Earnings)
    elif group1 == "Other" or group2 == "Other":
        action_score = 1.0  # Neutral/Allowed
    elif group1 != group2:
        # Check for reaction arcs (e.g. Action -> Reaction)
        # Corporate move matches price/sentiment reaction
        if (group1 == "Corporate" and group2 == "Other") or (group1 == "Other" and group2 == "Corporate"):
            action_score = 1.0 # Allowed reaction arc
        else:
            action_score = -2.0 # Conflicting context

    # 4. Final Thresholds
    total_score = keyword_score + action_score
    
    # Normal corporate stories: need identity + strong overlap (score ~3.0+)
    required_score = 3.0
    
    # Broad sector stories: higher threshold to prevent pollution
    if is_broad:
        required_score = 5.0
        
    # Macro stories (RBI, etc): Identity is the main source
    # Broad entities often have generic keywords, so we trust identity + moderate overlap
    if any(s in ["RBI", "SEBI", "NSE", "BSE", "FED", "ECB", "UNION_BUDGET", "BRENT"] for s in shared_ids):
        required_score = 2.0 # Lower threshold for Macro identities to catch related story beats

    is_match = total_score >= required_score
    
    if is_match:
        logger.info(f"MATCH: IDs {shared_ids} | ARCS [{'Broad' if is_broad else 'Specific'}] | SCORE {total_score} | TOPICS {overlap}")
    else:
        if total_score > 1.0:
            logger.debug(f"REJECT: IDs {shared_ids} | SCORE {total_score} | REASON Insufficient Relevance")
            
    return is_match

def process_event_grouping(news_id: int, title: str, category: str, table_name: str = 'news', ai_symbols: list[str] = None):
    """
    Main stateful clustering entry point.
    """
    from app.db.db import fetch_all, execute_query
    
    # 1. Identity Resolution (Sole Truth Source)
    new_ids = ai_symbols or []
    # If scraper didn't provide symbols, attempt lazy resolution on title
    if not new_ids:
        entities = extract_entities(title)
        new_ids = [resolve_identity(e[0]) for e in entities if resolve_identity(e[0])]
    
    # Deduplicate and sort
    new_ids = sorted(list(set(new_ids)))

    logger.info(f"Grouping Analysis: news_id={news_id} | Identity: {new_ids}")

    # 2. Fetch Stateful Candidates
    recent_news = fetch_all(f"""
        SELECT id, title, event_id, event_title, symbols as ids, published 
        FROM {table_name}
        WHERE published >= NOW() - (%s * INTERVAL '1 hour')
        AND id != %s
        ORDER BY published DESC
    """, (EVENT_LOOKBACK_HOURS, news_id))
    
    if not recent_news:
        return False
        
    matched_candidates = []
    
    for old in recent_news:
        old_ids = old.get('ids') or []
        # Legacy Fallback: Resolve symbols for older rows if missing
        if not old_ids:
            old_entities = extract_entities(old['title'])
            old_ids = [resolve_identity(e[0]) for e in old_entities if resolve_identity(e[0])]
            
        if are_titles_related(title, old['title'], new_ids, old_ids):
            # Calculate match score for representative selection
            score = len(get_meaningful_tokens(title, set(new_ids)).intersection(get_meaningful_tokens(old['title'], set(old_ids))))
            matched_candidates.append({
                "article": old,
                "score": score
            })
            
    if not matched_candidates:
        return False

    # 3. Join Existing or Form New Event
    # Select best representative: Highest keyword overlap score, then newest
    matched_candidates.sort(key=lambda x: (x['score'], x['article']['published']), reverse=True)
    
    existing_event = next((c['article'] for c in matched_candidates if c['article']['event_id']), None)
    
    if existing_event:
        event_id = existing_event['event_id']
        event_title = existing_event['event_title']
        logger.info(f"news_id={news_id} matched arc: JOIN [{event_title}]")
        execute_query(f"UPDATE {table_name} SET event_id = %s, event_title = %s WHERE id = %s", 
                      (event_id, event_title, news_id))
        return True
        
    # Brand new cluster formation
    rep = matched_candidates[0]['article']
    new_event_id = f"EV_{rep['id']}_{category}"
    
    # Rep title generation
    rep_entities = extract_entities(rep['title'])
    rep_action = extract_action(rep['title'])
    new_event_title = generate_dynamic_title(rep['title'], rep_entities, rep_action, category)
    
    logger.info(f"news_id={news_id} sired new cluster: [{new_event_title}]")
    
    all_ids_to_update = [c['article']['id'] for c in matched_candidates] + [news_id]
    format_strings = ','.join(['%s'] * len(all_ids_to_update))
    
    execute_query(f"""
        UPDATE {table_name}
        SET event_id = %s, event_title = %s
        WHERE id IN ({format_strings})
    """, [new_event_id, new_event_title] + all_ids_to_update)
    
    return True
