import os
from typing import Optional
from openai import AsyncOpenAI  # ⬅️ async client

SYSTEM_EN = (
    "You are a banking cybersecurity educator for the public. "
    "Hard rules: Never ask for or process OTP, PIN, passwords, or personal account data. "
    "Never provide balance, transfer, or investment advice—always redirect to the user's bank. "
    "Keep answers short, factual, and actionable. If unsure, recommend contacting the bank via official channels."
)

SYSTEM_MY = (
    "သင်သည် အများပြည်သူအတွက် ဘဏ်ဆိုက်ဘာလုံခြုံရေး ဆိုင်ရာ ပညာပေးအကြံပေးရှင်ဖြစ်သည်။ "
    "စည်းကမ်းချက်များ: OTP, PIN, စကားဝှက် သို့မဟုတ် ကိုယ်ရေးအကောင့်အချက်အလက်များကို မတောင်းပါနှင့်၊ မကိုင်တွယ်ပါနှင့်။ "
    "လက်ကျန်ငွေ၊ ငွေလွဲ သို့မဟုတ် ရင်းနှီးမြှုပ်နှံအကြံပြု မပေးပါနှင့်—အမြဲ တရားဝင်လမ်းကြောင်းမှ ဘဏ်ကို ဆက်သွယ်ရန် ညွှန်ပြပါ။ "
    "ဖြေကြားချက်များကို တိုတောင်း၊ သက်ဆိုင်၍ လုပ်ဆောင်နိုင်ရန် အထောက်အကူဖြစ်သည့်အချက်များသာ ပေးပါ။ မသေချာပါက တရားဝင်လမ်းကြောင်းမှ ဘဏ်ကို ဆက်သွယ်ရန် အကြံပြုပါ။"
)

class AIFallback:
    def __init__(self):
        self.enabled = bool(os.getenv("OPENAI_API_KEY"))
        # configurable server-side timeout (seconds)
        self.timeout = int(os.getenv("LLM_TIMEOUT", "60"))
        # async client with a sensible timeout; keep small retry count
        self.client = AsyncOpenAI(timeout=self.timeout, max_retries=2) if self.enabled else None
        # cost-efficient default model
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")

    async def answer(self, user_text: str, language: str) -> Optional[str]:
        if not (self.enabled and self.client):
            return None

        system_prompt = SYSTEM_MY if language == "my" else SYSTEM_EN

        try:
            # Responses API (v1) — async, with per-request timeout
            resp = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                max_output_tokens=400,   # a little more room than your previous 250
                temperature=0.2,
                timeout=self.timeout,
            )
            out = getattr(resp, "output_text", None)
            return out.strip() if out else None
        except Exception:
            # Fail gracefully back to rule engine
            return None
