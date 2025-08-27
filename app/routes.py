# app/routes.py
from __future__ import annotations
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from typing import Optional
import time
import os
import io
from collections import defaultdict, deque

from pydantic import BaseModel

# ---- Safe logging shim (project may not have app.utils.logger) ----
try:
    from app.utils.logger import log_event, tail_jsonl
except Exception:
    def log_event(*args, **kwargs):  # type: ignore
        pass
    def tail_jsonl(n: int):  # type: ignore
        return []

# ---- External deps (OpenAI optional at runtime) ----
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

# ---- Project models/engines ----
from app.models import (
    ChatRequest, ChatResponse, Reasoning,
    AnalyzeRequest, RiskReport, RiskFinding
)
from app.engine.rule_engine import RuleEngine, scope_check
from app.engine.scam_detector import ScamDetector
from app.engine.fallback import AIFallback
from app.nlp.redactor import redact
from app.nlp.lang import detect_language

# ---------------- SETUP ----------------
router = APIRouter()
rules = RuleEngine("data/knowledge.json")
scams = ScamDetector()
ai = AIFallback()

# ---------------- CONSTANT TEXT ----------------
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

_BANNER_EN = (
    "\n\n**Note:** I’m an educational assistant, not your bank. "
    "Never share OTP/PIN. For account matters, contact your bank directly."
)
_BANNER_MY = (
    "\n\n**မှတ်ချက်:** ပစ်တိုင်းထောင်သည် ပညာပေးအတွက်သာ ဖြစ်ပြီး သင့်ဘဏ်မဟုတ်ပါ။ "
    "OTP/PIN မမျှဝေပါနှင့်။ အကောင့်ဆိုင်ရာအတွက် တရားဝင် App/Website သို့မဟုတ် Hotline ကိုသာ သုံးပါ။"
)

_SYSTEM_PROMPT = (
    "You are Pyit Tine Htaung, a Myanmar banking cybersecurity guide. "
    "Answer ONLY banking/cybersecurity questions; if out of scope, refuse briefly and redirect. "
    "Never request/accept OTP/PIN or credentials. "
    "Always answer in the language requested by the client (lang_hint), defaulting to English if not provided."
)

# ---------------- OpenAI client (lazy) ----------------
def _get_openai_client():
    if OpenAI is None:
        return None
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        return OpenAI(api_key=key)
    except Exception:
        return None

_OPENAI_MODEL = os.environ.get("AT_MODEL", "gpt-4o-mini")

# ---------------- FOLLOW-UP FLOWS (generic) ----------------
# In-memory session: topic, step, lang, short history
_SESS = defaultdict(lambda: {"topic": None, "step": 0, "lang": "en", "hist": deque(maxlen=6)})

FOLLOWUP_MARKERS = {
    "en": [
        "i already did", "i did that", "i did those", "done", "still not", "cant find", "can't find",
        "next", "what next", "go on", "continue", "step", "next step", "not working", "didn’t work", "didn't work"
    ],
    "my": ["လုပ်ပြီးပြီ", "ပြီးပြီ", "မရသေး", "နောက်", "နောက်အဆင့်", "မအောင်မြင်သေး", "မရဘူး"]
}

def _is_followup(text: str, lang: str) -> bool:
    t = text.lower()
    return any(k in t for k in FOLLOWUP_MARKERS.get(lang, []))

def _answer_for(intent: str, lang: str) -> str:
    # fetch localized answer from rules
    try:
        return rules.answer_for(intent, lang)
    except Exception:
        return ""


# ---------- Models used by admin/trace ----------
class TraceReq(BaseModel):
    message: str
    lang_hint: Optional[str] = None
    top_k: int = 8


