# app/nlp/lang.py
import unicodedata as ud
import regex as re

# Basic detection: Myanmar block
_MY_RANGE = re.compile(r"[\u1000-\u109F]")

def detect_language(text: str, hint: str | None = None) -> str:
    """
    Very lightweight language hinting:
    - trust 'hint' if provided and valid,
    - else detect 'my' if Myanmar chars present, otherwise 'en'.
    """
    if hint in {"en", "my"}:
        return hint
    if not text:
        return "en"
    return "my" if _MY_RANGE.search(text) else "en"

def normalize(text: str) -> str:
    """
    Normalize inputs so rules match more often:
    - NFKC unicode normalize (flattens full-width, etc.)
    - unify smart quotes/dashes/ellipsis
    - strip Myanmar punctuation to spaces
    - lowercase
    - collapse whitespace
    """
    if not text:
        return ""
    s = ud.normalize("NFKC", text)

    # Common smart punctuation -> ascii
    table = str.maketrans({
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "—": "-", "–": "-", "…": "...",
        # Myanmar punctuation to spaces (acts like token separator)
        "။": " ", "၊": " ",
    })
    s = s.translate(table)

    # Lowercase (safe for English; Myanmar unaffected)
    s = s.lower()

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
