# Pyit Tine Htaung (Banking Cybersecurity Chatbot) — v1

Rule-first, bilingual (English/Myanmar) chatbot with AI fallback (optional). Backend: **FastAPI**. Single-file knowledge base.

## Features
- Deterministic intent matching (patterns + synonyms) in EN/MY
- Safe scope guard (no personal-account help; redirect to bank)
- Scam analyzer (keywords, URL heuristics, confusables)
- Image upload endpoint with optional OCR (pytesseract)
- PII redaction (card numbers, long digit sequences)

## Endpoints
- `GET /health` — service health
- `POST /chat` — body `{ message, lang_hint?, allow_ai_fallback? }`
- `POST /analyze` — body `{ text?, urls? }`
- `POST /upload` — multipart file upload; runs OCR if available

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000