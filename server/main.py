"""FastAPI application entry point for the mAistro Moderator Agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from server.config import get_settings
from server.utils.logger import setup_logging
from server.ws.handler import ConferenceHandler

# Setup logging
settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("mAistro Moderator Agent starting")
    yield
    logger.info("mAistro Moderator Agent shutting down")


# Create FastAPI app
app = FastAPI(
    title="mAistro Moderator Agent",
    description="AI-powered conference moderator using Azure OpenAI Realtime API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - use configured origins
allowed_origins = [
    origin.strip()
    for origin in settings.allowed_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Serve client static files in production
CLIENT_DIST = Path(__file__).parent.parent / "client" / "dist"
if CLIENT_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(CLIENT_DIST / "assets")), name="assets")


@app.get("/")
async def root():
    """Serve the client app or return API info."""
    index_path = CLIENT_DIST / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "name": "mAistro Moderator Agent",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for browser client connections."""
    await ws.accept()
    logger.info("Browser client connected")

    handler = ConferenceHandler(ws)
    await handler.run()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
