from __future__ import annotations
import math
import secrets
import string
import regex as re
from typing import List, Dict

# ------------------- config -------------------
# Show/hide the "(~X bits of entropy)" suffix in responses.
SHOW_ENTROPY = False
ENTROPY_FMT_EN = " (~{bits:.1f} bits of entropy)"
ENTROPY_FMT_MY = " (~{bits:.1f} bits entropy)"

# A tiny list of very common passwords / patterns we’ll flag immediately.
COMMON = {
    "123456", "1234567", "12345678", "123456789", "1234567890",
    "password", "passw0rd", "qwerty", "qwertyuiop", "abc123",
    "111111", "000000", "letmein", "iloveyou", "admin", "welcome",
    "monkey", "dragon", "football", "baseball", "princess", "sunshine"
}

# ------------------- detection helpers -------------------

_EN_STRONG_WEAK = r"(strong|weak|safe|good|okay|secure)"
_MY_PASSWORD = r"(စကားဝှက်|password)"
_MY_STRONG_WEAK = r"(အားကောင်း|အားနည်း|လုံခြုံ)"

def _has_pw_question(text_norm: str) -> bool:
    # Obvious English questions
    if re.search(rf"\b(is|my|this)\b.*\bpassword\b.*\b{_EN_STRONG_WEAK}\b", text_norm, flags=re.I):
        return True
    if re.search(rf"\bpassword\b.*\b{_EN_STRONG_WEAK}\b\??$", text_norm, flags=re.I):
        return True
    # Myanmar
    if re.search(rf"{_MY_PASSWORD}.*{_MY_STRONG_WEAK}", text_norm):
        return True
    # If a quoted thing is present and the sentence mentions password at all
    if re.search(r"password|စကားဝှက်", text_norm, flags=re.I) and re.search(r"[\"“‘']([^\"”’']{3,64})[\"”’']", text_norm):
        return True
    return False

def is_password_question(text: str, lang: str) -> bool:
    t = (text or "").strip()
    return _has_pw_question(t)

def wants_examples(text: str, lang: str) -> bool:
    t = (text or "").lower()
    if lang == "my":
        return ("နမူနာ" in t) or ("ဥပမာ" in t)
    return any(w in t for w in ("example", "examples", "sample", "recommend", "suggest"))

