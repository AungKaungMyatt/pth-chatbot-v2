# app/engine/rule_engine.py
from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from difflib import SequenceMatcher

from app.nlp.lang import normalize as _normalize  # handles Myanmar variants

# ---------------------------------------------------------------------
# Matching helpers (single source of truth)
# ---------------------------------------------------------------------
def _prep(text: str) -> str:
    return _normalize(text or "")

def _score_hit(text_norm: str, patt: str) -> float:
    """Regex/contains score. 'text_norm' must already be normalized."""
    try:
        if re.search(patt, text_norm, flags=re.I):
            return 1.0
    except re.error:
        # invalid regex → fallback to substring contains
        if patt.lower() in text_norm.lower():
            return 0.8
    return 0.0

def _fuzzy(a: str, b: str) -> float:
    """Very light fuzzy match using normalized inputs."""
    return SequenceMatcher(None, _prep(a), _prep(b)).ratio()

def _candidates(entries: List[Dict[str, Any]], text: str) -> List[Tuple[str, float, Dict[str, Any]]]:
    """Return [(intent, score, entry), ...] sorted by score desc."""
    tnorm = _prep(text)
    scored: List[Tuple[str, float, Dict[str, Any]]] = []
    for e in entries:
        patt = e.get("patterns", []) or []
        syns = e.get("synonyms", []) or []
        s = 0.0
        for p in patt:
            s += _score_hit(tnorm, p)
        for p in syns:
            s += _score_hit(tnorm, p) * 0.6
        # gentle fuzzy if nothing hit
        if s == 0.0 and patt:
            for p in patt[:3]:
                s = max(s, _fuzzy(tnorm, p) * 0.6)
        if s > 0:
            e["matched"] = ", ".join((patt[:2] + syns[:2])[:4])
            scored.append((e.get("intent") or "", s, e))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

# ---------------------------------------------------------------------
# Language helper (keeps working even if nlp module is missing)
# ---------------------------------------------------------------------
try:
    from app.nlp.lang import detect_language as _detect_language  # type: ignore
except Exception:  # fallback if import not available
    def _detect_language(text: str, hint: Optional[str] = None) -> str:
        # naive: if Myanmar Unicode chars present → Burmese
        return "my" if any("\u1000" <= ch <= "\u109F" for ch in (text or "")) else (hint or "en")

def detect_language(text: str, hint: Optional[str] = None) -> str:
    try:
        return _detect_language(text, hint=hint)
    except Exception:
        return "my" if any("\u1000" <= ch <= "\u109F" for ch in (text or "")) else (hint or "en")

# ---------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------
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
                "has_escalation": bool(entry.get("escalation")),
            })
        return {"language": lang, "candidates": out}

    def kb_context(self, message: str, lang_hint: Optional[str] = None, top_k: int = 5) -> List[str]:
        """
        Return up to top_k localized answer snippets for use in AI fallback prompts.
        """
        lang = detect_language(message, hint=lang_hint)
        cands = _candidates(self.entries, message)
        out: List[str] = []
        for _, _, entry in cands[:top_k]:
            s = _render_answer(entry.get("answers"), lang)
            if s:
                out.append(s)
        return out

    def match(
        self,
        message: str,
        lang_hint: Optional[str] = None,
        *,
        min_confidence: float = 0.0,   # gate weak matches for AI fallback
    ) -> Dict[str, Any]:
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

        # Treat low scores as "no match" so the router can use AI.
        if min_confidence and float(score) < float(min_confidence):
            return {"intent": None, "answer": "", "confidence": float(score), "matched": entry.get("matched", ""), "safety_notes": []}

        answer = _render_answer(entry.get("answers"), lang)

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
            "escalation": esc,
        }

# ---------------------------------------------------------------------
# Answer rendering helpers
# ---------------------------------------------------------------------
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
    if len(parts) == 1:
        return parts[0]
    return "\n".join(f"• {p}" for p in parts)

# ---------------------------------------------------------------------
# Scope Check (used by routers to allow/deny/redirect)
# ---------------------------------------------------------------------
# The scope is broad: banking + cybersecurity for both customers & employees.
# We still tag personal account actions as "sensitive" so the router can
# respond with a polite redirect (no account operations).

