from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import Optional

from app.models import AnalyzeRequest, RiskReport, RiskFinding
from app.api.helpers import scams, log_event, redact

router = APIRouter()

@router.post("/analyze", response_model=RiskReport)
async def analyze_text(req: AnalyzeRequest):
    if not (req.text or req.urls):
        raise HTTPException(400, "Provide 'text' or 'urls'.")

    text = req.text or " ".join(req.urls or [])
    res = scams.analyze_text(text, lang_hint=req.lang_hint)
    findings = [RiskFinding(**f) for f in res.get("findings", [])]

    try:
        log_event(
            "analyze",
            lang=res.get("language", "en"),
            score=res.get("score"),
            risk_level=res.get("risk_level"),
            findings=len(findings),
            text=redact(text)[:400],
        )
    except Exception:
        pass

    return RiskReport(
        risk_level=res.get("risk_level", "low"),
        score=res.get("score", 0),
        findings=findings,
        language=res.get("language", "en"),
    )