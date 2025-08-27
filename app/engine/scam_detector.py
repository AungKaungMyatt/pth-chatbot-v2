# app/engine/scam_detector.py
import regex as re
from typing import List, Dict, Tuple
from urllib.parse import urlparse, unquote
from ipaddress import ip_address
from app.nlp.lang import detect_language, normalize

# ---------- URL & text patterns ----------
URL_RE = re.compile(r"(?i)\b((?:https?://|http://|www\.)[^\s<>'\"()]+)")
DOMAIN_RE = re.compile(r"(?:https?://)?([a-z0-9\-\.]+)\.[a-z]{2,}(?::\d+)?", re.I)

SUSP_TLDS = {
    "tk","ml","ga","cf","gq","xyz","top","live","icu","vip","kim","win","bid",
    "loan","work","mom","party","click","country","fit","review","date"
}
SHORTENERS = {
    "bit.ly","tinyurl.com","t.co","goo.gl","ow.ly","rebrand.ly","is.gd","buff.ly","rb.gy",
    "cutt.ly","lnkd.in","s.id"
}
SUSP_PATH_WORDS = {
    "login","signin","verify","update","unlock","secure","confirm","re-activate","reactivate",
    "gift","prize","bonus","promo","invoice","bank","wallet","crypto","airdrop"
}
SUSP_QUERY_KEYS = {"otp","pin","password","pass","code","token","auth","session","secret"}

RISK_KEYWORDS_EN = {
    "urgent","verify now","account locked","limited time","suspend","reset link","free gift",
    "congratulations","winner","bitcoin","airdrop","claim now","click now"
}
RISK_KEYWORDS_MY = {
    "အရေးပေါ်","အမြန်","အကောင့်ပိတ်","အပ္ဒိတ်","ချိတ်ဆက်","ပြန်လည်ဖွင့်",
    "လင့်ခ်","နှိပ်","ဆုလာဘ်","အကူအညီ","OTP","PIN","ကုဒ်","ထည့်ပေး","အတည်ပြု"
}

CONFUSABLES = {"раураl","paypaⅼ","facebоok","ｍｍbank","kbｚ","ayɑ"}  # mixed-script lookalikes

KNOWN_BANK_WORDS = {"kbz","aya","cb bank","uab","yoma","agd","global treasure","shwe","myawaddy","innwa"}

# ---------- helpers ----------
def _is_ip(host: str) -> bool:
    try:
        ip_address(host)
        return True
    except ValueError:
        return False

def _idna_ascii(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except Exception:
        return host

def _count_subdomains(host: str) -> int:
    parts = host.split(".")
    return max(0, len(parts) - 2)  # ignore TLD + registrable label

def _norm(text: str) -> str:
    return normalize(text or "")

# ---------- core scoring ----------
def _score_domain(host: str) -> Tuple[int, List[Dict]]:
    findings: List[Dict] = []
    score = 0
    ascii_host = _idna_ascii(host)

    # IP address in URL
    if _is_ip(host):
        score += 25
        findings.append({"rule": "ip-in-url", "detail": host})

    # IDN/punycode or non-ascii
    if "xn--" in ascii_host or any(ord(c) > 127 for c in host):
        score += 20
        findings.append({"rule": "idn", "detail": host})

    # suspicious TLD
    tld = ascii_host.rsplit(".", 1)[-1].lower() if "." in ascii_host else ""
    if tld in SUSP_TLDS:
        score += 15
        findings.append({"rule": "tld", "detail": tld})

    # shorteners
    if ascii_host.lower() in SHORTENERS:
        score += 20
        findings.append({"rule": "shortener", "detail": ascii_host})

    # too many subdomains or hyphens
    if _count_subdomains(ascii_host) >= 3:
        score += 15
        findings.append({"rule": "subdomains", "detail": ascii_host})
    if ascii_host.count("-") >= 3:
        score += 10
        findings.append({"rule": "hyphens", "detail": ascii_host})

    # confusables (simple check)
    for fake in CONFUSABLES:
        if fake in host.lower():
            score += 20
            findings.append({"rule": "confusable", "detail": fake})

    return score, findings

def _score_url(url: str) -> Tuple[int, List[Dict]]:
    s, f = 0, []
    if not url.lower().startswith(("http://","https://")):
        url = "http://" + url  # make parseable

    p = urlparse(url)
    host = p.hostname or ""
    path = (p.path or "") + ("?" + (p.query or "") if p.query else "")
    s1, f1 = _score_domain(host)
    s += s1; f += f1

    # http (no https)
    if p.scheme == "http":
        s += 10
        f.append({"rule": "no-https", "detail": url})

    # @ in URL (userinfo trick)
    if "@" in p.netloc or "@" in url.split("://",1)[-1].split("/",1)[0]:
        s += 15
        f.append({"rule": "at-sign", "detail": url})

    # suspicious path/query words
    low = unquote(path).lower()
    for w in SUSP_PATH_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low, flags=re.I):
            s += 8
            f.append({"rule": "path-keyword", "detail": w})

    # suspicious query keys
    for k in SUSP_QUERY_KEYS:
        if re.search(rf"[?&]{k}=", low):
            s += 10
            f.append({"rule": "query-key", "detail": k})

    return s, f