# -- Banking domain (EN) --
_BANKING_GENERAL_EN = [
    r"\bbank(s)?\b", r"\bbranch(es)?\b", r"\baccount(s)?\b", r"\bstatement(s)?\b",
    r"\bfee(s)?\b", r"\binterest\b", r"\bsavings?\b", r"\bcurrent account\b",
    r"\bchecking\b", r"\bdeposit\b", r"\bwithdraw(al)?\b", r"\bloan(s)?\b",
    r"\bmortgage(s)?\b", r"\binstallment\b", r"\bterm deposit\b", r"\btime deposit\b",
    r"\bcertificate of deposit\b", r"\btreasury\b", r"\bremittance\b", r"\bwire\b",
    r"\btelegraphic transfer\b", r"\bswift\b", r"\bexchange rate\b",
    r"\bforeign exchange\b", r"\bfx\b", r"\bcheque|checkbook|chequebook\b",
    r"\boverdraft\b",
]

# -- Banking domain (MY) --
_BANKING_GENERAL_MY = [
    r"ဘဏ်", r"ဘဏ်ခွဲ", r"အကောင့်", r"စာရင်းပြ", r"ကြေးငွေ", r"အတိုး",
    r"သိုလှောင်ငွေ|အစုဆောင်း", r"လက်ကျန်", r"ငွေသွင်း|ငွေထုတ်",
    r"ချေးငွေ", r"အရစ်ကျ", r"ကြာချိန် သိုလှောင်ငွေ", r"SWIFT",
    r"အပြည်ပြည်ဆိုင်ရာ ငွေလွှဲ", r"ငွေလွှဲ|ပို့ရန်", r"ငွေကြေးအပြောင်းအလဲနှုန်း",
    r"လက်မှတ်စာရင်း|cheque|check",
]

# -- Digital channels / payments / cards (EN) --
_CHANNELS_EN = [
    r"\bonline banking\b", r"\bmobile banking\b", r"\bapp\b", r"\bnetbank\b",
    r"\bdebit card\b", r"\bcredit card\b", r"\bvirtual card\b", r"\batm\b",
    r"\bpoint of sale\b", r"\bpos\b", r"\bmerchant\b", r"\bqr(\s*code)?\b",
    r"\bcard not present\b", r"\bchargeback(s)?\b", r"\bdispute(s)?\b",
    r"\bcharge(s)?\b", r"\bauto[- ]debit|standing order\b",
    r"\balert(s)?\b|\bnotification(s)?\b",
]

# -- Digital channels / payments / cards (MY) --
_CHANNELS_MY = [
    r"အွန်လိုင်း ဘဏ်", r"မိုဘိုင်း ဘဏ်", r"အက်ပ်", r"ကတ်|ဒက်ဘစ်ကတ်|ကရက်ဒစ်ကတ်",
    r"ATM", r"POS", r"ကုန်သည်", r"QR", r"ပြန်လည်တင်သွင်း|ပြန်ညှိ",
    r"Auto[- ]debit|အလိုအလျောက် ငွေဖြတ်", r"အသိပေးချက်",
]

# -- Cybersecurity (EN) --
_SECURITY_EN = [
    r"phish|smish|vish|scam|fraud|spoof|impersonat",
    r"\botp\b|\bpin\b|\b2fa\b|\bmfa\b",
    r"password|passphrase|credential(s)?",
    r"malware|virus|trojan|spyware|ransomware",
    r"breach|data leak|exfiltration|compromise",
    r"sim\s*swap|port[- ]out",
    r"qr\s*code phishing|qrishing",
    r"device security|lost phone|stolen laptop",
    r"privacy|gdpr|ccpa|compliance|audit|policy|kyc",
    r"incident response|soc|siem|edr|dlp|removable media|usb|data loss",
]

# -- Cybersecurity (MY) --
_SECURITY_MY = [
    r"လိမ်|လှည့်စား|လိမ်လည်",
    r"OTP|PIN|2FA|အတည်ပြု ကုဒ်",
    r"စကားဝှက်|စကားစု",
    r"ဗိုင်းရပ်စ်|မော်ဝဲ",
    r"ဒေတာ ယို|ဖောက်ဖျက်",
    r"SIM ပြောင်း|port out",
    r"QR လိမ်လည်",
    r"ကိရိယာ လုံခြုံရေး|ဖုန်း ပျောက်|လက်ပ်တော့ ခိုး",
    r"privacy|compliance|audit|policy|KYC",
    r"Incident Response|SOC|EDR|DLP|USB|အချက်အလက် ဆုံးရှုံး",
]

# -- Employee-focused (EN & MY) --
_EMPLOYEE_EN = [
    r"\bemployee\b|\bstaff\b|\bteller\b|\bcsr\b|\bagent\b",
    r"policy|procedure|sop|playbook|handbook|standard operating",
    r"customer information handling|pci|data handling|clean desk",
    r"usb|removable media|external drive|shadow it|byod",
]
_EMPLOYEE_MY = [
    r"ဝန်ထမ်း|တယ်လာ|ဘဏ်ဝန်ထမ်း",
    r"မူဝါဒ|လုပ်ထုံးလုပ်နည်း|လမ်းညွှန်",
    r"ဖောက်သည် အချက်အလက် ကိုင်တွယ်မှု|clean desk",
    r"USB|external drive|BYOD",
]

