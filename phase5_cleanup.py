import os
import re

SERVER_PATH = "d:\\News (1)\\server.py"

def main():
    # 1. DELETE GLOBAL FILES
    files_to_delete = [
        "d:\\News (1)\\app\\core\\agent.py",
        "d:\\News (1)\\app\\workers\\monitor.py",
        "d:\\News (1)\\app\\scrap_news\\scraper.py",
        "d:\\News (1)\\frontend\\index.html",
        "d:\\News (1)\\frontend\\app.js",
    ]
    
    for f in files_to_delete:
        if os.path.exists(f):
            os.remove(f)
            print(f"Deleted {f}")
        else:
            print(f"Already deleted or missing: {f}")

    # 2. REFACTOR SERVER.PY
    with open(SERVER_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    
    lines = content.split("\n")
    new_lines = []
    in_global_route = False

    # Target routes to delete entirely
    delete_routes = [
        "/api/news\"",
        "/api/events/global\"",
        "/api/sources\"",
        "/api/analyze/{news_id}\"",
        "/api/stats\"",
        "/api/forex/pairs\"",
        "/api/forex/candles\"",
        "/api/forex/news-markers\"",
    ]

    for line in lines:
        if line.startswith("@app."):
            is_global = any(route in line for route in delete_routes)
            if is_global:
                in_global_route = True
                continue
            else:
                in_global_route = False
        elif line.startswith("# @app.") or line.startswith("try:") or line.startswith("if __name__ == ") or line.startswith("# API server"):
            in_global_route = False

        if not in_global_route:
            # We want to remove the import of analyze_news from app.core.agent
            if "from app.core.agent import analyze_news, save_analysis" in line:
                continue
            
            # We must fix get_predictions to join on indian_news instead of news
            if "LEFT JOIN news n ON n.id = p.news_id" in line:
                line = line.replace("LEFT JOIN news n", "LEFT JOIN indian_news n")
                
            new_lines.append(line)
            
    with open(SERVER_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))
    print(f"Updated {SERVER_PATH}")

if __name__ == "__main__":
    main()
