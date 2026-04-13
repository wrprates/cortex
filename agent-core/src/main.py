import logging

from fastapi import FastAPI

from .api.routes import router
from .config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Cortex / DS-Agents",
    version="0.1.0",
    description="Multi-agent data science system",
)

app.include_router(router, prefix="/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.environment}
