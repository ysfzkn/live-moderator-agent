"""Gemini Live API WebSocket connection.

This is the main AI connection. Unlike OpenAI's WebRTC+sideband model,
Gemini uses a single server-side WebSocket for everything:
- Send/receive audio (PCM 16kHz mono, base64 encoded)
- System instructions and session configuration
- Function calling (tools)
- Voice activity detection

Protocol: BidiGenerateContent over WebSocket
Docs: https://ai.google.dev/api/live
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

from server.config import get_settings

logger = logging.getLogger(__name__)

# Gemini event types we care about
GEMINI_SETUP_COMPLETE = "setupComplete"
GEMINI_SERVER_CONTENT = "serverContent"
GEMINI_TOOL_CALL = "toolCall"
GEMINI_TOOL_CALL_CANCELLATION = "toolCallCancellation"

# Voices supported by gemini-2.5-flash-native-audio
GEMINI_VALID_VOICES = {"Orus", "Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Zephyr"}
GEMINI_DEFAULT_VOICE = "Orus"

EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class GeminiLiveConnection:
    """Server-side WebSocket connection to Gemini Live API.

    Handles the BidiGenerateContent protocol:
    1. Connect → send setup message → receive setupComplete
    2. Stream audio in/out via realtimeInput / serverContent
    3. Handle tool calls via toolCall / toolResponse
    """

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._listen_task: asyncio.Task | None = None
        self._event_handlers: dict[str, list[EventCallback]] = {}
        self._connected = False
        self._setup_complete = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def on_event(self, event_type: str, handler: EventCallback) -> None:
        """Register a handler for a Gemini event type.

        Event types: setupComplete, serverContent, toolCall,
                     toolCallCancellation, *, audio, transcription
        """
        self._event_handlers.setdefault(event_type, []).append(handler)

    async def connect(
        self,
        instructions: str = "",
        voice: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Connect to Gemini Live API and send setup message."""
        settings = get_settings()
        url = settings.gemini_ws_url
        voice = voice or settings.gemini_voice

        # Validate voice name — fallback to default if invalid
        if voice not in GEMINI_VALID_VOICES:
            logger.warning("Voice '%s' not supported, falling back to '%s'", voice, GEMINI_DEFAULT_VOICE)
            voice = GEMINI_DEFAULT_VOICE

        logger.info(
            "Connecting to Gemini Live API: model=%s, key=%s..., url_len=%d",
            settings.gemini_model,
            settings.gemini_api_key[:8] if settings.gemini_api_key else "EMPTY",
            len(url),
        )

        self._ws = await websockets.connect(
            url,
            max_size=None,
            additional_headers={"Content-Type": "application/json"},
        )
        self._connected = True
        self._setup_complete.clear()

        # Start listen loop BEFORE sending setup
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Send setup message (must be first message)
        setup_msg = self._build_setup(instructions, voice, tools)
        await self._send(setup_msg)

        # Wait for setupComplete
        try:
            await asyncio.wait_for(self._setup_complete.wait(), timeout=15.0)
            logger.info("Gemini Live session established")
        except asyncio.TimeoutError:
            logger.error("Gemini setup timed out")
            await self.disconnect()
            raise ConnectionError("Gemini Live setup timed out")

    def _build_setup(
        self,
        instructions: str,
        voice: str,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the BidiGenerateContentSetup message."""
        settings = get_settings()

        setup: dict[str, Any] = {
            "setup": {
                "model": f"models/{settings.gemini_model}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": voice,
                            }
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": instructions}],
                },
                "realtimeInputConfig": {
                    "automaticActivityDetection": {
                        "disabled": False,
                        "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                        "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                        "silenceDurationMs": 800,
                    },
                    "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }

        if tools:
            setup["setup"]["tools"] = tools

        return setup

    async def disconnect(self) -> None:
        """Close the connection."""
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

    async def _send(self, msg: dict[str, Any]) -> None:
        """Send a JSON message to Gemini."""
        if not self._ws:
            logger.warning("Cannot send: not connected")
            return
        await self._ws.send(json.dumps(msg))

    # --- High-level convenience methods ---

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send audio data to Gemini (PCM 16kHz 16-bit mono)."""
        b64 = base64.b64encode(pcm_data).decode("ascii")
        await self._send({
            "realtimeInput": {
                "audio": {
                    "data": b64,
                    "mimeType": "audio/pcm;rate=16000",
                }
            }
        })

    async def send_text(self, text: str) -> None:
        """Send a text message as user input (triggers response)."""
        await self._send({
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": text}],
                    }
                ],
                "turnComplete": True,
            }
        })

    async def send_tool_response(
        self, call_id: str, result: dict[str, Any]
    ) -> None:
        """Send function call result back to Gemini."""
        await self._send({
            "toolResponse": {
                "functionResponses": [
                    {
                        "id": call_id,
                        "response": result,
                    }
                ]
            }
        })

    async def update_instructions(self, instructions: str) -> None:
        """Update system instructions by sending as context.

        Gemini doesn't have a session.update like OpenAI.
        We send instructions as a system turn via clientContent.
        """
        await self._send({
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": f"[SYSTEM UPDATE] {instructions}"}],
                    }
                ],
                "turnComplete": True,
            }
        })

    async def trigger_speech(self, text: str) -> None:
        """Make the AI speak specific content."""
        await self.send_text(text)

    # --- Internal ---

    async def _listen_loop(self) -> None:
        """Listen for messages from Gemini Live API."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    event = json.loads(message)
                    await self._dispatch_event(event)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from Gemini")

        except websockets.ConnectionClosed as e:
            logger.warning("Gemini WebSocket closed: %s", e)
            self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Gemini listen loop error")
            self._connected = False

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        """Route a Gemini event to registered handlers."""

        # Determine event type based on which field is present
        event_type = ""
        if "setupComplete" in event:
            event_type = GEMINI_SETUP_COMPLETE
            self._setup_complete.set()
        elif "serverContent" in event:
            event_type = GEMINI_SERVER_CONTENT
            # Extract sub-events for convenience
            sc = event["serverContent"]

            # Audio data
            model_turn = sc.get("modelTurn", {})
            parts = model_turn.get("parts", [])
            for part in parts:
                inline = part.get("inlineData")
                if inline and "audio" in inline.get("mimeType", ""):
                    await self._fire("audio", {
                        "data": inline["data"],
                        "mimeType": inline["mimeType"],
                    })

            # Input transcription
            input_tx = sc.get("inputTranscription")
            if input_tx and input_tx.get("text"):
                await self._fire("input_transcription", input_tx)

            # Output transcription
            output_tx = sc.get("outputTranscription")
            if output_tx and output_tx.get("text"):
                await self._fire("output_transcription", output_tx)

            # Turn complete
            if sc.get("turnComplete"):
                await self._fire("turn_complete", event)

            # Interrupted
            if sc.get("interrupted"):
                await self._fire("interrupted", event)

        elif "toolCall" in event:
            event_type = GEMINI_TOOL_CALL
        elif "toolCallCancellation" in event:
            event_type = GEMINI_TOOL_CALL_CANCELLATION

        # Fire typed handlers
        if event_type:
            await self._fire(event_type, event)

        # Fire wildcard
        await self._fire("*", event)

    async def _fire(self, event_type: str, event: dict[str, Any]) -> None:
        """Fire handlers for a given event type."""
        for handler in self._event_handlers.get(event_type, []):
            try:
                await handler(event)
            except Exception:
                logger.exception("Error in handler for %s", event_type)
