from fastapi import FastAPI
from app.routes import router
from app.models import Health

app = FastAPI(title="Pyit Tine Htaung", version="1.0")

@app.get("/health", response_model=Health)
def health():
    return Health(ok=True, version="1.0")

app.include_router(router, prefix="")
