import json, regex as re
from typing import Any, Dict, List, Tuple
from app.nlp.lang import normalize, detect_language

class RuleEngine:
    def __init__(self, knowledge_path: str):
        with open(knowledge_path, "r", encoding="utf-8") as f:
            self.db: Dict[str, Any] = json.load(f)
        self.entries = self.db.get("entries", [])

    def _score(self, text_norm: str, entry: Dict[str, Any]) -> Tuple[float, str]:
        # Score = patterns hit (weight 1.0) + synonyms hit (weight 0.5)
        matched = []
        score = 0.0
        for p in entry.get("patterns", []):
            if p and p.lower() in text_norm:
                score += 1.0; matched.append(p)
        for s in entry.get("synonyms", []):
            if s and s.lower() in text_norm:
                score += 0.5; matched.append(s)
        return score, ", ".join(matched[:3]) if matched else ""

    def match(self, text: str, lang_hint: str | None = None) -> Dict[str, Any]:
        lang = detect_language(text, hint=lang_hint)
        text_norm = normalize(text)
        best, best_score, best_match = None, 0.0, ""
        for e in self.entries:
            score, matched = self._score(text_norm, e)
            if score > best_score:
                best, best_score, best_match = e, score, matched
        result = {
            "language": lang,
            "intent": best["intent"] if best else None,
            "confidence": min(best_score / 3.0, 1.0),  # rough 0..1
            "answer": None,
            "safety_notes": best.get("safety_notes", []) if best else [],
            "matched": best_match
        }
        if best:
            answers = best.get("answers", {})
            result["answer"] = answers.get(lang) or answers.get("en") or ""
        return result
