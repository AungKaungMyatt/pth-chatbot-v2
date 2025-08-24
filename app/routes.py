# app/routes.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from typing import Optional
import time

from app.models import ChatRequest, ChatResponse, Reasoning, AnalyzeRequest, RiskReport, RiskFinding
from app.engine.rule_engine import RuleEngine
from app.engine.scam_detector import ScamDetector
from app.engine.fallback import AIFallback
from app.nlp.redactor import redact
from app.utils.logger import log_event, tail_jsonl

from pydantic import BaseModel

router = APIRouter()
rules = RuleEngine("data/knowledge.json")
scams = ScamDetector()
ai = AIFallback()


class TraceReq(BaseModel):
    message: str
    lang_hint: Optional[str] = None
    top_k: int = 8


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    t0 = time.perf_counter()

    # ---- Rule-based first
    m = rules.match(req.message, lang_hint=req.lang_hint)
    reply = m.get("answer") or ""
    conf = float(m.get("confidence", 0.0))
    lang = m.get("language", "en")
    used_ai = False

    # ---- Scope guard: personal account actions should always return the canned scope-safe answer
    if m.get("intent") == "personal_account_scope":
        reply = m["answer"]

    # ---- AI fallback (more generous threshold + error logging)
    # Was 0.45 in your file; bumping to 0.55 so borderline cases can still help users while you improve rules.
    if conf < 0.55 and req.allow_ai_fallback:
        try:
            ai_ans = await ai.answer(req.message, lang)
        except Exception as e:
            # surface the issue in logs; do not crash the request
            try:
                log_event("chat_ai_error", error=str(e)[:400])
            except Exception:
                pass
            ai_ans = None

        if ai_ans:
            reply = ai_ans
            used_ai = True

    # ---- Safe default if still empty (never return a blank reply)
    if not reply:
        reply = (
            "I can’t fully confirm this from my rules. "
            "Please use your bank’s official app/website or hotline for account actions. "
            "If this looks like a scam, don’t click links or share codes."
            if lang == "en"
            else "စည်းမျဉ်းအခြေပြု အဖြေမရှိသေးပါ။ ကိုယ်ရေးအကောင့်ဆိုင်ရာလုပ်ဆောင်ချက်များအတွက် "
                 "ဘဏ်၏ တရားဝင် App/Website သို့မဟုတ် Hotline ကိုသာ သုံးပါ။ "
                 "လိမ်လည်မှုဖြစ်နိုင်ပါက Link မနှိပ်ပါနှင့်၊ OTP/PIN မမျှဝေပါနှင့်။"
        )

    # ---- Safety banner + redaction (keep your behavior)
    banner = (
        "\n\n**Note:** I’m an educational assistant, not your bank. "
        "Never share OTP/PIN. For account matters, contact your bank directly."
    )
    if lang == "my":
        banner = (
            "\n\n**မှတ်ချက်:** ငါသည် အကြံပညာပေးကူညီရေးဝန်ဆောင်မှုဖြစ်ပြီး သင့်ဘဏ်မဟုတ်ပါ။ "
            "OTP/PIN မမျှဝေပါနှင့်။ ကိုယ်ရေးအကောင့်ဆိုင်ရာအတွက် ဘဏ်နှင့်တိုက်ရိုက်ဆက်သွယ်ပါ။"
        )

    safe_reply = redact(reply) + banner

    # ---- LOG (keep your structured fields)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    try:
        log_event(
            "chat",
            duration_ms=duration_ms,
            lang=lang,
            intent=m.get("intent"),
            confidence=round(conf, 3),
            matched=m.get("matched"),
            used_ai=used_ai,
            allow_ai=req.allow_ai_fallback,
            msg=redact(req.message),
            reply_preview=safe_reply[:220],
        )
    except Exception:
        pass

    return ChatResponse(
        reply=safe_reply,
        language=lang,
        reasoning=Reasoning(
            intent=m.get("intent"),
            confidence=conf,
            matched=m.get("matched"),
            safety_notes=m.get("safety_notes", []),
        ),
    )


@router.post("/analyze", response_model=RiskReport)
async def analyze_text(req: AnalyzeRequest):
    if not (req.text or req.urls):
        raise HTTPException(400, "Provide 'text' or 'urls'.")
    text = req.text or " ".join(req.urls or [])
    res = scams.analyze_text(text, lang_hint=req.lang_hint)
    findings = [RiskFinding(**f) for f in res["findings"]]

    # LOG
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
        risk_level=res["risk_level"],
        score=res["score"],
        findings=findings,
        language=res["language"],
    )


@router.post("/upload", response_model=RiskReport)
async def upload_file(file: UploadFile = File(...)):
    # OCR is optional; if not available, we proceed with empty text safely
    try:
        import pytesseract
        from PIL import Image
        import io

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
            log_event("upload", note="no_text_extracted", filename=file.filename)
        except Exception:
            pass
        return out

    res = scams.analyze_text(text)
    findings = [RiskFinding(**f) for f in res["findings"]]

    try:
        log_event(
            "upload",
            lang=res.get("language", "en"),
            score=res.get("score"),
            risk_level=res.get("risk_level"),
            findings=len(findings),
            filename=file.filename,
        )
    except Exception:
        pass

    return RiskReport(
        risk_level=res["risk_level"],
        score=res["score"],
        findings=findings,
        language=res["language"],
    )


# ---------- Admin/debug ----------

@router.get("/admin/logs/tail")
async def logs_tail(n: int = Query(200, ge=1, le=1000)):
    """
    Get the latest N log events (redacted).
    """
    return {"events": tail_jsonl(n)}


@router.post("/admin/trace")
async def trace(req: TraceReq):
    """
    See how the rule engine scores your message across intents.
    Useful to debug mismatches (why intent X didn't win).
    """
    out = rules.trace(req.message, lang_hint=req.lang_hint, top_k=req.top_k)
    # also log that a trace was requested (no message content, only length)
    try:
        log_event("trace", msg_len=len(req.message), lang=out.get("language"))
    except Exception:
        pass
    return out