def extract_password_candidates(text: str) -> List[str]:
    """
    Try hard not to over-extract. We look for text inside quotes first.
    Fallback: token right after the word 'password' (or 'စကားဝှက်').
    """
    t = text or ""

    # 1) Quoted strings “like this” or 'like_this'
    quoted = re.findall(r"[\"“‘']([^\"”’']{3,64})[\"”’']", t)
    cands: List[str] = [s.strip() for s in quoted if s.strip()]

    # 2) Token after 'password' e.g., "is password Ak12$m strong?"
    if not cands:
        m = re.search(r"password\s+([^\s\"'、၊။]{3,64})", t, flags=re.I)
        if m:
            cands.append(m.group(1))

    # 3) Myanmar: after စကားဝှက်
    if not cands:
        m2 = re.search(r"(?:စကားဝှက်)\s*([^\s\"'、၊။]{3,64})", t)
        if m2:
            cands.append(m2.group(1))

    # Dedup, keep up to 3
    seen = set()
    out: List[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= 3:
            break
    return out

# ------------------- scoring & feedback -------------------

def _char_classes(pw: str) -> Dict[str, bool]:
    return {
        "lower": any("a" <= ch <= "z" for ch in pw),
        "upper": any("A" <= ch <= "Z" for ch in pw),
        "digit": any("0" <= ch <= "9" for ch in pw),
        "symbol": any(ch in string.punctuation for ch in pw),
        "space": any(ch.isspace() for ch in pw),
    }

def _is_sequence(pw: str) -> bool:
    # Detect simple ascending or repeating sequences like 12345, abcde, aaaa, 121212
    if len(pw) < 4:
        return False
    # repeats
    if pw == pw[0] * len(pw):
        return True
    # alternating ABAB (simple)
    if len(set(pw)) <= 2 and pw[:2] * (len(pw)//2) in pw:
        return True
    # monotonic ASCII sequences
    asc = all(ord(pw[i+1]) - ord(pw[i]) == 1 for i in range(len(pw)-1))
    desc = all(ord(pw[i]) - ord(pw[i+1]) == 1 for i in range(len(pw)-1))
    return asc or desc

def _entropy_bits(pw: str) -> float:
    classes = _char_classes(pw)
    pool = 0
    if classes["lower"]:  pool += 26
    if classes["upper"]:  pool += 26
    if classes["digit"]:  pool += 10
    if classes["symbol"]: pool += len(string.punctuation)
    # Avoid zero; if weird chars, give a small pool
    if pool == 0:
        pool = 20
    return round(len(pw) * math.log2(pool), 1)

def assess_password(pw: str) -> Dict[str, object]:
    pwn = pw.lower()
    length = len(pw)
    classes = _char_classes(pw)
    entropy = _entropy_bits(pw)
    is_common = pwn in COMMON
    looks_seq = _is_sequence(pw)

    score = 0
    if length >= 12: score += 2
    elif length >= 8: score += 1
    if classes["lower"] and classes["upper"]: score += 1
    if classes["digit"]: score += 1
    if classes["symbol"]: score += 1
    if is_common or looks_seq: score -= 3
    if classes["space"]: score -= 1

    # Scale to bucket
    if score <= 0: verdict = "very weak"
    elif score == 1: verdict = "weak"
    elif score == 2: verdict = "okay"
    elif score == 3: verdict = "strong"
    else: verdict = "very strong"

    tips: List[str] = []
    if length < 12: tips.append("Use 12+ characters.")
    if not classes["symbol"]: tips.append("Add a symbol (e.g., ! # ?).")
    if not classes["digit"]: tips.append("Add at least one number.")
    if not (classes["lower"] and classes["upper"]): tips.append("Mix UPPER and lower case.")
    if is_common: tips.append("Avoid common passwords (e.g., 123456, qwerty).")
    if looks_seq: tips.append("Avoid sequences or repeats.")
    if classes["space"]: tips.append("Avoid spaces in passwords.")

    return {
        "length": length,
        "entropy_bits": entropy,
        "classes": classes,
        "common": is_common,
        "sequence": looks_seq,
        "score": score,
        "verdict": verdict,
        "tips": tips,
    }

def _mask(pw: str) -> str:
    if len(pw) <= 2:
        return "*" * len(pw)
    return pw[0] + ("*" * (len(pw) - 2)) + pw[-1]

def format_assessment(pw: str, lang: str = "en") -> str:
    r = assess_password(pw)
    verdict = r["verdict"]  # type: ignore
    bits = r["entropy_bits"]  # type: ignore
    tips = r["tips"]  # type: ignore
    masked = _mask(pw)

    if lang == "my":
        label = {
            "very weak": "အလွန် အားနည်း",
            "weak": "အားနည်း",
            "okay": "လုံလော်",
            "strong": "အားကောင်း",
            "very strong": "အလွန် အားကောင်း",
        }[verdict]  # type: ignore

        suffix = ENTROPY_FMT_MY.format(bits=bits) if SHOW_ENTROPY else ""
        out = [f"စကားဝှက် `{masked}` အတွက် သုံးသပ်ချက် — **{label}**{suffix}"]
        if tips:
            out.append("တိုးတက်ရန်:")
            for t in tips[:5]:
                out.append(f"• {t}")
        else:
            out.append("ကောင်းမွန်နေပြီ — 2FA ကိုလည်း ဖွင့်ထားပါ။")
        return "\n".join(out)

    # English
    suffix = ENTROPY_FMT_EN.format(bits=bits) if SHOW_ENTROPY else ""
    out = [f"Password `{masked}` looks **{verdict}**{suffix}."]
    if tips:
        out.append("Improve it by:")
        for t in tips[:5]:
            out.append(f"• {t}")
    else:
        out.append("Looks good — also enable 2FA.")
    return "\n".join(out)

# ------------------- examples -------------------

SAFE_SYMBOLS = "!@#$%^&*?-_"
SAFE_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"  # avoid easily confusable chars
SAFE_DIGITS = "23456789"  # avoid 0/1

def generate_random_password(length: int = 14) -> str:
    pool = SAFE_ALPHA + SAFE_DIGITS + SAFE_SYMBOLS
    return "".join(secrets.choice(pool) for _ in range(length))

def generate_examples(n: int = 3) -> List[str]:
    return [generate_random_password() for _ in range(max(1, min(n, 5)))]
