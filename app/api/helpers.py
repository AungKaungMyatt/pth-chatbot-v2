from __future__ import annotations
from typing import Optional, List, Tuple, Dict, Any
from collections import defaultdict, deque
import os

# ---- logging shim (safe if module is missing) ----
try:
    from app.utils.logger import log_event, tail_jsonl  # type: ignore
except Exception:  # pragma: no cover
    def log_event(*args, **kwargs):  # type: ignore
        pass
    def tail_jsonl(n: int):  # type: ignore
        return []

# ---- Optional OpenAI client (lazy) ----
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

# ---- Project engines & utils ----
from app.engine.rule_engine import RuleEngine
from app.engine.scam_detector import ScamDetector
from app.engine.fallback import AIFallback
from app.nlp.redactor import redact
from app.nlp.lang import detect_language

# ---- Singletons ----
rules = RuleEngine("data/knowledge.json")
scams = ScamDetector()
ai = AIFallback()

# ---- Constants ----
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

def banner_for(lang: str) -> str:
    return _BANNER_MY if lang == "my" else _BANNER_EN

SYSTEM_PROMPT = (
    "You are Pyit Tine Htaung, a Myanmar banking cybersecurity guide. "
    "Answer ONLY banking/cybersecurity questions; if out of scope, refuse briefly and redirect. "
    "Never request/accept OTP/PIN or credentials. "
    "Always answer in the language requested by the client (lang_hint), defaulting to English if not provided."
)

OPENAI_MODEL = os.environ.get("AT_MODEL", "gpt-4o-mini")

# ---- follow-up memory (very light) ----
SESS: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    "topic": None, "step": 0, "lang": "en", "hist": deque(maxlen=6)
})

FOLLOWUP_MARKERS = {
    "en": [
        "i already did", "i did that", "i did those", "done", "still not",
        "cant find", "can't find", "next", "what next", "go on", "continue",
        "step", "next step", "not working", "didn’t work", "didn't work",
    ],
    "my": ["လုပ်ပြီးပြီ", "ပြီးပြီ", "မရသေး", "နောက်", "နောက်အဆင့်", "မအောင်မြင်သေး", "မရဘူး"],
}

def is_followup(text: str, lang: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in FOLLOWUP_MARKERS.get(lang, []))

def _answer_for(intent: str, lang: str) -> str:
    try:
        return rules.answer_for(intent, lang)
    except Exception:
        return ""

# ---- helpers used by endpoints ----

def sess_key(req, request) -> str:
    return (
        getattr(req, "session_id", None)
        or request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else "anon")
    )

def out_of_scope(lang: str) -> str:
    msg = OUT_OF_SCOPE_MY if lang == "my" else OUT_OF_SCOPE_EN
    return msg + banner_for(lang)

def sensitive_redirect(lang: str) -> str:
    txt = _answer_for("personal_account_scope", lang) or _answer_for("personal_account_scope", "en")
    if not txt:
        return OUT_OF_SCOPE_MY if lang == "my" else OUT_OF_SCOPE_EN
    return txt

# OpenAI client (sync streaming usage)
def get_openai_client():
    if OpenAI is None:
        return None
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        return OpenAI(api_key=key)
    except Exception:
        return None

# Grounded AI rewrite wrapper
async def rewrite_with_ai(
    *,
    user_text: str,
    lang: str,
    kb_points: Optional[List[str]] = None,
    flow_steps: Optional[List[str]] = None,
    safety_notes: Optional[List[str]] = None,
    intent: Optional[str] = None,
    context: Optional[List[Tuple[str, str]]] = None,
) -> Optional[str]:
    try:
        return await ai.answer_grounded(
            user_text=user_text,
            lang=lang,
            kb_points=kb_points,
            flow_steps=flow_steps,
            safety_notes=safety_notes,
            intent=intent,
            context=context,
        )
    except Exception:
        return None
