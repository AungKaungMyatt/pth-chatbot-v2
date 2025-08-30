# app/api/chat.py
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from collections import deque
from typing import List
import os, time, re

from app.engine.password_strength import (
    is_password_question, extract_password_candidates,
    format_assessment, wants_examples, generate_examples
)

from app.models import ChatRequest, ChatResponse, Reasoning
from app.api.helpers import (
    rules, ai, redact, detect_language,
    out_of_scope, sensitive_redirect, sess_key, is_followup,
    rewrite_with_ai, get_openai_client, SYSTEM_PROMPT, OPENAI_MODEL,
    SESS, log_event,
)

# --- scams detector (via helpers or local) ---
try:
    from app.api.helpers import scams  # type: ignore
except Exception:
    from app.engine.scam_detector import ScamDetector
    scams = ScamDetector()

router = APIRouter()

# ---- URL detector for quick safety checks ----
URL_RE = re.compile(r"(?i)\b((?:https?://|http://|www\.)[^\s<>'\"()]+)")

# Heuristics: if a user is asking for “steps/procedure”, we trigger a flow.
STEP_HINTS_EN = ("how to", "steps", "procedure", "process", "what next", "next step", "guide", "instructions")
STEP_HINTS_MY = ("ဘယ်လိုလုပ်", "အဆင့်", "လုပ်နည်း", "လမ်းညွှန်", "စနစ်", "နောက်အဆင့်")

def wants_steps(msg: str, lang: str) -> bool:
    t = (msg or "").lower()
    if lang == "my":
        return any(h in t for h in STEP_HINTS_MY)
    return any(h in t for h in STEP_HINTS_EN)

# ======================================================================
# STREAMING
# ======================================================================
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    # ---- Scope gate ----
    from app.engine.rule_engine import scope_check
    in_scope, lang_gate, scope_reason, scope_tag = scope_check(req.message, rules=rules)
    if not in_scope:
        return StreamingResponse(iter([out_of_scope(lang_gate)]), media_type="text/plain")

    # Language
    lang = detect_language(req.message, hint=getattr(req, "lang_hint", None))

    # ---- Dynamic password strength check (runs BEFORE rules) ----
    if is_password_question(req.message, lang):
        cands = extract_password_candidates(req.message)
        if cands:
            text = "\n\n".join(format_assessment(pw, lang) for pw in cands)
        else:
            examples = generate_examples(3)
            if lang == "my":
                text = (
                    "စကားဝှက်အားကို စစ်ဆေးရန် စကားဝှက်ကို (“……”) အတွင်းရေးပေးပါ။ ဥပမာများ:\n"
                    + "\n".join(f"• {e}" for e in examples)
                )
            else:
                text = (
                    'To check strength, send your password in quotes (e.g., "MyP@ss..."). Examples:\n'
                    + "\n".join(f"• {e}" for e in examples)
                )
        return StreamingResponse(iter([text]), media_type="text/plain")

    # Optional: users ask for password EXAMPLES explicitly
    if wants_examples(req.message, lang):
        examples = generate_examples(3)
        text = "\n".join(f"{i+1}. {p}" for i, p in enumerate(examples))
        return StreamingResponse(iter([text]), media_type="text/plain")

    # ---- Sensitive redirect (no banner) ----
    if scope_tag == "sensitive":
        return StreamingResponse(iter([sensitive_redirect(lang)]), media_type="text/plain")

    # ---- Quick link-safety short-circuit ----
    lower_msg = (req.message or "").lower()
    if URL_RE.search(req.message) or (
        ("link" in lower_msg and "safe" in lower_msg)
        or ("လင့်" in lower_msg and ("အန္တရာယ်" in lower_msg or "လုံခြုံ" in lower_msg))
    ):
        res = scams.analyze_text(req.message, lang_hint=lang)
        if lang == "my":
            summary = "အန္တရာယ်အဆင့်: " + res["risk_level"] + "\n" + "\n".join(
                f"• {f['rule']}: {f['detail']}" for f in res["findings"][:5]
            )
        else:
            summary = "Risk level: " + res["risk_level"] + "\n" + "\n".join(
                f"• {f['rule']}: {f['detail']}" for f in res["findings"][:5]
            )
        out = redact(summary + ("\n\n" + res.get("advice", "") if res.get("advice") else ""))
        return StreamingResponse(iter([out]), media_type="text/plain")

    # ---- Session & history ----
    key = sess_key(req, request)
    sess = SESS[key]
    sess["lang"] = lang
    sess["hist"].append(("user", req.message))

    # ---- Rule match ----
    m = rules.match(req.message, lang_hint=lang)
    intent = m.get("intent")
    is_fup = is_followup(req.message, lang)

    # Prefer direct KB answer on first turn
    if intent and m.get("answer") and not is_fup:
        text = m["answer"]
        sess["hist"].append(("assistant", text))
        return StreamingResponse(iter([text]), media_type="text/plain")

    # Flow handling (multi-step guidance)
    if intent and m.get("flow"):
        steps: List[str] = m["flow"]
        if is_fup and sess.get("topic") == intent:
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
        return StreamingResponse(iter([text]), media_type="text/plain")

    # If no flow but follow-up, let AI continue the SAME scenario briefly
    if intent and is_fup and req.allow_ai_fallback:
        system = (
            "You are a banking/cybersecurity assistant. Continue the SAME troubleshooting scenario. "
            "Do not restart from step 1 unless the user asked to reset. "
            "Return 1–2 next actions only, concise and specific. Never ask for OTP/PIN. Language: {lang}."
        ).format(lang=lang)
        try:
            cont = await ai.answer_with_system(
                system=system, user_text=req.message, lang=lang, context=list(sess["hist"])
            )
        except Exception:
            cont = None
        if cont:
            sess["hist"].append(("assistant", cont))
            return StreamingResponse(iter([cont]), media_type="text/plain")

    # ---- General streaming fallback (model) ----
    client = get_openai_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"[language:{lang}] {req.message}"},
    ]
    max_tokens = int(os.environ.get("MAX_TOKENS", "900"))
    t0 = time.perf_counter()

    async def gen():
        try:
            if client is None:
                yield (
                    m.get("answer")
                    or "I’ll share general guidance only. If this involves your personal account, use the official bank app/website."
                )
            else:
                stream = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=max_tokens,
                    stream=True,
                )
                for event in stream:
                    delta = event.choices[0].delta.content or ""
                    if delta:
                        yield delta
        except Exception as e:
            yield f"\n\n[error] {str(e)}"
        finally:
            try:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_event("chat_stream", duration_ms=duration_ms, lang=lang, scope=scope_reason)
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/plain")

