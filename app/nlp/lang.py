"""
Lightweight language utilities for Pyit Tine Htaung.

- is_burmese(text): returns True if any Myanmar characters are present.
- detect_language(text, hint): returns 'my' if Myanmar seen, else 'en'.
  If 'hint' is provided and valid ('en'/'my'), it is trusted.
- normalize(text): normalizes unicode, converts smart punctuation,
  removes Myanmar sentence punctuation (၊ ။) to spaces, lowercases,
  and collapses whitespace. Safe for rule matching.
"""

import unicodedata as ud
import regex as re

# Compiled range for Myanmar block (U+1000..U+109F)
_MY_RANGE = re.compile(r"[\u1000-\u109F]")

def is_burmese(text: str) -> bool:
    """
    Quick boolean check: does 'text' contain any Myanmar letters?
    Useful for choosing language-specific banners/copy without
    performing full detection.
    """
    if not text:
        return False
    return bool(_MY_RANGE.search(text))

def detect_language(text: str, hint: str | None = None) -> str:
    """
    Tiny language detector:
    - If 'hint' is 'en' or 'my', trust and return it.
    - Else return 'my' if Myanmar block chars are present, otherwise 'en'.
    """
    if hint in {"en", "my"}:
        return hint
    if not text:
        return "en"
    return "my" if _MY_RANGE.search(text) else "en"

def normalize(text: str) -> str:
    """
    Normalize inputs so rule matching becomes easier and consistent.

    Steps:
      1) Unicode NFKC (flattens full-width forms, etc.)
      2) Convert common smart quotes/dashes/ellipsis to ASCII equivalents
      3) Replace Myanmar punctuation (။၊) with spaces (acts as token separators)
      4) Lowercase (safe for English; Myanmar unaffected)
      5) Collapse repeated whitespace
    """
    if not text:
        return ""

    # 1) Unicode normalization (NFKC)
    s = ud.normalize("NFKC", text)

    # 2) Smart punctuation → ASCII, 3) Myanmar punctuation → spaces
    table = str.maketrans({
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "—": "-", "–": "-", "…": "...",
        "။": " ", "၊": " ",
    })
    s = s.translate(table)

    # 4) Lowercase (does not affect Myanmar script)
    s = s.lower()

    # 5) Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
