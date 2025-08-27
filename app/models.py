from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    lang_hint: Optional[str] = Field(None, description="Optional: 'en' or 'my'")
    allow_ai_fallback: bool = True
    session_id: Optional[str] = Field(None, description="Client-provided session id for continuity")

class Reasoning(BaseModel):
    intent: Optional[str] = None
    confidence: float = 0.0
    matched: Optional[str] = None
    safety_notes: List[str] = []

class ChatResponse(BaseModel):
    reply: str
    language: str
    reasoning: Reasoning

class AnalyzeRequest(BaseModel):
    text: Optional[str] = None
    urls: Optional[List[str]] = None
    lang_hint: Optional[str] = None

class RiskFinding(BaseModel):
    rule: str
    detail: str

class RiskReport(BaseModel):
    risk_level: str  # "low" | "medium" | "high"
    score: int       # 0-100
    findings: List[RiskFinding]
    language: str

class Health(BaseModel):
    ok: bool
    version: str