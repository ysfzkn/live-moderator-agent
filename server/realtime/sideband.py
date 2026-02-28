"""Sideband WebSocket connection to Azure OpenAI Realtime API.

This connection runs server-side and shares the same session as the
browser's WebRTC connection (via call_id). It allows the server to:
- Update session instructions (session.update)
- Register and handle function calls
- Trigger AI responses (response.create)
- Monitor session events
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

from server.config import get_settings

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class SidebandConnection:
    """Server-side WebSocket connection to an Azure OpenAI Realtime session."""

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._listen_task: asyncio.Task | None = None
        self._event_handlers: dict[str, list[EventCallback]] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def on_event(self, event_type: str, handler: EventCallback) -> None:
        """Register a handler for a specific Realtime server event."""
        self._event_handlers.setdefault(event_type, []).append(handler)

    async def connect(self, call_id: str | None = None) -> None:
        """Connect to the Azure OpenAI Realtime API via WebSocket.

        If call_id is provided, connects to an existing session (sideband mode).
        Otherwise creates a new direct WebSocket session.
        """
        settings = get_settings()

        url = settings.azure_realtime_ws_url
        if call_id:
            url += f"&call_id={call_id}"

        headers = {
            "api-key": settings.azure_openai_api_key,
        }

        logger.info("Connecting sideband WebSocket to Azure OpenAI")

        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=None,
        )
        self._connected = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Sideband WebSocket connected")

    async def disconnect(self) -> None:
        """Close the sideband connection."""
        self._connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_event(self, event: dict[str, Any]) -> None:
        """Send an event to the Realtime API."""
        if not self._ws:
            logger.warning("Cannot send event: not connected")
            return

        data = json.dumps(event)
        await self._ws.send(data)
        logger.debug("Sent event: %s", event.get("type"))

    # --- High-level convenience methods ---

    async def update_session(
        self,
        instructions: str | None = None,
        tools: list[dict] | None = None,
        turn_detection: dict | None = None,
        voice: str | None = None,
    ) -> None:
        """Send a session.update event to change session configuration."""
        session: dict[str, Any] = {}

        if instructions is not None:
            session["instructions"] = instructions
        if tools is not None:
            session["tools"] = tools
        if turn_detection is not None:
            session["turn_detection"] = turn_detection
        if voice is not None:
            session["voice"] = voice

        if session:
            await self.send_event({"type": "session.update", "session": session})

    async def create_response(self, instructions: str | None = None) -> None:
        """Trigger the AI to generate a response."""
        event: dict[str, Any] = {"type": "response.create", "response": {}}
        if instructions:
            event["response"]["instructions"] = instructions
        await self.send_event(event)

    async def send_function_call_output(
        self, call_id: str, output: dict[str, Any]
    ) -> None:
        """Send the result of a function call back to the API."""
        await self.send_event(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output, ensure_ascii=False),
                },
            }
        )

    async def cancel_response(self) -> None:
        """Cancel the current response (e.g., when operator presses Next)."""
        await self.send_event({"type": "response.cancel"})

    # --- Internal ---

    async def _listen_loop(self) -> None:
        """Listen for events from the Realtime API."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    event = json.loads(message)
                    event_type = event.get("type", "")

                    handlers = self._event_handlers.get(event_type, [])
                    for handler in handlers:
                        try:
                            await handler(event)
                        except Exception:
                            logger.exception(
                                "Error in event handler for %s", event_type
                            )

                    # Also fire wildcard handlers
                    for handler in self._event_handlers.get("*", []):
                        try:
                            await handler(event)
                        except Exception:
                            logger.exception("Error in wildcard handler")

                except json.JSONDecodeError:
                    logger.warning("Received non-JSON message from Realtime API")

        except websockets.ConnectionClosed as e:
            logger.warning("Sideband WebSocket closed: %s", e)
            self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Sideband listen loop error")
            self._connected = False
