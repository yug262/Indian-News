import sys
import os
import time
import cloudscraper
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.core.db import execute_query, fetch_all

def run_backfill():
    print("Starting background script to backfill generic descriptions...")
    # Fetch all articles that have a generic description
    generic_conditions = [
        "description = 'News from CNBC'",
        "description = 'News from Yahoo Finance'",
        "description = 'Reuters business and finance news.'",
        "description = 'Bloomberg market updates and news.'",
        "description LIKE 'Extracted directly from%'"
    ]
    query = f"SELECT id, link, source FROM news WHERE {' OR '.join(generic_conditions)}"
    articles = fetch_all(query)
    
    if not articles:
        print("No articles need backfilling.")
        return

    print(f"Found {len(articles)} articles to update.")
    
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome','platform': 'windows','desktop': True})
    
    updated = 0
    for article in articles:
        db_id = article['id']
        link = article['link']
        source = article['source']
        try:
            time.sleep(1) # respectful delay to avoid IP blocks
            if source in ["Reuters", "Bloomberg"]:
                art_resp = cffi_requests.get(link, impersonate="chrome120", timeout=10)
            else:
                art_resp = scraper.get(link, timeout=10)
                
            if art_resp.status_code == 200:
                art_soup = BeautifulSoup(art_resp.text, 'lxml')
                new_desc = None
                
                meta_desc = art_soup.find('meta', attrs={'name': 'description'}) or art_soup.find('meta', attrs={'property': 'og:description'})
                if meta_desc and meta_desc.get('content'):
                    fetched_desc = meta_desc['content'].strip()
                    if len(fetched_desc) > 25 and "business and finance news" not in fetched_desc.lower() and "market updates" not in fetched_desc.lower():
                        new_desc = fetched_desc
                    else:
                        for p in art_soup.find_all('p'):
                            if len(p.text) > 40:
                                new_desc = p.text.strip()
                                break
                else:
                    for p in art_soup.find_all('p'):
                        if len(p.text) > 40:
                            new_desc = p.text.strip()
                            break
                            
                if new_desc:
                    execute_query("UPDATE news SET description = %s WHERE id = %s", (new_desc, db_id))
                    updated += 1
                    print(f"Updated description for ID {db_id}: {new_desc[:50]}...")
        except Exception as e:
            print(f"Failed to update {db_id}: {e}")
            
    print(f"Backfill complete! Updated {updated} existing articles.")

if __name__ == "__main__":
    run_backfill()
