# app/main.py
import os
import time
import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# ---- Logging (shows in Render logs) -----------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

# ---- App --------------------------------------------------------------------
app = FastAPI(title="Pyit Tine Htaung API", version="2.0.0")

# ---- CORS (Netlify + local dev + Netlify previews) --------------------------
# Your public frontend:
NETLIFY_SITE = "https://pyittinehtaung.netlify.app"

ALLOWED_ORIGINS = [
    NETLIFY_SITE,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Allow Netlify branch/preview deploys too (e.g., my-branch--site.netlify.app)
    allow_origin_regex=r"^https:\/\/([a-z0-9-]+--)?pyittinehtaung\.netlify\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,  # set True only if you use cookies/session auth
)

# ---- Simple middleware (timing + safe 500) ----------------------------------
@app.middleware("http")
async def timing_and_errors(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa
        logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    response.headers["X-Process-Time-ms"] = f"{(time.time() - start)*1000:.2f}"
    return response

# ---- Health & root (for Render health checks) --------------------------------
@app.get("/", tags=["meta"])
def root():
    return {"ok": True, "service": "pth-chatbot-api", "version": "2.0.0"}

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "healthy"}

# ---- Optional: lightweight admin trace endpoint ------------------------------
@app.get("/admin/trace", tags=["meta"])
def admin_trace(q: Optional[str] = None):
    # You can replace this with your real trace/view logic later
    return {"trace": "enabled", "q": q}

# ---- Include your actual API routes if you have them -------------------------
# If you already have routers (e.g., app/api.py with `router = APIRouter()`), include them:
try:
    from app.api import router as api_router  # adjust path if yours differs
    app.include_router(api_router, prefix="/api")
    logger.info("Included router from app.api")
except Exception as e:  # noqa
    logger.info("No app.api router included (%s). Using meta endpoints only.", e)
