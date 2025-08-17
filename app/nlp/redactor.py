import regex as re

DIGITS = re.compile(r"\d")

def luhn_check(number: str) -> bool:
    s = ''.join(ch for ch in number if ch.isdigit())
    if len(s) < 12:  # quick reject
        return False
    total, parity = 0, len(s) % 2
    for i, ch in enumerate(s):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0

def redact(text: str) -> str:
    # Card numbers (Luhn)
    def repl_card(m):
        raw = m.group(0)
        digits = ''.join(ch for ch in raw if ch.isdigit())
        return "[REDACTED-CARD-{}digits]".format(len(digits))

    text = re.sub(r"\b(?:\d[ -]?){12,19}\b", 
                  lambda m: repl_card(m) if luhn_check(m.group(0)) else m.group(0),
                  text)

    # Account/phone sequences (general)
    text = re.sub(r"\b\d{7,}\b", "[REDACTED-NUM]", text)
    return text
