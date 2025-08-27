from fastapi import APIRouter

# Optional aggregator if you prefer a single include in main.py
from . import chat, analyze, upload, admin

api_router = APIRouter()
api_router.include_router(chat.router)
api_router.include_router(analyze.router)
api_router.include_router(upload.router)
api_router.include_router(admin.router)