"""Minimal diagnostic server — no safeagent imports, confirms uvicorn starts."""
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok", "server": "minimal"}
