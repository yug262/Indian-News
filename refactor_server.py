import re
import os

SERVER_PATH = "d:\\News (1)\\server.py"
INDIAN_ROUTER_PATH = "d:\\News (1)\\app\\api\\indian_router.py"

def main():
    with open(SERVER_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    
    # We will build indian_router.py with the necessary imports and the extracted routes
    header = """from fastapi import APIRouter, Query
from typing import Optional, Any, List, Dict
from datetime import datetime, timezone
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

from app.core.db import fetch_all, fetch_one

router = APIRouter()
INDIAN_SERVER_START = datetime.now(timezone.utc)
executor = ThreadPoolExecutor(max_workers=20)

async def run_with_timeout(func, timeout_sec, *args):
    try:
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(executor, lambda: func(*args))
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Operation timed out after {timeout_sec} seconds")

"""
    
    # We'll use regex to extract the functions one by one from server.py
    # We extract everything from @app.get/post until the next @app.get/post or the end of the file.
    
    routes_to_extract = [
        r"@app\.get\(\"/api/events/india\"\)",
        r"@app\.get\(\"/api/indian_news\"\)",
        r"@app\.get\(\"/api/indian_sources\"\)",
        r"@app\.get\(\"/api/nse/holidays\"\)",
        r"@app\.get\(\"/api/nse/pairs\"\)",
        r"@app\.get\(\"/api/nse/candles\"\)",
        r"@app\.get\(\"/api/nse/news-markers\"\)",
        r"@app\.get\(\"/api/indian_stats\"\)",
        r"@app\.post\(\"/api/indian_analyze/{news_id}\"\)"
    ]
    
    extracted_code = []
    
    for route_pattern in routes_to_extract:
        # The pattern looks for the route decorator, the function def, and consumes until the next @app or the `# API Routes for News Feed` comment or end of file.
        # But wait, python's regex is tricky for nested blocks. 
        # A simpler way is line-by-line parsing.
        pass

    # Line by line
    lines = content.split('\n')
    new_server_lines = []
    extracted_lines = []
    
    in_indian_route = False
    
    for i, line in enumerate(lines):
        if line.startswith("@app."):
            is_indian = any(
                route in line for route in [
                    "/api/events/india", "/api/indian_news", "/api/indian_sources",
                    "/api/nse/holidays", "/api/nse/pairs", "/api/nse/candles",
                    "/api/nse/news-markers", "/api/indian_stats", "/api/indian_analyze"
                ]
            )
            if is_indian:
                in_indian_route = True
                extracted_lines.append(line.replace("@app.", "@router."))
                continue
            else:
                in_indian_route = False
        elif line.startswith("# @app."):
            # commented route
            in_indian_route = False
        elif line.startswith("if __name__ == ") or line.startswith("# API server entry point"):
            in_indian_route = False
                
        if in_indian_route:
            extracted_lines.append(line)
            # if we see a line that is unindented and not empty and not inside a docstring/dict (hard to tell), we might be out. But since next route is @app we're safe.
            # Except at EOF.
        else:
            new_server_lines.append(line)
            
    # Write indian_router.py
    with open(INDIAN_ROUTER_PATH, "w", encoding="utf-8") as f:
        f.write(header + "\n" + "\n".join(extracted_lines))
        
    print("Created", INDIAN_ROUTER_PATH)
    
    # Inject router import into server
    final_server = "\n".join(new_server_lines)
    
    import_stmt = "from app.api.indian_router import router as indian_router\napp.include_router(indian_router)\n"
    
    if "from app.api.indian_router" not in final_server:
        # Find the end of imports or after app = FastAPI()
        app_decl_idx = final_server.find('app = FastAPI(title="News Website API")')
        if app_decl_idx != -1:
            end_of_line = final_server.find('\n', app_decl_idx)
            final_server = final_server[:end_of_line+1] + import_stmt + final_server[end_of_line+1:]
        
    with open(SERVER_PATH, "w", encoding="utf-8") as f:
        f.write(final_server)
        
    print("Updated", SERVER_PATH)

if __name__ == "__main__":
    main()
