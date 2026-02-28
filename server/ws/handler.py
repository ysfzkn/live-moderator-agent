"""WebSocket handler for browser client connections.

Orchestrates the conference by connecting the browser, state machine,
timer, and Gemini Live API.

Architecture (Gemini):
  Browser <-WebSocket-> Server <-WebSocket-> Gemini Live API
  Audio flows: Browser mic -> Server -> Gemini -> Server -> Browser speaker
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from server.config import get_settings
from server.conference.agenda_manager import AgendaManager
from server.conference.state_machine import ConferenceStateMachine
from server.conference.timer import SessionTimer
from server.conference.tools import CONFERENCE_TOOLS, ToolHandler
from server.models.state import ACTIVE_SPEAKING_STATES, ConferenceState
from server.prompts.builder import build_prompt
from server.realtime.events import RealtimeEventHandler
from server.realtime.sideband import GeminiLiveConnection

logger = logging.getLogger(__name__)


class ConferenceHandler:
    """Handles a single conference session for one browser client."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

        # Core components
        self._sm = ConferenceStateMachine()
        self._agenda_mgr = AgendaManager()
        self._timer = SessionTimer(self._sm)
        self._tool_handler = ToolHandler(self._sm, self._agenda_mgr)

        # Gemini Live API connection
        self._gemini = GeminiLiveConnection()
        self._event_handler: RealtimeEventHandler | None = None

        # Register timer tick callback
        self._timer.on_tick(self._on_timer_tick)

        # Register state change callback for timer management
        self._sm.on_state_change(self._on_state_change_timer)

    async def run(self) -> None:
        """Main loop: receive messages from the browser and handle them."""
        try:
            while True:
                # Handle both text and binary messages
                message = await self._ws.receive()

                msg_type = message.get("type", "")

                if msg_type == "websocket.disconnect":
                    logger.info("Browser disconnected (clean)")
                    break

                if msg_type == "websocket.receive":
                    if "text" in message:
                        await self._handle_text_message(message["text"])
                    elif "bytes" in message:
                        await self._handle_audio_input(message["bytes"])

        except WebSocketDisconnect:
            logger.info("Browser disconnected")
        except RuntimeError as e:
            # Starlette raises RuntimeError after disconnect
            if "disconnect" in str(e).lower():
                logger.info("Browser disconnected (runtime)")
            else:
                logger.exception("WebSocket runtime error")
        except Exception:
            logger.exception("WebSocket handler error")
        finally:
            await self._cleanup()

    async def _handle_text_message(self, raw: str) -> None:
        """Parse and route a text message from the browser."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error("Invalid JSON")
            return

        msg_type = msg.get("type", "")
        payload = msg.get("payload", {})
        await self._handle_message(msg_type, payload)

    async def _handle_audio_input(self, audio_bytes: bytes) -> None:
        """Forward audio from browser microphone to Gemini."""
        if self._gemini.is_connected:
            await self._gemini.send_audio(audio_bytes)

    async def _handle_message(self, msg_type: str, payload: dict) -> None:
        """Route incoming messages to appropriate handlers."""

        if msg_type == "LOAD_AGENDA":
            await self._handle_load_agenda(payload)

        elif msg_type == "CONNECT_AI":
            await self._handle_connect_ai()

        elif msg_type == "START_CONFERENCE":
            await self._handle_start_conference()

        elif msg_type == "PAUSE":
            await self._handle_pause()

        elif msg_type == "RESUME":
            await self._handle_resume()

        elif msg_type == "NEXT_SESSION":
            await self._handle_next_session()

        elif msg_type == "TOGGLE_INTERACT":
            await self._handle_toggle_interact()

        elif msg_type == "SPEAKER_FINISHED":
            await self._handle_speaker_finished()

        elif msg_type == "OVERRIDE_MESSAGE":
            await self._handle_override_message(payload)

        else:
            logger.warning("Unknown message type: %s", msg_type)

    # --- Message handlers ---

    async def _handle_load_agenda(self, payload: dict) -> None:
        """Load and validate the conference agenda."""
        try:
            agenda = self._agenda_mgr.load_from_dict(payload.get("agenda", payload))
            self._sm.context.agenda = agenda
            await self._send({
                "type": "AGENDA_LOADED",
                "payload": {
                    "title": agenda.title,
                    "total_sessions": len(agenda.sessions),
                    "total_duration": agenda.total_duration_minutes,
                    "sessions": [
                        {
                            "id": s.id,
                            "type": s.type.value,
                            "title": s.title,
                            "duration_minutes": s.duration_minutes,
                            "speaker_name": s.speaker.name if s.speaker else None,
                        }
                        for s in agenda.sessions
                    ],
                },
            })
        except Exception as e:
            await self._send_error(f"Agenda yukleme hatasi: {e}")

    async def _handle_connect_ai(self) -> None:
        """Connect to Gemini Live API."""
        agenda = self._sm.context.agenda
        if not agenda:
            await self._send_error("Once agenda yukleyin")
            return

        try:
            initial_prompt = build_prompt(ConferenceState.IDLE, self._sm.context)

            # Set up event handler BEFORE connecting
            self._event_handler = RealtimeEventHandler(
                gemini=self._gemini,
                state_machine=self._sm,
                tool_handler=self._tool_handler,
                on_browser_message=self._send,
            )

            # Connect to Gemini with initial setup
            await self._gemini.connect(
                instructions=initial_prompt,
                voice=agenda.moderator_voice,
                tools=CONFERENCE_TOOLS,
            )

            await self._send({
                "type": "AI_CONNECTED",
                "payload": {"model": get_settings().gemini_model},
            })
        except Exception as e:
            logger.exception("Gemini connection failed")
            await self._send_error(f"Gemini baglanti hatasi: {e}")

    async def _handle_start_conference(self) -> None:
        """Start the conference."""
        if not self._sm.context.agenda:
            await self._send_error("Once agenda yukleyin")
            return

        if not self._gemini.is_connected:
            await self._send_error("Once AI'ya baglanin (CONNECT_AI)")
            return

        await self._sm.handle_start()

    async def _handle_pause(self) -> None:
        self._sm.context.is_paused = True
        self._timer.pause()
        await self._send({
            "type": "STATE_UPDATE",
            "payload": {
                "state": self._sm.current_state.value,
                "session_index": self._sm.context.current_session_index,
                "is_paused": True,
            },
        })

    async def _handle_resume(self) -> None:
        self._sm.context.is_paused = False
        self._timer.resume()
        await self._send({
            "type": "STATE_UPDATE",
            "payload": {
                "state": self._sm.current_state.value,
                "session_index": self._sm.context.current_session_index,
                "is_paused": False,
            },
        })

    async def _handle_next_session(self) -> None:
        """Operator pressed Next button."""
        await self._sm.handle_operator_next()

    async def _handle_toggle_interact(self) -> None:
        """Toggle interaction mode."""
        await self._sm.handle_toggle_interact()

    async def _handle_speaker_finished(self) -> None:
        """Operator signals that the speaker has finished their talk."""
        state = self._sm.current_state
        if state in (
            ConferenceState.SPEAKER_ACTIVE,
            ConferenceState.INTERACTING,
            ConferenceState.TIME_WARNING,
        ):
            logger.info("Operator triggered speaker_finished from state=%s", state.value)
            await self._sm.handle_advance_session("speaker_finished")
        else:
            logger.warning("speaker_finished not valid from state=%s", state.value)

    async def _handle_override_message(self, payload: dict) -> None:
        """Make the agent say a specific message."""
        message = payload.get("message", "")
        if message and self._gemini.is_connected:
            await self._gemini.trigger_speech(message)

    # --- Timer callback ---

    async def _on_timer_tick(
        self,
        elapsed: float,
        remaining: float,
        total: float,
        progress: float,
    ) -> None:
        """Send timer tick to browser."""
        await self._send({
            "type": "TIMER_TICK",
            "payload": {
                "elapsed_seconds": round(elapsed),
                "remaining_seconds": round(remaining),
                "total_seconds": round(total),
                "session_index": self._sm.context.current_session_index,
                "progress_ratio": round(progress, 3),
            },
        })

    # --- State change timer management ---

    async def _on_state_change_timer(
        self, state: ConferenceState, context: Any
    ) -> None:
        """Start/stop timer based on state transitions."""
        timed_states = {
            ConferenceState.SPEAKER_ACTIVE,
            ConferenceState.INTERACTING,
            ConferenceState.TIME_WARNING,
            ConferenceState.BREAK_ACTIVE,
        }

        if state in timed_states:
            self._timer.start()
        elif state in (
            ConferenceState.THANKING_SPEAKER,
            ConferenceState.TRANSITIONING,
            ConferenceState.ENDED,
        ):
            self._timer.stop()

    # --- Utilities ---

    async def _send(self, message: dict[str, Any]) -> None:
        """Send a message to the browser."""
        try:
            await self._ws.send_json(message)
        except Exception:
            logger.warning("Failed to send message to browser")

    async def _send_error(self, error: str) -> None:
        await self._send({"type": "ERROR", "payload": {"message": error}})

    async def _cleanup(self) -> None:
        """Clean up all resources."""
        self._timer.stop()
        await self._gemini.disconnect()
