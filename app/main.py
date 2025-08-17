from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import router
from app.models import Health

# Load environment (.env) if present
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

app = FastAPI(title="Pyit Tine Htaung", version="1.0")

# CORS for local dev; restrict in prod
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # e.g., ["http://localhost:5173"] later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", response_model=Health)
def health():
    return Health(ok=True, version="1.0")

app.include_router(router, prefix="")
