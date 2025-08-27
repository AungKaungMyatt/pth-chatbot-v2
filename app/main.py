# app/main.py
import os
import time
import logging
import shutil

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

APP_VERSION = os.getenv("API_VERSION", "2.0.0")

app = FastAPI(title="Pyit Tine Htaung API", version=APP_VERSION)

# --- CORS: Netlify prod + local dev + Netlify previews ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pyittinehtaung.netlify.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    # allow any Netlify preview environment for this site
    allow_origin_regex=r"^https:\/\/([a-z0-9-]+--)?pyittinehtaung\.netlify\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# --- timing + safe 500s ---
@app.middleware("http")
async def timing_and_errors(request: Request, call_next):
    start = time.time()
    try:
        resp = await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    resp.headers["X-Process-Time-ms"] = f"{(time.time() - start) * 1000:.2f}"
    return resp

# --- health endpoints ---
@app.get("/")
def root():
    return {"ok": True, "service": "pth-chatbot-api", "version": APP_VERSION}

@app.get("/healthz")
def health():
    return {
        "ok": True,
        "service": "pth-chatbot-api",
        "version": APP_VERSION,
        "tesseract_found": bool(shutil.which("tesseract")),
    }

# --- mount routers (Option A: explicit, per-feature) ---
from app.api import chat, analyze, upload, admin

app.include_router(chat.router,    prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(upload.router,  prefix="/api")
app.include_router(admin.router,   prefix="/api")