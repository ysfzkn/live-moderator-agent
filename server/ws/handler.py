"""WebSocket handler for browser client connections.

Orchestrates the conference by connecting the browser, state machine,
timer, and OpenAI Realtime API sideband.
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
from server.realtime.session import RealtimeSessionManager
from server.realtime.sideband import SidebandConnection

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

        # OpenAI components
        self._session_mgr = RealtimeSessionManager()
        self._sideband = SidebandConnection()
        self._event_handler: RealtimeEventHandler | None = None

        # Register timer tick callback
        self._timer.on_tick(self._on_timer_tick)

        # Register state change callback for timer management
        self._sm.on_state_change(self._on_state_change_timer)

    async def run(self) -> None:
        """Main loop: receive messages from the browser and handle them."""
        try:
            while True:
                raw = await self._ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send_error("Invalid JSON")
                    continue

                msg_type = msg.get("type", "")
                payload = msg.get("payload", {})

                await self._handle_message(msg_type, payload)

        except WebSocketDisconnect:
            logger.info("Browser disconnected")
        except Exception:
            logger.exception("WebSocket handler error")
        finally:
            await self._cleanup()

    async def _handle_message(self, msg_type: str, payload: dict) -> None:
        """Route incoming messages to appropriate handlers."""

        if msg_type == "LOAD_AGENDA":
            await self._handle_load_agenda(payload)

        elif msg_type == "REQUEST_TOKEN":
            await self._handle_request_token()

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

        elif msg_type == "OVERRIDE_MESSAGE":
            await self._handle_override_message(payload)

        elif msg_type == "SIDEBAND_CONNECT":
            await self._handle_sideband_connect(payload)

        else:
            logger.warning("Unknown message type: %s", msg_type)

    # --- Message handlers ---

    async def _handle_load_agenda(self, payload: dict) -> None:
        """Load and validate the conference agenda."""
        try:
            agenda = self._agenda_mgr.load_from_dict(payload.get("agenda", payload))
            self._sm.context.agenda = agenda
            await self._send(
                {
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
                }
            )
        except Exception as e:
            await self._send_error(f"Agenda yukleme hatasi: {e}")

    async def _handle_request_token(self) -> None:
        """Create an OpenAI Realtime session and return the ephemeral token."""
        agenda = self._sm.context.agenda
        if not agenda:
            await self._send_error("Once agenda yukleyin")
            return

        try:
            initial_prompt = build_prompt(ConferenceState.IDLE, self._sm.context)
            session_data = await self._session_mgr.create_session(
                voice=agenda.moderator_voice,
                instructions=initial_prompt,
                tools=CONFERENCE_TOOLS,
            )

            settings = get_settings()
            client_secret = session_data.get("client_secret", {})
            token = client_secret.get("value", "")

            await self._send(
                {
                    "type": "TOKEN_READY",
                    "payload": {
                        "token": token,
                        "endpoint_url": settings.azure_realtime_rest_url,
                        "voice": agenda.moderator_voice,
                    },
                }
            )
        except Exception as e:
            logger.exception("Token creation failed")
            await self._send_error(f"Token olusturma hatasi: {e}")

    async def _handle_sideband_connect(self, payload: dict) -> None:
        """Connect the sideband WebSocket using the call_id from WebRTC."""
        call_id = payload.get("call_id")
        if not call_id:
            await self._send_error("call_id gerekli")
            return

        try:
            # Connect sideband to the same session
            await self._sideband.connect(call_id=call_id)

            # Set up event handler (bridges sideband events to state machine)
            self._event_handler = RealtimeEventHandler(
                sideband=self._sideband,
                state_machine=self._sm,
                tool_handler=self._tool_handler,
                on_browser_message=self._send,
            )

            logger.info("Sideband connected with call_id=%s", call_id)
        except Exception as e:
            logger.exception("Sideband connection failed")
            await self._send_error(f"Sideband baglanti hatasi: {e}")

    async def _handle_start_conference(self) -> None:
        """Start the conference."""
        if not self._sm.context.agenda:
            await self._send_error("Once agenda yukleyin")
            return

        if not self._sideband.is_connected:
            # If no sideband, connect directly (without call_id)
            try:
                await self._sideband.connect()
                self._event_handler = RealtimeEventHandler(
                    sideband=self._sideband,
                    state_machine=self._sm,
                    tool_handler=self._tool_handler,
                    on_browser_message=self._send,
                )
            except Exception as e:
                await self._send_error(f"Sideband baglanti hatasi: {e}")
                return

        await self._sm.handle_start()

    async def _handle_pause(self) -> None:
        self._sm.context.is_paused = True
        self._timer.pause()
        await self._sideband.cancel_response()
        await self._send(
            {
                "type": "STATE_UPDATE",
                "payload": {
                    "state": self._sm.current_state.value,
                    "session_index": self._sm.context.current_session_index,
                    "is_paused": True,
                },
            }
        )

    async def _handle_resume(self) -> None:
        self._sm.context.is_paused = False
        self._timer.resume()
        await self._send(
            {
                "type": "STATE_UPDATE",
                "payload": {
                    "state": self._sm.current_state.value,
                    "session_index": self._sm.context.current_session_index,
                    "is_paused": False,
                },
            }
        )

    async def _handle_next_session(self) -> None:
        """Operator pressed Next button."""
        await self._sideband.cancel_response()
        await self._sm.handle_operator_next()

    async def _handle_toggle_interact(self) -> None:
        """Toggle interaction mode."""
        await self._sm.handle_toggle_interact()

    async def _handle_override_message(self, payload: dict) -> None:
        """Make the agent say a specific message."""
        message = payload.get("message", "")
        if message and self._sideband.is_connected:
            await self._sideband.create_response(
                instructions=f"Simdi su mesaji ilet: {message}"
            )

    # --- Timer callback ---

    async def _on_timer_tick(
        self,
        elapsed: float,
        remaining: float,
        total: float,
        progress: float,
    ) -> None:
        """Send timer tick to browser."""
        await self._send(
            {
                "type": "TIMER_TICK",
                "payload": {
                    "elapsed_seconds": round(elapsed),
                    "remaining_seconds": round(remaining),
                    "total_seconds": round(total),
                    "session_index": self._sm.context.current_session_index,
                    "progress_ratio": round(progress, 3),
                },
            }
        )

    # --- State change timer management ---

    async def _on_state_change_timer(
        self, state: ConferenceState, context: Any
    ) -> None:
        """Start/stop timer based on state transitions."""
        # States that need timing
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
        await self._sideband.disconnect()
        await self._session_mgr.close()
