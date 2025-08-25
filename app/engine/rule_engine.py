import json
import os
import random
import regex as re
from typing import Any, Dict, List, Tuple
from app.nlp.lang import normalize, detect_language

# app/engine/rule_engine.py

def _contains_myanmar(text: str) -> bool:
    # quick check: any Burmese char
    return any("\u1000" <= ch <= "\u109F" for ch in text or "")

# ----------- SIMPLE, EDITABLE VOCAB -----------
# If a phrase is present (substring match), it's considered in-scope.
# Keep this list short & meaningful; it's already quite broad.
EN_KEYWORDS = (
    # Cybersecurity (general + subfields)
    "cyber", "security", "infosec", "cybersecurity", "zero trust", "zerotrust",
    "phish", "smish", "vish", "spear phish", "social engineering",
    "malware", "virus", "worm", "trojan", "ransom", "ransomware", "spyware",
    "ddos", "botnet",
    "vulnerability", "vulnerabilities", "cve", "cvss", "patch", "patching",
    "threat intelligence", "siem", "soc", "edr", "mdm", "xdr",
    "iam", "access control", "least privilege",
    "mfa", "2fa", "otp", "pin", "password", "passkey", "password manager",
    "encryption", "decrypt", "ssl", "tls", "vpn", "firewall", "ids", "ips",
    "wifi", "bluetooth", "qr code", "qr scam",
    "forensic", "incident response", "risk assessment", "pen test", "pentest",
    "red team", "blue team", "mitre", "owasp", "api security", "mobile security",
    "sql injection", "xss", "csrf", "buffer overflow", "zero day", "zeroday",
    "data breach", "breach",

    # Banking (general + payments)
    "bank", "branch", "account", "balance", "statement", "transfer", "remittance",
    "atm", "card", "debit", "credit", "pos", "pin", "swift", "iban",
    "loan", "interest", "mortgage", "savings", "deposit", "withdraw",
    "kyc", "aml", "anti-money laundering", "fraud", "scam",
    "mobile banking", "internet banking", "wallet", "qr payment",

    # Myanmar payments/banks commonly used
    "kbz", "kbzpay", "wave", "ok dollar", "okdollar", "cbpay", "aya pay", "ayapay",
    "mpu", "cb bank", "aya bank", "uab", "yoma bank",
)

# Common Myanmar equivalents (write plain Myanmar text + brand names)
MY_KEYWORDS = (
    # Cybersecurity (Myanmar words & common English tokens users type)
    "ဆိုက်ဘာ", "လုံခြုံ", "လုံခြုံရေး", "လိမ်", "လိမ်လည်", "ဖိရှ်", "ဖိရှင်း", "ဗစ်ရှင်း", "စမစ်ရှင်း",
    "မော်လ်ဝဲ", "ဗိုင်းရပ်", "ရေနှောင့်", "ရဲန်ဆမ်ဝဲ", "စကားဝှက်", "OTP", "PIN",
    "အန္တရာယ်", "တားဆီး", "firewall", "vpn", "wifi", "qr", "qr လိမ်လည်",

    # Banking (Myanmar + brands)
    "ဘဏ်", "အကောင့်", "လွှဲ", "ငွေလွှဲ", "ငွေဖြုတ်", "ငွေသွင်း", "အတိုး", "ချေးငွေ", "statement",
    "ကတ်", "atm", "qr", "wallet", "kyc", "aml",
    "kbz", "kbzpay", "wave", "ok dollar", "cbpay", "aya", "aya pay", "mpu",
)

# Optional: light-weight “stems” so variants match (e.g., secure/security/securing)
EN_STEMS = ("secur", "encrypt", "authent", "fraud", "phish", "ransom", "vulnerab")
MY_STEMS = ()  # Burmese is better handled by explicit words/brands above.

def _match_any_substring(text: str, needles: tuple[str, ...]) -> bool:
    t = text.casefold()
    return any(n.casefold() in t for n in needles)

def _match_any_stem(text: str, stems: tuple[str, ...]) -> bool:
    t = text.casefold()
    return any(stem in t for stem in stems)

def scope_check(user_text: str) -> tuple[bool, str]:
    """
    Returns: (in_scope: bool, lang: 'my'|'en')

    Policy:
    - Allow ONLY cybersecurity or banking topics.
    - Deny everything else (frontend shows your out-of-scope message).
    """
    if not user_text or not user_text.strip():
        return False, "en"

    lang = "my" if _contains_myanmar(user_text) else "en"

    # Substring phrases + lightweight stems
    en_hit = _match_any_substring(user_text, EN_KEYWORDS) or _match_any_stem(user_text, EN_STEMS)
    my_hit = _match_any_substring(user_text, MY_KEYWORDS) or _match_any_stem(user_text, MY_STEMS)

    return (en_hit or my_hit), lang

# ---------- token helpers ----------
_WORD_RE = re.compile(r"[a-z0-9\u1000-\u109F]+", re.IGNORECASE)

def _tokenize(s: str) -> List[str]:
    if not s:
        return []
    s = normalize(s).lower()
    return [t for t in _WORD_RE.findall(s) if t]

def _simple_stem_en(tok: str) -> str:
    # Tiny stemmer for English
    if re.match(r"^[a-z]+$", tok):
        if tok.endswith("ing") and len(tok) > 5:
            return tok[:-3]
        if tok.endswith("ied") and len(tok) > 4:
            return tok[:-3] + "y"
        if tok.endswith("ed") and len(tok) > 4:
            return tok[:-2]
        if tok.endswith("es") and len(tok) > 4:
            return tok[:-2]
        if tok.endswith("s") and len(tok) > 3:
            return tok[:-1]
    return tok

