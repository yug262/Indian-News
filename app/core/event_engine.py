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
    
    # COMMODITIES & FX
    "gold": "Gold", "oil": "Crude Oil", "brent": "Brent Oil",
    "bitcoin": "Bitcoin", "ethereum": "Ethereum", "crypto": "Crypto",
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
    
    "bitcoin": "CRYPTO", "ethereum": "CRYPTO", "crypto": "CRYPTO",
    "solana": "CRYPTO", "binance": "CRYPTO", "coinbase": "CRYPTO",
}

BROAD_ENTITIES = {
    "Fed", "ECB", "BOJ", "BOE", "White House", "United Nations", "OPEC",
    "US", "China", "Iran", "Israel", "Ukraine", "Russia", "Taiwan", "Middle East", "Red Sea",
    "Hamas", "Hezbollah", "Lebanon", "Houthi",
    "RBI", "SEBI", "NSE", "BSE", "Supreme Court", "ECI", "Finance Ministry",
    "Gold", "Crude Oil", "Brent Oil", "Bitcoin", "Ethereum", "Crypto", "US Dollar", "DXY", "Rupee", "INR"
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

STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "for", "with", "by", "of", "and", "or", "is", "was", "be", "been", "to", "as", "amid",
    "revives", "fuels", "stirs", "sparks", "jostle", "challenge", "challenges", "likely", "report", "despite", "hits", "slides", "slips", "anxiety"
}

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
    time_display = date_obj.strftime("%b %Y")

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

# ---------------------------
# STATEFUL EVENT GROUPING
# ---------------------------

def are_titles_related(title1: str, title2: str, found_entities1: list, found_entities2: list) -> bool:
    ent1_set = set([e[0] for e in found_entities1])
    ent2_set = set([e[0] for e in found_entities2])
    
    shared_entities = ent1_set.intersection(ent2_set)
    if not shared_entities:
        return False
        
    only_broad = all(e in BROAD_ENTITIES for e in shared_entities)
    
    def get_sig(t):
        import re
        clean = re.sub(r'[^a-zA-Z0-9 ]', ' ', t.lower())
        return {w for w in clean.split() if len(w) >= 4 and w not in STOPWORDS}
        
    w1 = get_sig(title1)
    w2 = get_sig(title2)
    overlap = w1.intersection(w2)
    
    if only_broad:
        # E.g. Both have "SEBI" or "Crude Oil". Need 2 meaningful words to match.
        return len(overlap) >= 2
    else:
        # Shared specific entity (like HDFC Bank). Need 1 meaningful word to match.
        return len(overlap) >= 1

def process_event_grouping(news_id: int, title: str, category: str, table_name: str = 'news'):
    """
    Stateful evaluation. Should be invoked immediately after a news item is saved to DB.
    """
    from app.core.db import fetch_all, execute_query
    
    found_entities = extract_entities(title)
    
    # Note: Using fetch_all to get recent news. Exclude current news so it doesn't match itself.
    recent_news = fetch_all(f"""
        SELECT id, title, event_id, event_title, published 
        FROM {table_name}
        WHERE published >= NOW() - INTERVAL '48 hours'
        AND id != %s
        ORDER BY published ASC
    """, (news_id,))
    
    if not recent_news:
        return False
        
    matched_group = []
    
    for old in recent_news:
        old_entities = extract_entities(old['title'])
        if are_titles_related(title, old['title'], found_entities, old_entities):
            matched_group.append(old)
            
    if not matched_group:
        return False
        
    existing_event_id = None
    existing_event_title = None
    
    for m in matched_group:
        if m['event_id']:
            existing_event_id = m['event_id']
            existing_event_title = m['event_title']
            break
            
    if existing_event_id:
        # Join existing event
        execute_query(f"UPDATE {table_name} SET event_id = %s, event_title = %s WHERE id = %s", 
                      (existing_event_id, existing_event_title, news_id))
        return True
        
    # Brand new event
    breaking_news = matched_group[0]
    breaking_entities = extract_entities(breaking_news['title'])
    breaking_action = extract_action(breaking_news['title'])
    
    new_event_id = f"EV_{breaking_news['id']}_{category}"
    new_event_title = generate_dynamic_title(breaking_news['title'], breaking_entities, breaking_action, category)
    
    all_ids_to_update = [m['id'] for m in matched_group] + [news_id]
    format_strings = ','.join(['%s'] * len(all_ids_to_update))
    
    execute_query(f"""
        UPDATE {table_name}
        SET event_id = %s, event_title = %s
        WHERE id IN ({format_strings})
    """, [new_event_id, new_event_title] + all_ids_to_update)
    
    return True
