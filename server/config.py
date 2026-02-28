"""Application configuration using Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Azure OpenAI Configuration
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""  # e.g. https://myresource.openai.azure.com
    azure_openai_deployment: str = ""  # e.g. gpt-4o-realtime
    azure_openai_api_version: str = "2025-04-01-preview"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000"

    # Conference defaults
    session_renewal_seconds: int = 55 * 60  # Renew before 60 min timeout
    time_warning_threshold: float = 0.80
    break_ending_buffer_seconds: int = 120

    @property
    def azure_realtime_session_url(self) -> str:
        """REST endpoint for creating ephemeral tokens."""
        base = self.azure_openai_endpoint.rstrip("/")
        return (
            f"{base}/openai/realtime/sessions"
            f"?api-version={self.azure_openai_api_version}"
        )

    @property
    def azure_realtime_ws_url(self) -> str:
        """WebSocket endpoint for sideband connection."""
        base = self.azure_openai_endpoint.rstrip("/").replace("https://", "wss://")
        return (
            f"{base}/openai/realtime"
            f"?api-version={self.azure_openai_api_version}"
            f"&deployment={self.azure_openai_deployment}"
        )

    @property
    def azure_realtime_rest_url(self) -> str:
        """REST endpoint for WebRTC SDP exchange (used by browser)."""
        base = self.azure_openai_endpoint.rstrip("/")
        return (
            f"{base}/openai/realtime"
            f"?api-version={self.azure_openai_api_version}"
            f"&deployment={self.azure_openai_deployment}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
