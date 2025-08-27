# app/engine/fallback.py
from __future__ import annotations
import os
from typing import Optional, List, Tuple, Dict, Any
from openai import AsyncOpenAI  # async client

# --- Base personas (kept strict; localized voice) ---
SYSTEM_EN = (
    "You are a localized banking & cybersecurity assistant for general users.\n"
    "Hard rules:\n"
    "- ONLY handle banking/cybersecurity topics; briefly refuse others.\n"
    "- NEVER ask for or process OTP, PIN, passwords, or personal account data.\n"
    "- Do not provide balance/transfer/investment actions—redirect to the user's bank.\n"
    "- Keep answers short, clear, and actionable.\n"
    "- If unsure, suggest contacting the bank via official channels.\n"
)

SYSTEM_MY = (
    "သင်သည် လူทั่วไปအတွက် အပြည်ပြည်ဆိုင်ရာ ဘဏ်နှင့် ဆိုက်ဘာလုံခြုံရေး အကူအညီပေးသူဖြစ်သည်။\n"
    "စည်းကမ်းချက်များ:\n"
    "- ဘဏ်/လုံခြုံရေး မဟုတ်သည့် မေးခွန်းများကို တိုတောင်းစွာ ငြင်းဆိုပြီး သက်ဆိုင်ရာ ချိတ်ဆက်ချက်ပေးပါ။\n"
    "- OTP / PIN / စကားဝှက် / ကိုယ်ရေးအကောင့်အချက်အလက် မတောင်းပါနှင့်၊ မကိုင်တွယ်ပါနှင့်။\n"
    "- လက်ကျန်စစ်ခြင်း/ငွေလွဲ/ရင်းနှီးမြှုပ်နှံ စသည့် လုပ်ဆောင်မှု မပြုလုပ်ပါနှင့်—အမြဲ ဘဏ်၏ တရားဝင်လမ်းကြောင်းသို့ ညွှန်ပြပါ။\n"
    "- ဖြေကြားချက်များကို တိုတောင်း၊ ရှင်းလင်း၊ လုပ်ဆောင်နိုင်ရန် အထောက်အကူဖြစ်စေပါ။\n"
    "- မသေချာလျှင် တရားဝင်ချန်နယ်မှ ဘဏ်ကို ဆက်သွယ်ရန် အကြံပြုပါ။\n"
)

# --- Style nudges so answers feel local & unique ---
STYLE_EN = (
    "Style:\n"
    "- Sound like a friendly local bank educator.\n"
    "- Prefer bullets or short numbered steps.\n"
    "- Use concrete examples (e.g., QR scam signs, OTP safety).\n"
    "- Avoid generic fluff; deliver 3–6 crisp points max.\n"
)
STYLE_MY = (
    "ပုံစံ:\n"
    "- မြန်မာအသံထွက်ပုံနှင့် သဘောထားဖြင့် ရှင်းရှင်းလင်းလင်း ပြောပါ။\n"
    "- များသောအားဖြင့် မှတ်စုများ (•) သို့မဟုတ် အဆင့်လိုက် (၁၊ ၂၊ ၃) အသုံးပြုပါ။\n"
    "- ကိုယ်ကျနေ့မှ နမူနာများ ထည့်ပါ (ဥပမာ QR လိမ်လည် သတိထားရမည့် အချက်များ၊ OTP လုံခြုံရေး စသည်).\n"
    "- ပိုပြီးရှည်လျားသော စကားများ မသုံးပါနှင့် — အချက် ၃–၆ ခန့်ပဲ ထုတ်ပေးပါ။\n"
)

def _system(lang: str) -> str:
    base = SYSTEM_MY if lang == "my" else SYSTEM_EN
    style = STYLE_MY if lang == "my" else STYLE_EN
    bank = os.getenv("BANK_NAME", "").strip()
    bank_line = f"\nContext: The assistant is not {bank}'s support, but educates users about safe banking practices." if bank else ""
    return base + "\n" + style + bank_line

