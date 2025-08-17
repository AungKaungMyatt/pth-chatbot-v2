import json
from typing import Any, Dict, List, Tuple, Optional
import regex as re
from app.nlp.lang import normalize, detect_language

# Tokenizer: letters & numbers in any language (Myanmar included)
WORD = re.compile(r"[\p{L}\p{N}]+", re.UNICODE)


class RuleEngine:
    def __init__(self, knowledge_path: str):
        self.knowledge_path = knowledge_path
        self.db: Dict[str, Any] = {}
        self.entries: List[Dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        """Reload knowledge.json from disk."""
        with open(self.knowledge_path, "r", encoding="utf-8") as f:
            self.db = json.load(f)
        self.entries = self.db.get("entries", []) or []

    def _score(self, text_norm: str, entry: Dict[str, Any]) -> Tuple[float, str]:
        """
        Scoring with token overlap:
          - Full phrase hit: weight (1.0 for patterns, 0.5 for synonyms)
          - Partial hit (>=2 words present AND >=60% words present): 60% of weight
            (prevents single-word overlaps like 'check' from triggering)
        Returns (score, matched_summary)
        """
        tokens = set(WORD.findall(text_norm))
        matched: List[str] = []
        score = 0.0

        def score_phrase(phrase: str, weight: float) -> None:
            nonlocal score
            if not phrase:
                return
            words = WORD.findall(phrase.lower())
            if not words:
                return
            present = sum(1 for w in words if w in tokens)
            if present == 0:
                return
            frac = present / len(words)
            if frac >= 1.0:
                score += weight
                matched.append(phrase)
            elif len(words) >= 2 and present >= 2 and frac >= 0.6:
                score += weight * 0.6
                matched.append(f"{phrase} (partial)")

        for p in (entry.get("patterns") or []):
            score_phrase(p, 1.0)
        for s in (entry.get("synonyms") or []):
            score_phrase(s, 0.5)

        return score, ", ".join(matched[:3]) if matched else ""

    def match(self, text: str, lang_hint: Optional[str] = None) -> Dict[str, Any]:
        lang = detect_language(text, hint=lang_hint)
        text_norm = normalize(text)
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        best_match = ""

        for e in self.entries:
            s, m = self._score(text_norm, e)
            if s > best_score:
                best = e
                best_score = s
                best_match = m

        result: Dict[str, Any] = {
            "language": lang,
            "intent": best.get("intent") if best else None,
            "confidence": min(best_score / 3.0, 1.0),  # keep scaling similar to your original
            "answer": None,
            "safety_notes": best.get("safety_notes", []) if best else [],
            "matched": best_match,
        }

        if best:
            answers = best.get("answers", {}) or {}
            result["answer"] = answers.get(lang) or answers.get("en") or ""

        return result