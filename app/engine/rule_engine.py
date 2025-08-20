import json
import regex as re
from typing import Any, Dict, List, Tuple
from app.nlp.lang import normalize, detect_language

# ---------- token helpers ----------
_WORD_RE = re.compile(r"[a-z0-9\u1000-\u109F]+", re.IGNORECASE)

def _tokenize(s: str) -> List[str]:
    if not s:
        return []
    s = normalize(s).lower()
    return [t for t in _WORD_RE.findall(s) if t]

def _simple_stem_en(tok: str) -> str:
    # tiny English stemmer (skip Myanmar tokens)
    if re.match(r"^[a-z]+$", tok):
        if tok.endswith("ing") and len(tok) > 5:
            return tok[:-3]
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
    Score how well pat_tokens appear in text_tokens.
    - full hit (all tokens present): 1.0
    - partial hit: proportion (>=0.6 -> 0.7, >=0.4 -> 0.4)
    - else 0
    """
    if not pat_tokens:
        return 0.0, []
    hits = [t for t in pat_tokens if t in text_tokens]
    if len(hits) == 0:
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
        # precompute tokens
        for e in self.entries:
            e["_pat_tokens"] = [_pattern_tokens(p) for p in e.get("patterns", [])]
            e["_syn_tokens"] = [_pattern_tokens(s) for s in e.get("synonyms", [])]

    def _score_entry(self, text_tokens: set, entry: Dict[str, Any]) -> Tuple[float, str, float]:
        """
        Returns (total_score, matched_terms_str, strongest_single_pattern_score)
        Patterns = full/partial 1.0 / 0.7 / 0.4
        Synonyms = half weight
        """
        matched_terms: List[str] = []
        score = 0.0
        pat_max = 0.0

        # Patterns (primary)
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

        confidence = max(0.0, min(best_score / 1.8, 1.0))
        result = {
            "language": lang,
            "intent": best_entry["intent"] if best_entry else None,
            "confidence": confidence,
            "answer": None,
            "safety_notes": best_entry.get("safety_notes", []) if best_entry else [],
            "matched": best_match_str
        }
        if best_entry:
            answers = best_entry.get("answers", {})
            result["answer"] = answers.get(lang) or answers.get("en") or ""
        return result

    def trace(self, text: str, lang_hint: str | None = None, top_k: int = 8) -> Dict[str, Any]:
        """
        Debug: return per-intent scores so you can see why a message matched (or not).
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
        return {
            "language": lang,
            "text_tokens": sorted(list(text_tokens)),
            "top": scored[:top_k]
        }