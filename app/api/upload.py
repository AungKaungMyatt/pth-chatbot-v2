from __future__ import annotations
from fastapi import APIRouter, UploadFile, File
import io

from app.models import RiskReport, RiskFinding
from app.api.helpers import scams, log_event

router = APIRouter()

@router.post("/upload", response_model=RiskReport)
async def upload_file(file: UploadFile = File(...)):
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        content = await file.read()
        img = Image.open(io.BytesIO(content))
        text = pytesseract.image_to_string(img)
    except Exception:
        text = ""

    if not text:
        out = RiskReport(
            risk_level="low",
            score=0,
            findings=[RiskFinding(rule="ocr", detail="No text extracted or OCR disabled")],
            language="en",
        )
        try:
            log_event("upload", note="no_text_extracted", filename=getattr(file, "filename", "unknown"))
        except Exception:
            pass
        return out

    res = scams.analyze_text(text)
    findings = [RiskFinding(**f) for f in res.get("findings", [])]
    try:
        log_event(
            "upload",
            lang=res.get("language", "en"),
            score=res.get("score"),
            risk_level=res.get("risk_level"),
            findings=len(findings),
            filename=getattr(file, "filename", "unknown"),
        )
    except Exception:
        pass

    return RiskReport(
        risk_level=res.get("risk_level", "low"),
        score=res.get("score", 0),
        findings=findings,
        language=res.get("language", "en"),
    )