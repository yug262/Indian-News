"""
Frontend Dev Server — Serves the Zentrade frontend on port 3000.
Handles consistent /static/ routing for assets.
"""

import http.server
import socketserver
import os
import sys

PORT = 3000
# Assuming this script is run from the project root
FRONTEND_DIR = os.path.join(os.getcwd(), "frontend")

class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # We serve from the 'frontend' directory
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        # The frontend calls itself via /static/ paths when served by FastAPI.
        # When running on port 3000 separately, we treat /static/ as the root of the frontend folder.
        if self.path.startswith("/static/"):
            self.path = self.path.replace("/static/", "/", 1)
        
        # Route logic for clean URLs if needed
        clean_path = self.path.split('?')[0]
        if clean_path == "/" or clean_path == "/indian":
            self.path = "/indian_news.html"
        # elif clean_path == "/global":
        #     self.path = "/index.html"
            
        return super().do_GET()

    def log_message(self, format, *args):
        # Silent or custom logging
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format%args))

if __name__ == "__main__":
    if not os.path.exists(FRONTEND_DIR):
        print(f"Error: Frontend directory not found at {FRONTEND_DIR}")
        sys.exit(1)

    with socketserver.TCPServer(("", PORT), FrontendHandler) as httpd:
        print(f"\n  🚀 Zentrade Frontend running on http://localhost:{PORT}")
        print(f"  🔗 Backend API expected at:  http://localhost:8000")
        print(f"  --------------------------------------------------")
        print(f"  Page Routes:")
        print(f"  - Home (Indian):  http://localhost:{PORT}/")
        # print(f"  - Global Feed:    http://localhost:{PORT}/global")
        print(f"  --------------------------------------------------\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Shutting down frontend server...")
