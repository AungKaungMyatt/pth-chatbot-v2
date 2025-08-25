from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
import time
import os

from pydantic import BaseModel
from openai import OpenAI

from app.models import (
    ChatRequest, ChatResponse, Reasoning,
    AnalyzeRequest, RiskReport, RiskFinding
)
from app.engine.rule_engine import RuleEngine
from app.engine.scam_detector import ScamDetector
from app.engine.fallback import AIFallback
from app.nlp.redactor import redact
from app.utils.logger import log_event, tail_jsonl
from app.engine.rule_engine import scope_check
from app.nlp.lang import is_burmese

router = APIRouter()
rules = RuleEngine("data/knowledge.json")
scams = ScamDetector()
ai = AIFallback()

OUT_OF_SCOPE_EN = (
    "I’m focused on **banking** and **cybersecurity** only "
    "(phishing/smishing/vishing, OTP/PIN safety, mobile wallets/QR scams, "
    "SIM swap, card & account security). Please ask within those topics."
)
OUT_OF_SCOPE_MY = (
    "ပစ်တိုင်းထောင်သည် **ဘဏ်လုပ်ငန်းနှင့် ဆိုက်ဘာလုံခြုံရေး** ဆိုင်ရာ မေးခွန်းများအတွက်သာ ဖြစ်သည်။ "
    "ဥပမာ — phishing/လိမ်လည်မှု၊ OTP/PIN လုံခြုံရေး၊ မိုဘိုင်းပိုက်ဆက်/QR လိမ်လည်မှု၊ "
    "SIM swap၊ ကတ်/အကောင့် လုံခြုံရေး စသဖြင့် မေးပါ။"
)

# ---------- Models used by admin/trace ----------
class TraceReq(BaseModel):
    message: str
    lang_hint: Optional[str] = None
    top_k: int = 8

# ===============   STREAMING CHAT   ===================
# New: stream the assistant reply so long answers don't time out.
# Frontend: call POST /api/chat/stream and read the response body as a stream.

_OPENAI_MODEL = os.environ.get("AT_MODEL", "gpt-4o-mini")
_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

_SYSTEM_PROMPT = (
    "You are Pyit Tine Htaung, a Myanmar banking cybersecurity guide. "
    "STRICTLY answer only banking or cybersecurity questions; if out of scope, "
    "refuse briefly and redirect to allowed topics. Never request/accept OTP/PIN "
    "or account credentials. If the user writes Burmese, answer in Burmese."
)

_BANNER_EN = (
    "\n\n**Note:** I’m an educational assistant, not your bank. "
    "Never share OTP/PIN. For account matters, contact your bank directly."
)
_BANNER_MY = (
    "\n\n**မှတ်ချက်:** ပစ်တိုင်းထောင်သည် ပညာပေးအတွက်သာ ဖြစ်ပြီး သင့်ဘဏ်မဟုတ်ပါ။ "
    "OTP/PIN မမျှဝေပါနှင့်။ အကောင့်ဆိုင်ရာအတွက် တရားဝင် App/Website သို့မဟုတ် "
    "Hotline ကိုသာ သုံးပါ။"
)

@router.post("/chat/stream")
def chat_stream(req: ChatRequest):
    """
    Streamed chat response. Media type is text/plain so the frontend
    can read chunks via ReadableStream. We append the safety banner at the end.
    """
    # ---- HARD-LIMIT (one scope check at the very top)
    in_scope, lang_gate = scope_check(req.message)
    if not in_scope:
        msg = OUT_OF_SCOPE_MY if lang_gate == "my" else OUT_OF_SCOPE_EN
        banner = _BANNER_MY if lang_gate == "my" else _BANNER_EN
        # Stream the refusal line (optionally with banner)
        return StreamingResponse(iter([msg + banner]), media_type="text/plain")

    t0 = time.perf_counter()

    # Build the OpenAI message list
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": req.message},
    ]

    max_tokens = int(os.environ.get("MAX_TOKENS", "900"))

    def gen():
        try:
            stream = _client.chat.completions.create(
                model=_OPENAI_MODEL,
                messages=messages,
                temperature=0.2,
                max_tokens=max_tokens,
                stream=True,
            )
            for event in stream:
                delta = event.choices[0].delta.content or ""
                if delta:
                    yield delta
            # After the model finishes, append your banner in the user’s language
            yield _BANNER_MY if is_burmese(req.message) else _BANNER_EN

        except Exception as e:
            # Send a readable error at the end of the stream
            yield f"\n\n[error] {str(e)}"

        finally:
            try:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_event("chat_stream", duration_ms=duration_ms, msg=redact(req.message)[:400])
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/plain")

# ===============   NON-STREAM CHAT   ==================
@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    t0 = time.perf_counter()
        # --- HARD-LIMIT: Banking + Cybersecurity only ---
    in_scope, lang_gate = scope_check(req.message)
    if not in_scope:
        banner = _BANNER_MY if lang_gate == "my" else _BANNER_EN
        return ChatResponse(
            reply=(OUT_OF_SCOPE_MY if lang_gate == "my" else OUT_OF_SCOPE_EN) + banner,
            language=lang_gate,
            reasoning=Reasoning(
                intent="out_of_scope",
                confidence=1.0,
                matched="",
                safety_notes=[],
            ),
        )

    # ---- Rule-based first
    m = rules.match(req.message, lang_hint=req.lang_hint)
    reply = m.get("answer") or ""
    conf = float(m.get("confidence", 0.0))
    lang = m.get("language", "en")
    used_ai = False

    # ---- Scope guard
    if m.get("intent") == "personal_account_scope":
        reply = m["answer"]

    # ---- AI fallback (slightly more generous threshold)
    if conf < 0.55 and req.allow_ai_fallback:
        try:
            ai_ans = await ai.answer(req.message, lang)
        except Exception as e:
            try:
                log_event("chat_ai_error", error=str(e)[:400])
            except Exception:
                pass
            ai_ans = None

        if ai_ans:
            reply = ai_ans
            used_ai = True

    # ---- Safe default if still empty
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

    # ---- Safety banner + redaction
    banner = _BANNER_MY if lang == "my" else _BANNER_EN
    safe_reply = redact(reply) + banner

    # ---- LOG
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

# ==================   ANALYZE   =======================
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

# ==================   UPLOAD   ========================
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

# ==================   ADMIN   =========================
@router.get("/admin/logs/tail")
async def logs_tail(n: int = Query(200, ge=1, le=1000)):
    """Get the latest N log events (redacted)."""
    return {"events": tail_jsonl(n)}

@router.post("/admin/trace")
async def trace(req: TraceReq):
    """Score a message across intents to debug rule matches."""
    out = rules.trace(req.message, lang_hint=req.lang_hint, top_k=req.top_k)
    try:
        log_event("trace", msg_len=len(req.message), lang=out.get("language"))
    except Exception:
        pass
    return out
