"""Handles Gemini Live API events and bridges them to the state machine.

Gemini event flow:
- serverContent.modelTurn.parts[].inlineData -> audio chunks
- serverContent.outputTranscription -> what AI said (text)
- serverContent.inputTranscription -> what user said (text)
- serverContent.turnComplete -> AI finished speaking
- toolCall.functionCalls -> function calling
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from server.conference.state_machine import ConferenceStateMachine
from server.conference.tools import CONFERENCE_TOOLS, ToolHandler
from server.models.state import ACTIVE_SPEAKING_STATES, SILENT_STATES, ConferenceState
from server.prompts.builder import build_prompt
from server.realtime.sideband import GeminiLiveConnection

logger = logging.getLogger(__name__)

BrowserCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class RealtimeEventHandler:
    """Handles events from Gemini Live API and coordinates
    between the state machine, Gemini connection, and browser client.
    """

    def __init__(
        self,
        gemini: GeminiLiveConnection,
        state_machine: ConferenceStateMachine,
        tool_handler: ToolHandler,
        on_browser_message: BrowserCallback,
        on_audio_out: Callable[[bytes], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._gemini = gemini
        self._sm = state_machine
        self._tools = tool_handler
        self._send_to_browser = on_browser_message
        self._on_audio_out = on_audio_out

        # Register Gemini event handlers
        self._gemini.on_event("audio", self._on_audio)
        self._gemini.on_event("output_transcription", self._on_output_transcription)
        self._gemini.on_event("input_transcription", self._on_input_transcription)
        self._gemini.on_event("turn_complete", self._on_turn_complete)
        self._gemini.on_event("interrupted", self._on_interrupted)
        self._gemini.on_event("toolCall", self._on_tool_call)
        self._gemini.on_event("setupComplete", self._on_setup_complete)

        # Register state change callback
        self._sm.on_state_change(self._on_state_change)

    async def _on_setup_complete(self, event: dict) -> None:
        logger.info("Gemini Live session setup complete")

    async def _on_audio(self, event: dict) -> None:
        """Audio chunk from Gemini (AI voice output).

        Forward base64 audio to browser for playback.
        """
        if self._on_audio_out:
            import base64
            audio_b64 = event.get("data", "")
            audio_bytes = base64.b64decode(audio_b64)
            await self._on_audio_out(audio_bytes)

        # Also send as JSON via browser callback
        await self._send_to_browser({
            "type": "AUDIO_DATA",
            "payload": {
                "data": event.get("data", ""),
                "mimeType": event.get("mimeType", "audio/pcm;rate=24000"),
            },
        })

    async def _on_output_transcription(self, event: dict) -> None:
        """Transcript of what the AI said."""
        text = event.get("text", "")
        if text:
            await self._send_to_browser({
                "type": "TRANSCRIPT",
                "payload": {"text": text},
            })

    async def _on_input_transcription(self, event: dict) -> None:
        """Transcript of what the user/audience said."""
        text = event.get("text", "")
        if text:
            logger.info("Input transcription: %s", text[:120])
            # Also send input transcription to browser for visibility
            await self._send_to_browser({
                "type": "TRANSCRIPT",
                "payload": {"text": f"[Salon] {text}"},
            })

    async def _on_turn_complete(self, event: dict) -> None:
        """AI finished its turn (done speaking)."""
        state = self._sm.current_state
        logger.info("Turn complete in state=%s", state)

        await self._send_to_browser({
            "type": "MODERATOR_STATUS",
            "payload": {"status": "idle"},
        })

        # IMPORTANT: In silent states, do NOT auto-advance the state machine.
        # The agent might respond to a direct question in SPEAKER_ACTIVE,
        # but that doesn't mean the speaker is done.
        if state in SILENT_STATES:
            logger.debug("Turn complete in silent state %s - not advancing", state.value)
            return

        # For active speaking states, let state machine handle transition
        await self._sm.handle_response_done()

    async def _on_interrupted(self, event: dict) -> None:
        """AI was interrupted (user started speaking)."""
        logger.debug("AI interrupted by user speech")
        await self._send_to_browser({
            "type": "MODERATOR_STATUS",
            "payload": {"status": "listening"},
        })

    async def _on_tool_call(self, event: dict) -> None:
        """Gemini wants to call function(s)."""
        tool_call = event.get("toolCall", {})
        function_calls = tool_call.get("functionCalls", [])

        for fc in function_calls:
            name = fc.get("name", "")
            call_id = fc.get("id", "")
            args = fc.get("args", {})

            logger.info("Gemini function call: %s(%s) id=%s", name, args, call_id)

            # Execute the tool
            result = await self._tools.handle_function_call(name, args)

            # Send result back to Gemini
            await self._gemini.send_tool_response(call_id, result)

    async def _on_state_change(
        self, state: ConferenceState, context: Any
    ) -> None:
        """Called when the state machine transitions. Updates prompts."""
        logger.info("State changed to: %s", state.value)

        # Build new prompt for this state
        new_prompt = build_prompt(state, self._sm.context)

        # Send updated instructions to Gemini
        await self._gemini.update_instructions(new_prompt)

        # Notify browser of state change
        session = self._sm.context.current_session
        await self._send_to_browser({
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
        })

        # If entering an active speaking state, trigger AI to speak
        # BUT NOT for silent states (SPEAKER_ACTIVE, BREAK_ACTIVE, etc.)
        if state in ACTIVE_SPEAKING_STATES and state not in SILENT_STATES:
            await self._send_to_browser({
                "type": "MODERATOR_STATUS",
                "payload": {"status": "speaking"},
            })
            await self._gemini.trigger_speech(
                f"Sen simdi {state.value} durumundasin. Lutfen bu duruma uygun konusmani yap."
            )
        elif state in SILENT_STATES:
            await self._send_to_browser({
                "type": "MODERATOR_STATUS",
                "payload": {"status": "idle"},
            })
