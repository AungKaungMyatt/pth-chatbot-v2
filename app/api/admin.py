from __future__ import annotations
from fastapi import APIRouter, Query

from app.api.helpers import rules, tail_jsonl, log_event

router = APIRouter()

@router.get("/admin/logs/tail")
async def logs_tail(n: int = Query(200, ge=1, le=1000)):
    return {"events": tail_jsonl(n)}

@router.post("/admin/trace")
async def trace(req: dict):
    message = str(req.get("message", ""))
    lang_hint = req.get("lang_hint")
    top_k = int(req.get("top_k", 8))
    out = rules.trace(message, lang_hint=lang_hint, top_k=top_k)
    try:
        log_event("trace", msg_len=len(message), lang=out.get("language"))
    except Exception:
        pass
    return out