class AIFallback:
    """
    AI fallback that can:
      - answer()           → plain model answer (strict system prompt)
      - answer_grounded()  → use scenario/KB snippets as grounding so outputs stay localized & unique
      - answer_with_system() → advanced custom system + optional chat history
    """
    def __init__(self):
        self.enabled = bool(os.getenv("OPENAI_API_KEY"))
        self.timeout = int(os.getenv("LLM_TIMEOUT", "60"))
        self.client = AsyncOpenAI(timeout=self.timeout, max_retries=2) if self.enabled else None
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")

    # ---------------- basic fallback (kept for compatibility) ----------------
    async def answer(self, user_text: str, language: str) -> Optional[str]:
        if not (self.enabled and self.client):
            return None
        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": _system(language)},
                    {"role": "user", "content": user_text},
                ],
                max_output_tokens=450,
                temperature=0.35,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            return None

    # ---------------- grounded fallback (preferred for scenarios) ------------
    async def answer_grounded(
        self,
        user_text: str,
        lang: str,
        *,
        kb_points: Optional[List[str]] = None,
        flow_steps: Optional[List[str]] = None,
        safety_notes: Optional[List[str]] = None,
        intent: Optional[str] = None,
        examples: Optional[List[Tuple[str, str]]] = None,  # (user, assistant) few-shots
        context: Optional[List[Tuple[str, str]]] = None,   # chat history
    ) -> Optional[str]:
        """
        Let AI do the final wording, but *ground* it with your scenario content.
        Pass in strings from knowledge.json (answers/flow/safety_notes).
        """
        if not (self.enabled and self.client):
            return None

        # Build a compact grounding block the model can lean on
        ground_lines: List[str] = []
        if intent:
            ground_lines.append(f"[intent] {intent}")
        if kb_points:
            for p in kb_points:
                if p:
                    ground_lines.append(f"[point] {p}")
        if flow_steps:
            for i, st in enumerate(flow_steps, 1):
                if st:
                    ground_lines.append(f"[step {i}] {st}")
        if safety_notes:
            for sn in safety_notes:
                if sn:
                    ground_lines.append(f"[safety] {sn}")

        grounding = "\n".join(ground_lines) if ground_lines else "(no explicit KB provided)"

        # Messages
        messages: List[Dict[str, Any]] = [{"role": "system", "content": _system(lang)}]

        # Optional few-shot examples (kept short)
        if examples:
            for u, a in examples[:3]:
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})

        # Optional short chat history
        if context:
            for role, msg in context[-4:]:
                if role in {"user", "assistant"} and msg:
                    messages.append({"role": role, "content": msg})

        # Final user request with grounding
        user_block = (
            f"User message:\n{user_text}\n\n"
            f"Grounding (use to stay accurate & local; rewrite naturally, do NOT quote verbatim):\n{grounding}\n\n"
            f"Output rules:\n"
            f"- Language: {'Burmese' if lang=='my' else 'English'}\n"
            f"- 3–6 bullet points or steps; include concrete, local examples.\n"
            f"- Be unique, friendly, and specific—avoid generic boilerplate.\n"
            f"- Never request or process OTP/PIN/passwords. No account operations.\n"
        )
        messages.append({"role": "user", "content": user_block})

        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=messages,
                max_output_tokens=500,
                temperature=0.35,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            return None

    # ---------------- advanced: custom system + history ----------------------
    async def answer_with_system(
        self,
        system: str,
        user_text: str,
        lang: str,
        context: Optional[List[Tuple[str, str]]] = None,
    ) -> Optional[str]:
        if not (self.enabled and self.client):
            return None

        base_system = _system(lang)
        system_prompt = (system or "").strip() + "\n\n" + base_system if system else base_system

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if context:
            for role, msg in context:
                if role in {"user", "assistant"} and msg:
                    messages.append({"role": role, "content": msg})
        messages.append({"role": "user", "content": user_text})

        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=messages,
                max_output_tokens=450,
                temperature=0.3,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            return None