def _score_text(text_norm: str) -> Tuple[int, List[Dict]]:
    s, f = 0, []
    for kw in RISK_KEYWORDS_EN:
        if kw in text_norm:
            s += 6; f.append({"rule": "keyword", "detail": kw})
    for kw in RISK_KEYWORDS_MY:
        if kw in text_norm:
            s += 6; f.append({"rule": "keyword", "detail": kw})
    for fake in CONFUSABLES:
        if fake in text_norm:
            s += 20; f.append({"rule": "confusable", "detail": fake})
    return s, f

def _brand_mismatch(text_norm: str, domains: List[str]) -> Tuple[int, List[Dict]]:
    if not domains:
        return 0, []
    s, f = 0, []
    mentioned = {b for b in KNOWN_BANK_WORDS if b in text_norm}
    if mentioned:
        for d in domains:
            if not any(b.replace(" ", "") in d.replace("-", "") for b in mentioned):
                s += 10
                f.append({"rule": "brand-mismatch", "detail": f"{','.join(sorted(mentioned))} -> {d}"})
    return s, f

def _risk_level(score: int) -> str:
    return "low" if score < 20 else "medium" if score < 50 else "high"

def _advice(lang: str, level: str) -> str:
    if lang == "my":
        tips = [
            "လင့်ခ်ကို တရားဝင် App/Website အတည်ပြုမချင်း မနှိပ်ပါနှင့်။",
            "အရေးတကြီး ချက်ချင်းလုပ်ရန် ပြောပါက ထပ်စစ်ပါ။",
            "OTP/PIN/စကားဝှက် မမျှဝေပါနှင့်။",
            "သံသယရှိ하면 ဘဏ်ထံ တိုက်ရိုက်ဆက်သွယ်ပါ။",
        ]
        head = "အန္တရာယ်အဆင့်: " + {"low":"နိမ့်","medium":"အလတ်","high":"မြင့်"}[level]
        return head + "\n• " + "\n• ".join(tips)
    else:
        tips = [
            "Don’t click the link unless you can verify via the official app/website.",
            "Be extra cautious with urgent/pressure wording.",
            "Never share OTP/PIN/password.",
            "Contact your bank via official channels if unsure.",
        ]
        return f"Risk level: {level}\n• " + "\n• ".join(tips)

class ScamDetector:
    def analyze_text(self, text: str, lang_hint: str | None = None) -> Dict:
        lang = detect_language(text, hint=lang_hint)
        tnorm = _norm(text)

        score, findings = 0, []

        # 1) Text-level cues (EN + MY)
        s, f = _score_text(tnorm)
        score += s; findings += f

        # 2) URL/domain analysis
        urls = [m.group(1) for m in URL_RE.finditer(text or "")]
        domains: List[str] = []
        for u in urls:
            # normalize www. without scheme
            full = u if u.lower().startswith(("http://","https://")) else "http://" + u
            p = urlparse(full)
            host = (p.hostname or "").lower()
            if not host:
                continue
            domains.append(host)
            s2, f2 = _score_url(u)
            score += s2; findings += f2

        # 3) Brand-word present but domain mismatch
        s3, f3 = _brand_mismatch(tnorm, domains)
        score += s3; findings += f3

        # Cap and classify
        score = max(0, min(score, 100))
        level = _risk_level(score)

        return {
            "score": score,
            "risk_level": level,
            "findings": findings,
            "language": lang,
            "urls": urls,
            "domains": domains,
            "advice": _advice(lang, level),
        }
