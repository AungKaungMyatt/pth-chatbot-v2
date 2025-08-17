import regex as re
from typing import List, Dict
from app.nlp.lang import detect_language, normalize

RISK_KEYWORDS = [
    "otp", "verify now", "urgent", "account locked", "reset link",
    "limited time", "suspend", "secret", "gift", "prize", "bitcoin",
    "အရေးပေါ်", "OTP", "အကောင့်ပိတ်", "ဆုလာဘ်", "လင့်ခ်", "နှိပ်"
]
PHISHY_DOMAINS = re.compile(r"https?://([a-z0-9\-\.]+\.[a-z]{2,})(/[^\s]*)?", re.I)
CONFUSABLES = ["раураl", "paypaⅼ", "facebоok"]  # examples (Cyrillic lookalikes)

class ScamDetector:
    def analyze_text(self, text: str, lang_hint: str | None = None) -> Dict:
        lang = detect_language(text, hint=lang_hint)
        t = normalize(text)
        score = 0
        findings = []

        # Keywords
        for kw in RISK_KEYWORDS:
            if kw in t:
                score += 10
                findings.append({"rule": "keyword", "detail": f"Matched '{kw}'"})

        # URLs
        for m in PHISHY_DOMAINS.finditer(text):
            domain = m.group(1).lower()
            if any(x in domain for x in ["-update", "secure-", "auth-"]):
                score += 15
                findings.append({"rule": "url-pattern", "detail": f"suspicious domain '{domain}'"})
            else:
                score += 5
                findings.append({"rule": "url", "detail": domain})

        # Confusables
        for fake in CONFUSABLES:
            if fake in t:
                score += 20
                findings.append({"rule": "confusable", "detail": fake})

        # Heuristic cap & level
        score = max(0, min(score, 100))
        level = "low" if score < 20 else "medium" if score < 50 else "high"

        return {"score": score, "risk_level": level, "findings": findings, "language": lang}
