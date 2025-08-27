# app/engine/rule_engine.py
from __future__ import annotations
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# --- Language helper (keep import but also safe fallback) ---
try:
    from app.nlp.lang import detect_language as _detect_language
except Exception:
    def _detect_language(text: str, hint: Optional[str] = None) -> str:
        # naive: if contains Myanmar range, assume Burmese
        return "my" if any("\u1000" <= ch <= "\u109F" for ch in text) else (hint or "en")


def detect_language(text: str, hint: Optional[str] = None) -> str:
    try:
        return _detect_language(text, hint=hint)
    except Exception:
        return "my" if any("\u1000" <= ch <= "\u109F" for ch in text) else (hint or "en")


# =========================
# ===== Rule Engine =======
# =========================
class RuleEngine:
    """
    Tiny matcher over knowledge.json.
    Expects either key "intents" (preferred) or legacy "entries".
    Each entry can include:
      - intent (str)
      - patterns (list[str])
      - synonyms (list[str], optional)
      - answers: str | list[str] | {lang:str|list[str]}
      - safety_notes (list[str], optional)
      - flow: {lang: list[str]}, optional
      - escalation: {lang: str}, optional
    """

    def __init__(self, path: str = "data/knowledge.json"):
        self.path = path
        self.entries: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {}
        self.reload()

    # ---- public API ----
    def reload(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        self.meta = data.get("meta", {})
        self.entries = data.get("intents") or data.get("entries") or []

    def answer_for(self, intent: str, lang: str = "en") -> str:
        for e in self.entries:
            if e.get("intent") == intent:
                return _render_answer(e.get("answers"), lang)
        return ""

    def trace(self, message: str, lang_hint: Optional[str] = None, top_k: int = 8) -> Dict[str, Any]:
        lang = detect_language(message, hint=lang_hint)
        cands = _candidates(self.entries, message)
        out = []
        for intent, score, entry in cands[:top_k]:
            out.append({
                "intent": intent,
                "score": round(score, 3),
                "matched": entry.get("matched", ""),
                "has_flow": bool(entry.get("flow")),
                "has_escalation": bool(entry.get("escalation"))
            })
        return {"language": lang, "candidates": out}

    def match(self, message: str, lang_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns a dict:
          {
            "intent": str|None,
            "answer": str,
            "confidence": float,
            "matched": str,
            "safety_notes": list[str],
            "flow": list[str]|None,
            "escalation": str|None
          }
        """
        lang = detect_language(message, hint=lang_hint)
        cands = _candidates(self.entries, message)
        if not cands:
            return {"intent": None, "answer": "", "confidence": 0.0, "matched": "", "safety_notes": []}

        intent, score, entry = cands[0]
        answer = _render_answer(entry.get("answers"), lang)
        # include flow/escalation so router can step
        flow = None
        esc = None
        if isinstance(entry.get("flow"), dict):
            flow = entry["flow"].get(lang) or entry["flow"].get("en")
        if isinstance(entry.get("escalation"), dict):
            esc = entry["escalation"].get(lang) or entry["escalation"].get("en")

        return {
            "intent": intent,
            "answer": answer,
            "confidence": float(score),
            "matched": entry.get("matched", ""),
            "safety_notes": entry.get("safety_notes", []),
            "flow": flow,
            "escalation": esc
        }


# ---- helpers ----

def _normalize_answers(ans: Any, lang: str) -> List[str]:
    if ans is None:
        return []
    if isinstance(ans, str):
        return [ans]
    if isinstance(ans, list):
        return [str(x) for x in ans if x]
    if isinstance(ans, dict):
        v = ans.get(lang) or ans.get("en") or ans.get("my")
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v if x]
    return []

def _render_answer(ans: Any, lang: str) -> str:
    parts = _normalize_answers(ans, lang)
    if not parts:
        return ""
    # If it's a list, render as short bullets.
    if len(parts) == 1:
        return parts[0]
    return "\n".join(f"• {p}" for p in parts)

def _score_hit(text: str, patt: str) -> float:
    # keyword or regex; default weight 1.0
    try:
        if re.search(patt, text, flags=re.I):
            return 1.0
    except re.error:
        if patt.lower() in text.lower():
            return 0.8
    return 0.0

def _candidates(entries: List[Dict[str, Any]], text: str) -> List[Tuple[str, float, Dict[str, Any]]]:
    scored: List[Tuple[str, float, Dict[str, Any]]] = []
    for e in entries:
        patt = e.get("patterns", []) or []
        syns = e.get("synonyms", []) or []
        s = 0.0
        for p in patt:
            s += _score_hit(text, p)
        for p in syns:
            s += _score_hit(text, p) * 0.6
        if s > 0:
            e["matched"] = ", ".join((patt[:2] + syns[:2])[:4])
            scored.append((e.get("intent") or "", s, e))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# =========================
# ===== Scope Check =======
# =========================
# Broad allowlists for banking + security (EN + MY)
_ALLOW_BANKING = [
    r"\bbank\b", r"\bbranch\b", r"\bstatement\b", r"\binterest\b", r"\bfee(s)?\b",
    r"\baccount\b", r"\bcard\b", r"\btransfer\b", r"\btransaction\b",
    r"ဘဏ်", r"ဘဏ်ခွဲ", r"စာရင်း", r"အတိုး", r"ကြေးငွေ", r"ကတ်", r"ငွေလွှဲ", r"ငွေလုပ်ငန်း"
]
_ALLOW_SECURITY = [
    r"phish|smish|vish|scam|fraud|otp|2fa|password|malware|ransomware|breach|sim\s*swap|qr\s*code|skimming",
    r"လိမ်|လှည့်စား|လိမ်လည်|OTP|2FA|စကားဝှက်|ဗိုင်းရပ်စ်|ဒေတာ\s*ယို|SIM\s*ပြောင်း|QR|skimming|ချိုးဖောက်"
]

# Obvious out-of-scope topics (safe deny)
_DENY = [
    r"\b(recipe|cooking|football|nba|weather|flight|hotel|programming|python|javascript|homework|math|calculus)\b",
    r"မိုးလေဝသ|စားချက်|ဘောလုံး|ခရီးစဉ်|ဟိုတယ်|ပရိုဂရမ်းမင်း|ကိုးနှစ်သင်္ချာ"
]

# Sensitive-but-allowed (redirect with polite answer)
_SENSITIVE_ACCOUNT_EN = (
    "balance", "transfer", "send money", "wire", "deposit", "withdraw",
    "statement", "loan", "investment", "kyc", "update details", "check my account"
)
_SENSITIVE_ACCOUNT_MY = (
    "လက်ကျန်", "ငွေလွဲ", "ငွေ ပို့", "ငွေသွင်း", "ငွေထုတ်", "statement",
    "ချေးငွေ", "ရင်းနှီးမြှုပ်နှံ", "KYC", "အချက်အလက် ပြောင်း", "အကောင့် စစ်"
)

def _any_regex(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)

def _any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    t = text.casefold()
    return any(ph.casefold() in t for ph in phrases)

def scope_check(user_text: str, *, rules: Optional[RuleEngine] = None) -> Tuple[bool, str, str, str]:
    """
    Returns: (in_scope: bool, lang: 'en'|'my', reason: str, tag: 'normal'|'sensitive'|'deny')
    """
    lang = detect_language(user_text) or "en"
    t = (user_text or "").strip()
    if not t:
        return False, lang, "empty", "deny"

    # 1) If knowledge has a match → in scope (and sensitive if personal account)
    if rules is not None:
        m = rules.match(t, lang_hint=lang)
        if m and m.get("intent"):
            if m["intent"] == "personal_account_scope":
                return True, lang, "matched_knowledge_sensitive", "sensitive"
            return True, lang, "matched_knowledge", "normal"

    # 2) Hard deny unrelated topics
    if _any_regex(_DENY, t):
        return False, lang, "denylist", "deny"

    # 3) Broad allow if banking/security keywords appear
    if _any_regex(_ALLOW_BANKING, t) or _any_regex(_ALLOW_SECURITY, t):
        tag = "sensitive" if (_any_phrase(t, _SENSITIVE_ACCOUNT_EN) or _any_phrase(t, _SENSITIVE_ACCOUNT_MY)) else "normal"
        return True, lang, "broad_allow", tag

    # 4) Soft allow for general greetings mentioning bank/security
    if re.search(r"\b(help|hello|hi|what can you do|bank|security)\b", t, flags=re.I) or ("ဘဏ်" in t):
        return True, lang, "soft_allow", "normal"

    # 5) Otherwise, out of scope
    return False, lang, "fallback_out", "deny"