def _stemmed(tokens: List[str]) -> List[str]:
    return [_simple_stem_en(t) for t in tokens]

def _token_set(text: str) -> set:
    return set(_stemmed(_tokenize(text)))

def _pattern_tokens(s: str) -> List[str]:
    return _stemmed(_tokenize(s))

def _match_score(text_tokens: set, pat_tokens: List[str]) -> Tuple[float, List[str]]:
    """
    Bag-of-words scoring for one pattern:
      - all tokens present: 1.0
      - >=60% present:     0.7
      - >=40% present:     0.4
      - else:              0.0
    Returns (score, matched_tokens).
    """
    if not pat_tokens:
        return 0.0, []
    hits = [t for t in pat_tokens if t in text_tokens]
    if not hits:
        return 0.0, []
    if len(hits) == len(pat_tokens):
        return 1.0, hits
    prop = len(hits) / len(pat_tokens)
    if prop >= 0.6:
        return 0.7, hits
    if prop >= 0.4:
        return 0.4, hits
    return 0.0, []

class RuleEngine:
    def __init__(self, knowledge_path: str):
        with open(knowledge_path, "r", encoding="utf-8") as f:
            self.db: Dict[str, Any] = json.load(f)
        self.entries: List[Dict[str, Any]] = self.db.get("entries", [])

        # Precompute tokenized patterns/synonyms
        for e in self.entries:
            e["_pat_tokens"] = [_pattern_tokens(p) for p in e.get("patterns", [])]
            e["_syn_tokens"] = [_pattern_tokens(s) for s in e.get("synonyms", [])]

    # ---- NEW: render answers with optional variety ----
    def _render_answer(self, entry: Dict[str, Any], lang: str) -> str:
        """
        Supports BOTH:
          answers[lang] = "string"
          answers[lang] = ["tip 1", "tip 2", ...]
        If it's a list, we sample a few and render a numbered list.
        ITEMS_PER_ANSWER env (default 4) controls how many to show.
        """
        answers = entry.get("answers", {}) or {}
        val = answers.get(lang) or answers.get("en") or ""

        # unchanged behavior for a single string
        if not isinstance(val, list):
            return str(val)

        try:
            k = max(1, int(os.getenv("ITEMS_PER_ANSWER", "4")))
        except Exception:
            k = 4

        pool = [str(x).strip() for x in val if str(x).strip()]
        if not pool:
            return ""

        random.shuffle(pool)
        items = pool[: min(k, len(pool))]
        return "\n".join(f"{i}. {s}" for i, s in enumerate(items, start=1))

    def _score_entry(self, text_tokens: set, entry: Dict[str, Any]) -> Tuple[float, str, float]:
        matched_terms: List[str] = []
        score = 0.0

        # Patterns (primary, also track strongest single hit)
        pat_max = 0.0
        for toks in entry.get("_pat_tokens", []):
            s, hits = _match_score(text_tokens, toks)
            if s > 0:
                pat_max = max(pat_max, s)
                score += s
                matched_terms.extend(hits)

        # Synonyms (secondary, half weight)
        for toks in entry.get("_syn_tokens", []):
            s, hits = _match_score(text_tokens, toks)
            if s > 0:
                score += s * 0.5
                matched_terms.extend(hits)

        # Dedup for debug readability
        if matched_terms:
            seen = set()
            uniq = []
            for m in matched_terms:
                if m not in seen:
                    seen.add(m)
                    uniq.append(m)
            matched_terms = uniq[:6]

        return score, (", ".join(matched_terms) if matched_terms else ""), pat_max

    def match(self, text: str, lang_hint: str | None = None) -> Dict[str, Any]:
        lang = detect_language(text, hint=lang_hint)
        text_tokens = _token_set(text)

        best_entry = None
        best_score = 0.0
        best_match_str = ""
        best_pat_max = 0.0

        for e in self.entries:
            score, matched_str, pat_max = self._score_entry(text_tokens, e)
            if (score > best_score) or (score == best_score and pat_max > best_pat_max):
                best_entry = e
                best_score = score
                best_match_str = matched_str
                best_pat_max = pat_max

        # Slightly friendlier normalization (1.6 instead of 1.8)
        confidence = max(0.0, min(best_score / 1.6, 1.0))

        result = {
            "language": lang,
            "intent": best_entry["intent"] if best_entry else None,
            "confidence": confidence,
            "answer": None,
            "safety_notes": best_entry.get("safety_notes", []) if best_entry else [],
            "matched": best_match_str,
        }
        if best_entry:
            # use the new renderer (keeps old behavior when a single string)
            result["answer"] = self._render_answer(best_entry, lang)
        return result

    def trace(self, text: str, lang_hint: str | None = None, top_k: int = 12) -> Dict[str, Any]:
        """
        Debug detail: per-intent scores and matched terms.
        """
        lang = detect_language(text, hint=lang_hint)
        text_tokens = _token_set(text)

        scored = []
        for e in self.entries:
            score, matched_str, pat_max = self._score_entry(text_tokens, e)
            scored.append({
                "intent": e.get("intent"),
                "score": round(score, 3),
                "pat_max": round(pat_max, 3),
                "matched_terms": matched_str
            })
        scored.sort(key=lambda x: (x["score"], x["pat_max"]), reverse=True)
        return {"language": lang, "text_tokens": sorted(list(text_tokens)), "top": scored[:top_k]}
