import os
from typing import Optional, List, Tuple
from openai import AsyncOpenAI  # async client

SYSTEM_EN = (
    "You are a banking cybersecurity educator for the public. "
    "STRICTLY answer only questions about banking or cybersecurity; if out of scope, refuse briefly and redirect. "
    "Hard rules: Never ask for or process OTP, PIN, passwords, or personal account data. "
    "Never provide balance, transfer, or investment advice—always redirect to the user's bank. "
    "Keep answers short, factual, and actionable. If unsure, recommend contacting the bank via official channels."
)

SYSTEM_MY = (
    "သင်သည် အများပြည်သူအတွက် ဘဏ်ဆိုက်ဘာလုံခြုံရေး ဆိုင်ရာ ပညာပေးအကြံပေးရှင်ဖြစ်သည်။ "
    "ဘဏ်နှင့် လုံခြုံရေးမဟုတ်သော မေးခွန်းများကို မဖြေကြားပါနှင့် — အတိုချုံး ငြင်းဆိုပြီး "
    "အသုံးဝင်သော ခေါင်းစဉ်များသို့ ညွှန်ပြပါ။ "
    "စည်းကမ်းချက်များ: OTP, PIN, စကားဝှက် သို့မဟုတ် ကိုယ်ရေးအကောင့်အချက်အလက်များကို မတောင်း/Ma ကိုင်တွယ်ပါနှင့်။ "
    "လက်ကျန်ငွေ၊ ငွေလွဲ သို့မဟုတ် ရင်းနှီးမြှုပ်နှံ အကြံပြု မပေးပါနှင့်—အမြဲ တရားဝင်လမ်းကြောင်းမှ ဘဏ်ကို ဆက်သွယ်ရန် ညွှန်ပြပါ။ "
    "ဖြေကြားချက်များကို တိုတောင်း၊ သက်ဆိုင်၍ လုပ်ဆောင်နိုင်ရန် အထောက်အကူဖြစ်သည့်အချက်များသာ ပေးပါ။ မသေချာပါက တရားဝင်လမ်းကြောင်းမှ ဘဏ်ကို ဆက်သွယ်ရန် အကြံပြုပါ။"
)

class AIFallback:
    def __init__(self):
        self.enabled = bool(os.getenv("OPENAI_API_KEY"))
        self.timeout = int(os.getenv("LLM_TIMEOUT", "60"))
        self.client = AsyncOpenAI(timeout=self.timeout, max_retries=2) if self.enabled else None
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")

    async def answer(self, user_text: str, language: str) -> Optional[str]:
        """
        Standard AI fallback with fixed system prompt (English or Myanmar).
        """
        if not (self.enabled and self.client):
            return None

        system_prompt = SYSTEM_MY if language == "my" else SYSTEM_EN

        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                max_output_tokens=400,
                temperature=0.2,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            return None

    async def answer_with_system(
        self,
        system: str,
        user_text: str,
        lang: str,
        context: Optional[List[Tuple[str, str]]] = None,
    ) -> Optional[str]:
        """
        Flexible continuation mode.
        - system: custom system prompt
        - user_text: latest user input
        - lang: "en" or "my"
        - context: list of (role, message) tuples from recent turns
        """
        if not (self.enabled and self.client):
            return None

        # Default to language-specific base prompt if not provided
        base_system = SYSTEM_MY if lang == "my" else SYSTEM_EN
        system_prompt = system or base_system

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            for role, msg in context:
                if role not in {"user", "assistant"}:
                    continue
                messages.append({"role": role, "content": msg})
        messages.append({"role": "user", "content": user_text})

        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=messages,
                max_output_tokens=400,
                temperature=0.2,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            return None