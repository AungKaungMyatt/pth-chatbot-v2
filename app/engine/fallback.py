import os
from typing import Optional

class AIFallback:
    def __init__(self):
        self.enabled = bool(os.getenv("OPENAI_API_KEY"))

    async def answer(self, user_text: str, language: str) -> Optional[str]:
        if not self.enabled:
            return None
        # Pseudo-LLM call (replace with real OpenAI SDK when you’re ready)
        # Keep responses safe & generic.
        system = ("You are a banking cybersecurity educator. "
                  "Never request OTPs/links. If user asks about personal accounts, "
                  "redirect them to their bank. Answer in language='{lang}'.").format(lang=language)
        # Example stub so the project runs without the SDK:
        return ("[AI Answer in {lang}] Here’s general guidance. "
                "For personal account help, contact your bank directly.").format(lang=language)
