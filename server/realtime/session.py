"""Azure OpenAI Realtime session lifecycle manager.

Handles creating ephemeral tokens, managing session config, and
session renewal before the 60-minute timeout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from server.config import get_settings

logger = logging.getLogger(__name__)


class RealtimeSessionManager:
    """Manages the lifecycle of an Azure OpenAI Realtime session."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._renewal_task: asyncio.Task | None = None
        self.session_id: str | None = None
        self.ephemeral_token: str | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=10.0)
            )
        return self._client

    async def create_session(
        self,
        voice: str = "coral",
        instructions: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new Realtime session and get an ephemeral token.

        Returns the full session response including:
        - client_secret.value (ephemeral token for WebRTC)
        - id (session ID)
        """
        settings = get_settings()
        client = await self._ensure_client()

        session_config: dict[str, Any] = {
            "voice": voice,
            "instructions": instructions,
            "input_audio_noise_reduction": {"type": "far_field"},
            "turn_detection": {
                "type": "semantic_vad",
                "eagerness": "low",
                "create_response": True,
                "interrupt_response": True,
            },
        }

        if tools:
            session_config["tools"] = tools

        logger.info(
            "Creating Azure Realtime session: deployment=%s, voice=%s",
            settings.azure_openai_deployment,
            voice,
        )

        response = await client.post(
            settings.azure_realtime_session_url,
            headers={
                "api-key": settings.azure_openai_api_key,
                "Content-Type": "application/json",
            },
            json=session_config,
        )

        if response.status_code != 200:
            body = response.text
            logger.error(
                "Session creation failed: status=%d body=%s",
                response.status_code,
                body,
            )
            response.raise_for_status()

        data = response.json()

        self.session_id = data.get("id")
        client_secret = data.get("client_secret", {})
        self.ephemeral_token = client_secret.get("value")

        logger.info("Session created: id=%s", self.session_id)

        # Schedule renewal
        self._schedule_renewal(voice, instructions, tools)

        return data

    def _schedule_renewal(
        self,
        voice: str,
        instructions: str,
        tools: list[dict[str, Any]] | None,
    ) -> None:
        """Schedule session renewal before the 60-minute timeout."""
        if self._renewal_task and not self._renewal_task.done():
            self._renewal_task.cancel()

        settings = get_settings()

        async def _renew() -> None:
            try:
                await asyncio.sleep(settings.session_renewal_seconds)
                logger.info("Session nearing timeout, renewing...")
                await self.create_session(voice, instructions, tools)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Session renewal failed")

        self._renewal_task = asyncio.create_task(_renew())

    async def close(self) -> None:
        """Clean up resources."""
        if self._renewal_task and not self._renewal_task.done():
            self._renewal_task.cancel()
            try:
                await self._renewal_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()