# -- Customer-focused (EN & MY) --
_CUSTOMER_EN = [
    r"statement|fee|limit|alert|notification|how to|enable|disable|turn on|turn off",
    r"card block|freeze|unfreeze|dispute|chargeback|unknown transaction|refund",
    r"privacy request|data deletion|opt[- ]out",
]
_CUSTOMER_MY = [
    r"စာရင်းပြ|ကြေးငွေ|ကန့်သတ်|အသိပေးချက်|ဘယ်လို|ဖွင့်|ပိတ်",
    r"ကတ်ပိတ်|အကောင့် ပိတ်|မဟုတ်သော လုပ်ဆောင်မှု|ပြန်အမ်း|တိုင်ကြား",
    r"ကိုယ်ရေး အချက်အလက် တောင်းဆို|ဖျက်|မမျှဝေ​ចား",
]

# -- Obvious out-of-scope topics (safe deny) --
_DENY = [
    r"\b(recipe|cooking|football|nba|weather|flight|hotel|programming|python|javascript|homework|math|calculus)\b",
    r"မိုးလေဝသ|စားချက်|ဘောလုံး|ခရီး|ဟိုတယ်|ပရိုဂရမ်|သင်ခန်းစာ",
]

# -- Sensitive account actions (still allowed but redirected) --
_SENSITIVE_ACCOUNT_EN = (
    "balance", "transfer", "send money", "wire", "deposit", "withdraw",
    "statement", "loan", "investment", "kyc", "update details", "check my account",
    "open account", "close account", "limit increase", "card pin", "pin reset",
)
_SENSITIVE_ACCOUNT_MY = (
    "လက်ကျန်", "ငွေလွဲ", "ငွေ ပို့", "ငွေသွင်း", "ငွေထုတ်", "statement",
    "ချေးငွေ", "ရင်းနှီးမြှုပ်နှံ", "KYC", "အချက်အလက် ပြောင်း", "အကောင့် စစ်",
    "အကောင့်ဖွင့်", "အကောင့်ပိတ်", "ကန့်သတ် တိုး", "ကတ် PIN", "PIN ပြန်သတ်မှတ်",
)

def _any_regex(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)

def _any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    t = text.casefold()
    return any(ph.casefold() in t for ph in phrases)

def scope_check(user_text: str, *, rules: Optional["RuleEngine"] = None) -> Tuple[bool, str, str, str]:
    """
    Returns: (in_scope: bool, lang: 'en'|'my', reason: str, tag: 'normal'|'sensitive'|'deny')
      - 'sensitive' → personal account actions; router should answer with
        the 'personal_account_scope' intent (polite redirect).
    """
    lang = detect_language(user_text) or "en"
    t = (user_text or "").strip()
    if not t:
        return False, lang, "empty", "deny"

    # 1) If the knowledge base already matches, it's in scope.
    if rules is not None:
        m = rules.match(t, lang_hint=lang)
        if m and m.get("intent"):
            if m["intent"] == "personal_account_scope":
                return True, lang, "matched_knowledge_sensitive", "sensitive"
            return True, lang, "matched_knowledge", "normal"

    # 2) Hard deny unrelated topics
    if _any_regex(_DENY, t):
        return False, lang, "denylist", "deny"

    # 3) Broad allow across BANKING + CYBERSEC (customers & employees)
    allow = (
        _any_regex(_BANKING_GENERAL_EN, t) or _any_regex(_BANKING_GENERAL_MY, t) or
        _any_regex(_CHANNELS_EN, t) or _any_regex(_CHANNELS_MY, t) or
        _any_regex(_SECURITY_EN, t) or _any_regex(_SECURITY_MY, t) or
        _any_regex(_EMPLOYEE_EN, t) or _any_regex(_EMPLOYEE_MY, t) or
        _any_regex(_CUSTOMER_EN, t) or _any_regex(_CUSTOMER_MY, t)
    )
    if allow:
        tag = "sensitive" if (_any_phrase(t, _SENSITIVE_ACCOUNT_EN) or _any_phrase(t, _SENSITIVE_ACCOUNT_MY)) else "normal"
        return True, lang, "broad_allow", tag

    # 4) Soft allow for very general banking/security queries & greetings
    if re.search(r"\b(help|hello|hi|what can you do|bank|security)\b", t, flags=re.I) or ("ဘဏ်" in t):
        return True, lang, "soft_allow", "normal"

    # 5) Otherwise, out of scope
    return False, lang, "fallback_out", "deny"
