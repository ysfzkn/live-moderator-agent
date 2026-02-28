"""Gemini Live API session placeholder.

Gemini Live API uses a direct WebSocket connection and does not require
ephemeral tokens like Azure OpenAI Realtime. This module is kept as a
placeholder for potential future session management needs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RealtimeSessionManager:
    """Placeholder session manager for Gemini Live API.

    Gemini connects directly via WebSocket using an API key,
    so no ephemeral token creation or renewal is needed.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None

    async def close(self) -> None:
        """Clean up resources (no-op for Gemini)."""
        self.session_id = None
