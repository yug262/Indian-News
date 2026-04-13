"""
Frontend Dev Server — Serves the Indian News Intelligence frontend on port 3000.
Handles consistent /static/ routing for assets.
"""

import http.server
import socketserver
import os
import sys
from urllib.parse import urljoin

import requests

PORT = 3000
# Use absolute path relative to this script to avoid CWD issues
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(SCRIPT_DIR, "frontend")
BACKEND_PROXY_URL = os.getenv("BACKEND_PROXY_URL", "http://localhost:8000")

class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # We serve from the 'frontend' directory
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _proxy_to_backend(self, method: str):
        target_url = urljoin(BACKEND_PROXY_URL, self.path)
        body = None
        if method in ("POST", "PUT", "PATCH"):
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > 0:
                body = self.rfile.read(content_length)

        # Pass through most headers, excluding hop-by-hop ones.
        fwd_headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in {"host", "connection", "content-length", "accept-encoding"}
        }

        try:
            # For SSE (Server-Sent Events), we MUST use stream=True and iter_content
            # to prevent buffering the whole response in the proxy.
            # No read timeout for streaming calls to allow long-lived connections.
            is_streaming = method == "GET" and (self.path.endswith("_stream") or "stream" in self.path)
            timeout = 10 if is_streaming else 60 
            
            with requests.request(
                method=method, 
                url=target_url, 
                headers=fwd_headers, 
                data=body, 
                timeout=timeout, 
                stream=is_streaming
            ) as resp:
                self.send_response(resp.status_code)

                excluded = {
                    "connection", "keep-alive", "proxy-authenticate", 
                    "proxy-authorization", "te", "trailers", 
                    "transfer-encoding", "upgrade", "content-encoding",
                }
                for k, v in resp.headers.items():
                    if k.lower() in excluded:
                        continue
                    self.send_header(k, v)
                self.end_headers()

                if is_streaming:
                    # Stream chunks immediately to the client
                    for chunk in resp.iter_content(chunk_size=None):
                        if chunk:
                            self.wfile.write(chunk)
                            self.wfile.flush() # CRITICAL for real-time SSE
                else:
                    if resp.content:
                        self.wfile.write(resp.content)
        except Exception as e:
            self.send_error(502, f"Backend proxy error: {e}")

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy_to_backend("GET")

        # The frontend calls itself via /static/ paths when served by FastAPI.
        # When running on port 3000 separately, we treat /static/ as the root of the frontend folder.
        if self.path.startswith("/static/"):
            self.path = self.path.replace("/static/", "/", 1)
        
        clean_path = self.path.split('?')[0]
        
        # Mask legacy global routes
        if clean_path in ["/global", "/predictions", "/predictions.html", "/indian", "/indian_news.html"]:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
            
        if clean_path == "/" or clean_path == "":
            self.path = "index.html"
            
        response = super().do_GET()
        # If the super call results in a 404, we can log the actual path attempted
        return response

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy_to_backend("POST")
        self.send_error(404, "File not found")

    def log_message(self, format, *args):
        # Silent or custom logging
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format%args))

if __name__ == "__main__":
    if not os.path.exists(FRONTEND_DIR):
        print(f"Error: Frontend directory not found at {FRONTEND_DIR}")
        sys.exit(1)

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

    with ThreadingHTTPServer(("", PORT), FrontendHandler) as httpd:
        print(f"\n  STARTING: Indian News Intelligence Frontend running on http://localhost:{PORT}")
        print(f"  LINK: Backend API expected at:  http://localhost:8000")
        print(f"  --------------------------------------------------")
        print(f"  Page Routes:")
        print(f"  - Home Platform:  http://localhost:{PORT}/")
        print(f"  --------------------------------------------------\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Shutting down frontend server...")
