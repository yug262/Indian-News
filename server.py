from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.indian_router import router as indian_router
import uvicorn
import os

app = FastAPI(title="News Website API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/sources") or request.url.path.startswith("/api/indian_sources"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif request.url.path.startswith("/api/stats"):
        response.headers["Cache-Control"] = "public, max-age=30"
    elif request.url.path.startswith("/api/news") or request.url.path.startswith("/api/indian_news"):
        response.headers["Cache-Control"] = "public, max-age=5"
    elif request.url.path.startswith("/api/analyze"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

app.include_router(indian_router)

if __name__ == "__main__":
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    print(f"Starting API Server on http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=True)
