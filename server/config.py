"""Application configuration using Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# Project root directory (where .env lives)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Gemini Live API Configuration
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-native-audio-latest"
    gemini_voice: str = "Orus"  # Default voice

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000"

    # Conference defaults
    time_warning_threshold: float = 0.80
    break_ending_buffer_seconds: int = 120

    @property
    def gemini_ws_url(self) -> str:
        """WebSocket endpoint for Gemini Live API."""
        return (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={self.gemini_api_key}"
        )

    model_config = {
        "env_file": str(_PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
