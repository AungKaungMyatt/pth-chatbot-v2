from fastapi import APIRouter, UploadFile, File, HTTPException
from app.models import (
    ChatRequest, ChatResponse, Reasoning,
    AnalyzeRequest, RiskReport, RiskFinding
)
from app.engine.rule_engine import RuleEngine
from app.engine.scam_detector import ScamDetector
from app.engine.fallback import AIFallback
from app.nlp.redactor import redact

router = APIRouter()

# Singletons
rules = RuleEngine("data/knowledge.json")
scams = ScamDetector()
ai = AIFallback()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # 1) Rule match
    m = rules.match(req.message, lang_hint=req.lang_hint)
    reply = m.get("answer") or ""
    conf = float(m.get("confidence", 0.0))
    lang = m.get("language", "en")
    intent = m.get("intent")

    # 2) Scope guard — personal account topics must redirect to bank
    if intent == "personal_account_scope":
        # Always use the KB's safe answer; never AI-fallback here.
        reply = m.get("answer") or reply
    else:
        # 3) AI fallback if rules are weak OR no answer produced
        if req.allow_ai_fallback and (conf < 0.45 or not reply):
            try:
                ai_ans = await ai.answer(req.message, lang)
            except Exception:
                ai_ans = None
            if ai_ans:
                reply = ai_ans

    # 4) Last-resort default if still empty
    if not reply:
        reply = (
            "Please rephrase your question."
            if lang == "en"
            else "ကျေးဇူးပြု၍ မေးခွန်းကို ပြန်လည်ဖော်ပြပေးပါ။"
        )

    # 5) Safety banner (always)
    banner = (
        "\n\n**Note:** I’m an educational assistant, not your bank. "
        "Never share OTP/PIN. For account matters, contact your bank directly."
    )
    if lang == "my":
        banner = (
            "\n\n**မှတ်ချက်:** ငါသည် ပညာပေးအကြံပေးကူညီရေးဝန်ဆောင်မှုဖြစ်ပြီး သင့်ဘဏ်မဟုတ်ပါ။ "
            "OTP/PIN မမျှဝေပါနှင့်။ ကိုယ်ရေးအကောင့်ဆိုင်ရာအတွက် ဘဏ်နှင့်တိုက်ရိုက်ဆက်သွယ်ပါ။"
        )

    safe_reply = redact(reply) + banner

    return ChatResponse(
        reply=safe_reply,
        language=lang,
        reasoning=Reasoning(
            intent=intent,
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
    return RiskReport(
        risk_level=res["risk_level"],
        score=res["score"],
        findings=findings,
        language=res["language"],
    )


@router.post("/upload", response_model=RiskReport)
async def upload_file(file: UploadFile = File(...)):
    # Try OCR if available (image files)
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        import io
        content = await file.read()
        img = Image.open(io.BytesIO(content))
        text = pytesseract.image_to_string(img)
    except Exception:
        # Graceful fallback: treat as no text extracted
        text = ""

    if not text:
        return RiskReport(
            risk_level="low",
            score=0,
            findings=[RiskFinding(rule="ocr", detail="No text extracted or OCR disabled")],
            language="en",
        )

    res = scams.analyze_text(text)
    findings = [RiskFinding(**f) for f in res["findings"]]
    return RiskReport(
        risk_level=res["risk_level"],
        score=res["score"],
        findings=findings,
        language=res["language"],
    )


@router.post("/admin/reload")
def admin_reload():
    """Reload knowledge.json from disk without restarting the server."""
    rules.reload()
    return {"ok": True, "entries": len(rules.entries)}


@router.get("/admin/ai_status")
def ai_status():
    """Expose whether AI fallback is enabled and which model is set."""
    return {
        "enabled": bool(getattr(ai, "enabled", False)),
        "model": getattr(ai, "model", None),
    }