# ==================================================
# ===============   STREAMING CHAT   ===============
# ==================================================
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    # ---- Scope gate (soft) ----
    in_scope, lang_gate, scope_reason, scope_tag = scope_check(req.message, rules=rules)
    if not in_scope:
        msg = OUT_OF_SCOPE_MY if lang_gate == "my" else OUT_OF_SCOPE_EN
        banner = _BANNER_MY if lang_gate == "my" else _BANNER_EN
        return StreamingResponse(iter([msg + banner]), media_type="text/plain")

    # If sensitive (balance/transfer/etc), return polite redirect immediately
    if scope_tag == "sensitive":
        lang = detect_language(req.message, hint=getattr(req, "lang_hint", None))
        text = _answer_for("personal_account_scope", lang) or _answer_for("personal_account_scope", "en")
        if not text:
            text = OUT_OF_SCOPE_MY if lang == "my" else OUT_OF_SCOPE_EN
        banner = _BANNER_MY if lang == "my" else _BANNER_EN
        return StreamingResponse(iter([text + banner]), media_type="text/plain")

    t0 = time.perf_counter()
    lang = detect_language(req.message, hint=getattr(req, "lang_hint", None))

    # Session & follow-up handling
    sess_key = getattr(req, "session_id", None) or request.headers.get("x-forwarded-for") or (request.client.host if request.client else "anon")
    sess = _SESS[sess_key]
    sess["lang"] = lang
    sess["hist"].append(("user", req.message))

    # Rule match
    m = rules.match(req.message, lang_hint=lang)
    intent = m.get("intent")

    # If flow defined → step through on follow-up
    if intent and m.get("flow"):
        steps = m["flow"]
        if _is_followup(req.message, lang) and sess.get("topic") == intent:
            sess["step"] = min(sess.get("step", 0) + 1, len(steps) - 1)
        else:
            sess["topic"] = intent
            sess["step"] = 0
        idx = sess["step"]
        text = steps[idx]
        if idx == len(steps) - 1 and m.get("escalation"):
            text += "\n\n" + m["escalation"]
        text += "\n\n" + ("Say 'done' when finished." if lang == "en" else "ပြီးရင် 'ပြီးပြီ' လို့ ပြောပါ။")
        sess["hist"].append(("assistant", text))
        banner = _BANNER_MY if lang == "my" else _BANNER_EN
        return StreamingResponse(iter([text + banner]), media_type="text/plain")

    # If no flow but follow-up → ask AI to continue the SAME scenario (1–2 actions)
    if intent and _is_followup(req.message, lang) and req.allow_ai_fallback:
        system = (
            "You are a banking/cybersecurity assistant. Continue the SAME troubleshooting scenario. "
            "Do not restart from step 1 unless the user asked to reset. "
            "Return 1–2 next actions only, concise and specific. Never ask for OTP/PIN. Language: {lang}."
        ).format(lang=lang)
        try:
            cont = await ai.answer_with_system(system=system, user_text=req.message, lang=lang, context=list(sess["hist"]))
        except Exception:
            cont = None
        if cont:
            sess["hist"].append(("assistant", cont))
            banner = _BANNER_MY if lang == "my" else _BANNER_EN
            return StreamingResponse(iter([cont + banner]), media_type="text/plain")

    # Otherwise stream from model (fallback)
    client = _get_openai_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"[language:{lang}] {req.message}"},
    ]
    max_tokens = int(os.environ.get("MAX_TOKENS", "900"))

    async def gen():
        try:
            if client is None:
                # No API key or client → graceful local reply
                yield (m.get("answer") or "I’ll share general guidance only. If this involves your personal account, use the official bank app/website.")  # noqa: E501
            else:
                stream = client.chat.completions.create(
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
            yield _BANNER_MY if lang == "my" else _BANNER_EN

        except Exception as e:
            yield f"\n\n[error] {str(e)}"
        finally:
            try:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_event("chat_stream", duration_ms=duration_ms, lang=lang, scope=scope_reason)
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/plain")


# ==================================================
# ===============   NON-STREAM CHAT  ===============
# ==================================================
@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    t0 = time.perf_counter()

    # ---------- 0) Scope ----------
    in_scope, lang_gate, scope_reason, scope_tag = scope_check(req.message, rules=rules)
    if not in_scope:
        banner = _BANNER_MY if lang_gate == "my" else _BANNER_EN
        return ChatResponse(
            reply=(OUT_OF_SCOPE_MY if lang_gate == "my" else OUT_OF_SCOPE_EN) + banner,
            language=lang_gate,
            reasoning=Reasoning(intent="out_of_scope", confidence=1.0, matched="", safety_notes=[]),
        )

    # ---------- 1) Language ----------
    lang = detect_language(req.message, hint=getattr(req, "lang_hint", None))

    # ---------- 2) Sensitive redirect (balance/transfer/etc) ----------
    if scope_tag == "sensitive":
        text = _answer_for("personal_account_scope", lang) or _answer_for("personal_account_scope", "en")
        if not text:
            text = OUT_OF_SCOPE_MY if lang == "my" else OUT_OF_SCOPE_EN
        banner = _BANNER_MY if lang == "my" else _BANNER_EN
        safe_reply = redact(text) + banner
        return ChatResponse(
            reply=safe_reply,
            language=lang,
            reasoning=Reasoning(intent="personal_account_scope", confidence=0.99, matched="scope_sensitive", safety_notes=[]),
        )

    # ---------- 3) Session & Follow-up ----------
    sess_key = getattr(req, "session_id", None) or request.headers.get("x-forwarded-for") or (request.client.host if request.client else "anon")
    sess = _SESS[sess_key]
    sess["lang"] = lang
    is_fup = _is_followup(req.message, lang)
    sess["hist"].append(("user", req.message))

    # Quick reset hook
    lower_msg = req.message.lower().strip()
    if lower_msg in {"reset", "restart"} or (lang == "my" and lower_msg in {"ပြန်စ", "အစပြန်"}):
        _SESS[sess_key] = {"topic": None, "step": 0, "lang": lang, "hist": deque(maxlen=6)}
        reply = ("Context cleared. Tell me the issue again." if lang == "en"
                 else "အကြောင်းအရာကို ရှင်းလင်းပြီး ပြန်စတင်ပါ။ ပြန်၍ ပြောပြပါ။")
        banner = _BANNER_MY if lang == "my" else _BANNER_EN
        return ChatResponse(
            reply=reply + banner,
            language=lang,
            reasoning=Reasoning(intent="reset", confidence=1.0, matched="", safety_notes=[]),
        )

    # ---------- 4) Rule engine ----------
    m = rules.match(req.message, lang_hint=lang)
    intent = m.get("intent")
    reply = ""
    conf = float(m.get("confidence", 0.0))
    used_ai = False

    if intent:
        steps = m.get("flow")
        # New topic or topic switch resets step to 0
        if not is_fup or sess.get("topic") != intent:
            sess["topic"] = intent
            sess["step"] = 0

        if steps:
            # Step through a predefined flow
            if is_fup:
                sess["step"] = min(sess["step"] + 1, len(steps) - 1)
            idx = sess["step"]
            reply = steps[idx]
            if idx == len(steps) - 1 and m.get("escalation"):
                reply += "\n\n" + (m["escalation"] or "")
            reply += "\n\n" + ("Say 'done' when finished." if lang == "en" else "ပြီးရင် 'ပြီးပြီ' လို့ ပြောပါ။")
            conf = max(conf, 0.9)
        else:
            # No flow defined → AI continuation for follow-ups
            if is_fup and req.allow_ai_fallback:
                system = (
                    "You are a banking/cybersecurity assistant. Continue the SAME troubleshooting scenario. "
                    "Do not restart from step 1. Return 1–2 next actions only. Never ask for OTP/PIN. Language: {lang}."
                ).format(lang=lang)
                try:
                    ai_ans = await ai.answer_with_system(system=system, user_text=req.message, lang=lang, context=list(sess["hist"]))
                except Exception:
                    ai_ans = None
                if ai_ans:
                    reply = ai_ans
                    conf = max(conf, 0.7)
                    used_ai = True

    # If still empty, use rule's short answer then normal AI fallback
    if not reply:
        reply = m.get("answer") or ""
    if (not reply) and req.allow_ai_fallback:
        try:
            ai_ans = await ai.answer(req.message, lang)
        except Exception:
            ai_ans = None
        if ai_ans:
            reply = ai_ans
            used_ai = True

    # Default safety reply
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

    # ---------- 5) Safety banner ----------
    banner = _BANNER_MY if lang == "my" else _BANNER_EN
    safe_reply = redact(reply) + banner

    # Save assistant reply into short history
    if safe_reply:
        sess["hist"].append(("assistant", safe_reply))

    # ---------- 6) LOG ----------
    duration_ms = int((time.perf_counter() - t0) * 1000)
    try:
        log_event(
            "chat",
            duration_ms=duration_ms,
            lang=lang,
            intent=intent,
            confidence=round(conf, 3),
            matched=m.get("matched"),
            used_ai=used_ai,
            allow_ai=req.allow_ai_fallback,
            scope=scope_reason,
            msg=redact(req.message),
            reply_preview=safe_reply[:220],
        )
    except Exception:
        pass

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


# ==================================================
# ===============   ANALYZE   ======================
# ==================================================
@router.post("/analyze", response_model=RiskReport)
async def analyze_text(req: AnalyzeRequest):
    if not (req.text or req.urls):
        raise HTTPException(400, "Provide 'text' or 'urls'.")
    text = req.text or " ".join(req.urls or [])
    res = scams.analyze_text(text, lang_hint=req.lang_hint)
    findings = [RiskFinding(**f) for f in res.get("findings", [])]

    try:
        log_event("analyze", lang=res.get("language", "en"),
                  score=res.get("score"), risk_level=res.get("risk_level"),
                  findings=len(findings), text=redact(text)[:400])
    except Exception:
        pass

    return RiskReport(
        risk_level=res.get("risk_level", "low"),
        score=res.get("score", 0),
        findings=findings,
        language=res.get("language", "en"),
    )


# ==================================================
# ===============   UPLOAD   =======================
# ==================================================
@router.post("/upload", response_model=RiskReport)
async def upload_file(file: UploadFile = File(...)):
    try:
        import pytesseract
        from PIL import Image
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
        log_event("upload", lang=res.get("language", "en"),
                  score=res.get("score"), risk_level=res.get("risk_level"),
                  findings=len(findings), filename=getattr(file, "filename", "unknown"))
    except Exception:
        pass

    return RiskReport(
        risk_level=res.get("risk_level", "low"),
        score=res.get("score", 0),
        findings=findings,
        language=res.get("language", "en"),
    )


# ===============   ADMIN   ========================
@router.get("/admin/logs/tail")
async def logs_tail(n: int = Query(200, ge=1, le=1000)):
    return {"events": tail_jsonl(n)}

@router.post("/admin/trace")
async def trace(req: TraceReq):
    out = rules.trace(req.message, lang_hint=req.lang_hint, top_k=req.top_k)
    try:
        log_event("trace", msg_len=len(req.message), lang=out.get("language"))
    except Exception:
        pass
    return out
