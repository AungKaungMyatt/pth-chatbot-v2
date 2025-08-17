import regex as re

MY_UNICODE_BLOCK = re.compile(r"[\p{Myanmar}]")

def detect_language(text: str, hint: str | None = None) -> str:
    if hint in ("en", "my"):
        return hint
    return "my" if MY_UNICODE_BLOCK.search(text) else "en"

def normalize(text: str) -> str:
    # Lowercase + trim; (Optionally plug Zawgyi->Unicode here later)
    return text.strip().lower()

def is_myanmar(text: str) -> bool:
    return bool(MY_UNICODE_BLOCK.search(text))
