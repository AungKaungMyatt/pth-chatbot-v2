import json
import os
import random
import regex as re
from typing import Any, Dict, List, Tuple
from app.nlp.lang import normalize, detect_language

# --- Scope guard: only Banking + Cybersecurity (EN + Burmese) --------------
from typing import Tuple
import re

BANKING_KWS_EN = {
    "bank","atm","account","transfer","wire","swift","debit","credit","loan",
    "card","wallet","qr","statement","pin","otp","kyc","balance","branch",
    "mobile banking","internet banking"
}
CYBER_KWS_EN = {
    "phishing","smishing","vishing","scam","malware","spyware","ransomware",
    "sim swap","password","2fa","mfa","breach","spoof","impersonation","fraud"
}

_MM_RE = re.compile(r"[\u1000-\u109F]")

BANKING_KWS_MY = {
    "ဘဏ်","အာတီအမ်","ဘဏ်စာရင်း","ငွေလွှဲ","ငွေသွင်း","ငွေထုတ်","ကတ်","QR","ပိုက်ဆက်",
    "အီးဘဏ်","မိုဘိုင်းဘဏ်","ပီအိုင်အန်","အော်တီပီ","KYC","ဘဏ်ခွဲ"
}
CYBER_KWS_MY = {
    "လိမ်လည်","phishing","smishing","vishing","စကားဝှက်","၂အဆင့်","OTP",
    "SIM swap","အကောင့်ဝင်","ထိုးဖောက်","မယ်လ်ဝဲ","ကွန်ပျူတာလုံခြုံရေး"
}

def is_burmese(text: str) -> bool:
    return bool(_MM_RE.search(text or ""))

def scope_check(text: str) -> Tuple[bool, str]:
    """
    Heuristic gate. Returns (in_scope, lang) where lang is 'my' or 'en'.
    In-scope if any allowlisted keyword appears (EN or Burmese).
    """
    if not text:
        return False, "en"
    lang_hint = "my" if is_burmese(text) else "en"
    t = text.lower()

    for kw in BANKING_KWS_EN | CYBER_KWS_EN:
        if kw in t:
            return True, lang_hint

    for kw in BANKING_KWS_MY | CYBER_KWS_MY:
        if kw in text:
            return True, lang_hint

    return False, lang_hint

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
