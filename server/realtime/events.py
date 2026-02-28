"""Handles OpenAI Realtime server events and bridges them to the state machine."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine

from server.conference.state_machine import ConferenceStateMachine
from server.conference.tools import CONFERENCE_TOOLS, ToolHandler
from server.models.state import ACTIVE_SPEAKING_STATES, ConferenceState
from server.prompts.builder import build_prompt
from server.realtime.sideband import SidebandConnection

logger = logging.getLogger(__name__)

# Callback for sending messages to the browser
BrowserCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class RealtimeEventHandler:
    """Handles events from the OpenAI Realtime API and coordinates
    between the state machine, sideband, and browser client.
    """

    def __init__(
        self,
        sideband: SidebandConnection,
        state_machine: ConferenceStateMachine,
        tool_handler: ToolHandler,
        on_browser_message: BrowserCallback,
    ) -> None:
        self._sideband = sideband
        self._sm = state_machine
        self._tools = tool_handler
        self._send_to_browser = on_browser_message

        # Register event handlers on sideband
        self._sideband.on_event("response.done", self._on_response_done)
        self._sideband.on_event(
            "response.function_call_arguments.done",
            self._on_function_call,
        )
        self._sideband.on_event("session.created", self._on_session_created)
        self._sideband.on_event("session.updated", self._on_session_updated)
        self._sideband.on_event("error", self._on_error)
        self._sideband.on_event(
            "input_audio_buffer.speech_started", self._on_speech_started
        )
        self._sideband.on_event(
            "input_audio_buffer.speech_stopped", self._on_speech_stopped
        )
        self._sideband.on_event(
            "response.audio_transcript.done", self._on_transcript_done
        )

        # Register state change callback
        self._sm.on_state_change(self._on_state_change)

    async def _on_session_created(self, event: dict) -> None:
        logger.info("Realtime session created: %s", event.get("session", {}).get("id"))

    async def _on_session_updated(self, event: dict) -> None:
        logger.debug("Session updated")

    async def _on_response_done(self, event: dict) -> None:
        """Called when the AI finishes a response (speaking)."""
        logger.info("Response done in state=%s", self._sm.current_state)

        # Notify browser that moderator stopped speaking
        await self._send_to_browser(
            {"type": "MODERATOR_STATUS", "payload": {"status": "idle"}}
        )

        # Let the state machine handle the transition
        await self._sm.handle_response_done()

    async def _on_function_call(self, event: dict) -> None:
        """Called when the AI wants to call a function."""
        name = event.get("name", "")
        call_id = event.get("call_id", "")
        args_str = event.get("arguments", "{}")

        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            arguments = {}

        logger.info("Function call: %s(%s) call_id=%s", name, arguments, call_id)

        # Execute the tool
        result = await self._tools.handle_function_call(name, arguments)

        # Send result back to OpenAI
        await self._sideband.send_function_call_output(call_id, result)

        # Trigger a new response so the model can continue
        await self._sideband.create_response()

    async def _on_speech_started(self, event: dict) -> None:
        """User (microphone) started speaking."""
        await self._send_to_browser(
            {"type": "MODERATOR_STATUS", "payload": {"status": "listening"}}
        )

    async def _on_speech_stopped(self, event: dict) -> None:
        """User (microphone) stopped speaking."""
        pass  # Moderator will be "speaking" once response starts

    async def _on_transcript_done(self, event: dict) -> None:
        """Transcript of what the AI said is available."""
        transcript = event.get("transcript", "")
        if transcript:
            await self._send_to_browser(
                {"type": "TRANSCRIPT", "payload": {"text": transcript}}
            )

    async def _on_error(self, event: dict) -> None:
        """Error from Realtime API."""
        error = event.get("error", {})
        logger.error("Realtime API error: %s", error)
        await self._send_to_browser(
            {"type": "ERROR", "payload": {"message": str(error)}}
        )

    async def _on_state_change(
        self, state: ConferenceState, context: Any
    ) -> None:
        """Called when the state machine transitions. Updates prompts and controls."""
        logger.info("State changed to: %s", state.value)

        # Build new prompt for this state
        new_prompt = build_prompt(state, self._sm.context)

        # Determine turn detection config based on state
        if state in ACTIVE_SPEAKING_STATES:
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": "medium" if state == ConferenceState.INTERACTING else "low",
                "create_response": True,
                "interrupt_response": True,
            }
        else:
            # Silent states - don't auto-respond
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": "low",
                "create_response": False,
                "interrupt_response": False,
            }

        # Update the session via sideband
        await self._sideband.update_session(
            instructions=new_prompt,
            tools=CONFERENCE_TOOLS,
            turn_detection=turn_detection,
        )

        # Notify browser of state change
        session = self._sm.context.current_session
        await self._send_to_browser(
            {
                "type": "STATE_UPDATE",
                "payload": {
                    "state": state.value,
                    "session_index": self._sm.context.current_session_index,
                    "session_title": session.title if session else None,
                    "speaker_name": (
                        session.speaker.name
                        if session and session.speaker
                        else None
                    ),
                    "is_paused": self._sm.context.is_paused,
                },
            }
        )

        # If entering an active speaking state, trigger the AI to speak
        if state in ACTIVE_SPEAKING_STATES:
            await self._send_to_browser(
                {"type": "MODERATOR_STATUS", "payload": {"status": "speaking"}}
            )
            await self._sideband.create_response()
