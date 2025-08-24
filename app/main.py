# app/main.py
import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Pyit Tine Htaung API", version="2.0.0")

# --- CORS: Netlify prod + local dev + Netlify previews ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pyittinehtaung.netlify.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"^https:\/\/([a-z0-9-]+--)?pyittinehtaung\.netlify\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# --- timing + safe 500s (helps Render logs) ---
@app.middleware("http")
async def timing_and_errors(request: Request, call_next):
    start = time.time()
    try:
        resp = await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    resp.headers["X-Process-Time-ms"] = f"{(time.time()-start)*1000:.2f}"
    return resp

# --- health endpoints for Render ---
@app.get("/")
def root():
    return {"ok": True, "service": "pth-chatbot-api", "version": "2.0.0"}

@app.get("/healthz")
def health():
    return {
        "ok": True,
        "service": "pth-chatbot-api",
        "version": "2.0.0",
        "tesseract_found": bool(shutil.which("tesseract")),
    }

# --- mount your router (your file is app/routes.py) ---
from app.routes import router as api_router
app.include_router(api_router, prefix="/api")