# ======================================================================
# NON-STREAM
# ======================================================================
@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    from app.engine.rule_engine import scope_check
    t0 = time.perf_counter()

    in_scope, lang_gate, scope_reason, scope_tag = scope_check(req.message, rules=rules)
    if not in_scope:
        return ChatResponse(
            reply=out_of_scope(lang_gate),
            language=lang_gate,
            reasoning=Reasoning(intent="out_of_scope", confidence=1.0, matched="", safety_notes=[]),
        )

    lang = detect_language(req.message, hint=getattr(req, "lang_hint", None))

    # Sensitive redirect (no banner)
    if scope_tag == "sensitive":
        return ChatResponse(
            reply=redact(sensitive_redirect(lang)),
            language=lang,
            reasoning=Reasoning(intent="personal_account_scope", confidence=0.99, matched="scope_sensitive", safety_notes=[]),
        )

    # 1) Quick link-safety short-circuit
    lower_msg = (req.message or "").lower()
    if URL_RE.search(req.message) or (
        ("link" in lower_msg and "safe" in lower_msg) or
        ("လင့်" in lower_msg and ("အန္တရာယ်" in lower_msg or "လုံခြုံ" in lower_msg))
    ):
        res = scams.analyze_text(req.message, lang_hint=lang)
        if lang == "my":
            summary = "အန္တရာယ်အဆင့်: " + res["risk_level"] + "\n" + "\n".join(f"• {f['rule']}: {f['detail']}" for f in res["findings"][:5])
        else:
            summary = "Risk level: " + res["risk_level"] + "\n" + "\n".join(f"• {f['rule']}: {f['detail']}" for f in res["findings"][:5])
        reply = summary + ("\n\n" + res.get("advice", "") if res.get("advice") else "")
        return ChatResponse(
            reply=redact(reply),
            language=lang,
            reasoning=Reasoning(intent="link_check", confidence=0.95, matched="detector", safety_notes=[]),
        )
    
    if is_password_question(req.message, lang):
        cands = extract_password_candidates(req.message)
        if cands:
            reply = "\n\n".join(format_assessment(pw, lang) for pw in cands)
        else:
            examples = generate_examples(3)
            if lang == "my":
                reply = "စကားဝှက်အားကို စစ်ဆေးရန် စကားဝှက်ကို (“……”) အတွင်းရေးပေးပါ။ ဥပမာများ:\n" + "\n".join(f"• {e}" for e in examples)
            else:
                reply = "To check strength, send your password in quotes. Examples:\n" + "\n".join(f"• {e}" for e in examples)
        return ChatResponse(
            reply=redact(reply),
            language=lang,
            reasoning=Reasoning(intent="password_strength", confidence=0.99, matched="dynamic_pw", safety_notes=["Do not reuse passwords. Enable 2FA."]),
        )

    # Password example generator (non-stream)
    if wants_examples(req.message, lang):
        examples = generate_examples(3)
        reply = "\n".join(f"{i+1}. {p}" for i, p in enumerate(examples))
        return ChatResponse(
            reply=redact(reply),
            language=lang,
            reasoning=Reasoning(intent="password_examples", confidence=0.99, matched="generator", safety_notes=["Use a password manager."])
        )

    # 2) Session / follow-up
    key = sess_key(req, request)
    sess = SESS[key]
    sess["lang"] = lang
    is_fup = is_followup(req.message, lang)
    sess["hist"].append(("user", req.message))

    # quick reset
    lower_cmd = lower_msg.strip()
    if lower_cmd in {"reset", "restart"} or (lang == "my" and lower_cmd in {"ပြန်စ", "အစပြန်"}):
        SESS[key] = {"topic": None, "step": 0, "lang": lang, "hist": deque(maxlen=6)}
        reply = "Context cleared. Tell me the issue again." if lang == "en" else "အကြောင်းအရာကို ရှင်းလင်းပြီး ပြန်စတင်ပါ။ ပြန်၍ ပြောပြပါ။"
        return ChatResponse(
            reply=reply, language=lang, reasoning=Reasoning(intent="reset", confidence=1.0, matched="", safety_notes=[])
        )

    # 3) Rules
    m = rules.match(req.message, lang_hint=lang)
    intent = m.get("intent")
    reply = ""
    conf = float(m.get("confidence", 0.0))
    used_ai = False

    # --- Prefer direct KB answer on first turn ---
    if intent and m.get("answer") and not is_fup and not wants_steps(req.message, lang):
        reply = m["answer"]
        conf = max(conf, 0.95)

    # --- Flow only when follow-up or user asked for steps ---
    elif intent and m.get("flow") and (is_fup or wants_steps(req.message, lang)):
        steps = m["flow"]
        if not is_fup or sess.get("topic") != intent:
            sess["topic"] = intent
            sess["step"] = 0
        if is_fup:
            sess["step"] = min(sess["step"] + 1, len(steps) - 1)
        idx = sess["step"]
        base_step_text = steps[idx]

        tail = ""
        if idx == len(steps) - 1 and m.get("escalation"):
            tail += "\n\n" + (m["escalation"] or "")
        tail += "\n\n" + ("Say 'done' when finished." if lang == "en" else "ပြီးရင် 'ပြီးပြီ' လို့ ပြောပါ။")

        ai_ans = await rewrite_with_ai(
            user_text=req.message, lang=lang,
            kb_points=[base_step_text] if base_step_text else None,
            safety_notes=m.get("safety_notes"),
            intent=intent, context=list(sess["hist"]),
        ) if req.allow_ai_fallback else None

        reply = (ai_ans or base_step_text) + tail
        conf = max(conf, 0.9)
        used_ai = used_ai or bool(ai_ans)

    else:
        # No flow / not follow-up → try KB answer rewrite (more localized)
        base_ans = m.get("answer") or ""
        if base_ans and req.allow_ai_fallback:
            ai_ans = await rewrite_with_ai(
                user_text=req.message, lang=lang, kb_points=[base_ans],
                safety_notes=m.get("safety_notes"), intent=intent, context=list(sess["hist"]),
            )
            if ai_ans:
                reply = ai_ans
                conf = max(conf, 0.85)
                used_ai = True

        if not reply and is_fup and req.allow_ai_fallback:
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

    # Fallbacks
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

    if not reply:
        reply = (
            "I can’t fully confirm this from my rules. "
            "Please use your bank’s official app/website or hotline for account actions. "
            "If this looks like a scam, don’t click links or share codes."
            if lang == "en" else
            "စည်းမျဉ်းအခြေပြု အဖြေမရှိသေးပါ။ ကိုယ်ရေးအကောင့်ဆိုင်ရာလုပ်ဆောင်ချက်များအတွက် "
            "ဘဏ်၏ တရားဝင် App/Website သို့မဟုတ် Hotline ကိုသာ သုံးပါ။ "
            "လိမ်လည်မှုဖြစ်နိုင်ပါက Link မနှိပ်ပါနှင့်၊ OTP/PIN မမျှဝေပါနှင့်။"
        )

    safe_reply = redact(reply)
    if safe_reply:
        sess["hist"].append(("assistant", safe_reply))

    duration_ms = int((time.perf_counter() - t0) * 1000)
    try:
        log_event(
            "chat", duration_ms=duration_ms, lang=lang, intent=intent,
            confidence=round(conf, 3), matched=m.get("matched"),
            used_ai=used_ai, allow_ai=req.allow_ai_fallback, scope=scope_reason,
            msg=redact(req.message), reply_preview=safe_reply[:220],
        )
    except Exception:
        pass

    return ChatResponse(
        reply=safe_reply,
        language=lang,
        reasoning=Reasoning(intent=intent, confidence=conf, matched=m.get("matched"), safety_notes=m.get("safety_notes", [])),
    )
