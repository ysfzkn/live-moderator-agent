"""Conference moderator state machine using the `transitions` library."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from transitions import Machine

from server.models.agenda import SessionType
from server.models.state import (
    ACTIVE_SPEAKING_STATES,
    ConferenceContext,
    ConferenceState,
)

logger = logging.getLogger(__name__)

# All valid states
STATES = [s.value for s in ConferenceState]

# Transition definitions: (trigger, source, dest, conditions)
TRANSITIONS = [
    # --- Start ---
    {"trigger": "start_conference", "source": ConferenceState.IDLE.value, "dest": ConferenceState.OPENING.value},
    # --- Opening complete ---
    {"trigger": "opening_complete", "source": ConferenceState.OPENING.value, "dest": ConferenceState.TRANSITIONING.value},
    # --- Introducing speaker ---
    {"trigger": "introduce_speaker", "source": ConferenceState.TRANSITIONING.value, "dest": ConferenceState.INTRODUCING_SPEAKER.value},
    # --- Introduction complete -> speaker active ---
    {"trigger": "introduction_complete", "source": ConferenceState.INTRODUCING_SPEAKER.value, "dest": ConferenceState.SPEAKER_ACTIVE.value},
    # --- Speaker interactions ---
    {"trigger": "enter_interaction", "source": ConferenceState.SPEAKER_ACTIVE.value, "dest": ConferenceState.INTERACTING.value},
    {"trigger": "exit_interaction", "source": ConferenceState.INTERACTING.value, "dest": ConferenceState.SPEAKER_ACTIVE.value},
    # --- Time warning ---
    {"trigger": "time_warning", "source": ConferenceState.SPEAKER_ACTIVE.value, "dest": ConferenceState.TIME_WARNING.value},
    {"trigger": "time_warning", "source": ConferenceState.INTERACTING.value, "dest": ConferenceState.TIME_WARNING.value},
    {"trigger": "warning_delivered", "source": ConferenceState.TIME_WARNING.value, "dest": ConferenceState.SPEAKER_ACTIVE.value},
    # --- Speaker finished ---
    {"trigger": "speaker_finished", "source": ConferenceState.SPEAKER_ACTIVE.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    {"trigger": "speaker_finished", "source": ConferenceState.INTERACTING.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    {"trigger": "speaker_finished", "source": ConferenceState.TIME_WARNING.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    # --- Thank complete -> transitioning ---
    {"trigger": "thank_complete", "source": ConferenceState.THANKING_SPEAKER.value, "dest": ConferenceState.TRANSITIONING.value},
    # --- Break flow ---
    {"trigger": "announce_break", "source": ConferenceState.TRANSITIONING.value, "dest": ConferenceState.BREAK_ANNOUNCEMENT.value},
    {"trigger": "break_announced", "source": ConferenceState.BREAK_ANNOUNCEMENT.value, "dest": ConferenceState.BREAK_ACTIVE.value},
    {"trigger": "break_ending_soon", "source": ConferenceState.BREAK_ACTIVE.value, "dest": ConferenceState.BREAK_ENDING.value},
    {"trigger": "break_over", "source": ConferenceState.BREAK_ENDING.value, "dest": ConferenceState.TRANSITIONING.value},
    {"trigger": "break_over", "source": ConferenceState.BREAK_ACTIVE.value, "dest": ConferenceState.TRANSITIONING.value},
    # --- Closing ---
    {"trigger": "start_closing", "source": ConferenceState.TRANSITIONING.value, "dest": ConferenceState.CLOSING.value},
    {"trigger": "closing_complete", "source": ConferenceState.CLOSING.value, "dest": ConferenceState.ENDED.value},
    # --- Operator overrides (from any active state) ---
    {"trigger": "operator_next", "source": ConferenceState.SPEAKER_ACTIVE.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    {"trigger": "operator_next", "source": ConferenceState.INTERACTING.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    {"trigger": "operator_next", "source": ConferenceState.TIME_WARNING.value, "dest": ConferenceState.THANKING_SPEAKER.value},
    {"trigger": "operator_next", "source": ConferenceState.BREAK_ACTIVE.value, "dest": ConferenceState.TRANSITIONING.value},
    {"trigger": "operator_next", "source": ConferenceState.OPENING.value, "dest": ConferenceState.TRANSITIONING.value},
]

# Callback type
AsyncCallback = Callable[[ConferenceState, ConferenceContext], Coroutine[Any, Any, None]]


class ConferenceStateMachine:
    """Manages the conference flow through a state machine.

    Provides async callbacks on state entry/exit for the realtime session
    to update prompts and control audio behavior.
    """

    def __init__(self) -> None:
        self.context = ConferenceContext()
        self._on_state_change_callbacks: list[AsyncCallback] = []
        self._machine = Machine(
            model=self,
            states=STATES,
            transitions=TRANSITIONS,
            initial=ConferenceState.IDLE.value,
            auto_transitions=False,
            send_event=False,
        )

    @property
    def current_state(self) -> ConferenceState:
        return ConferenceState(self.state)  # type: ignore[attr-defined]

    @property
    def is_speaking_state(self) -> bool:
        return self.current_state in ACTIVE_SPEAKING_STATES

    def on_state_change(self, callback: AsyncCallback) -> None:
        """Register a callback for state changes."""
        self._on_state_change_callbacks.append(callback)

    async def _notify_state_change(self) -> None:
        """Notify all registered callbacks about the state change."""
        state = self.current_state
        for cb in self._on_state_change_callbacks:
            try:
                await cb(state, self.context)
            except Exception:
                logger.exception("State change callback failed for state=%s", state)

    # --- High-level actions ---

    async def handle_start(self) -> None:
        """Start the conference."""
        self.start_conference()  # type: ignore[attr-defined]
        await self._notify_state_change()

    async def handle_response_done(self) -> None:
        """Called when the AI model finishes speaking. Advances the state machine."""
        state = self.current_state

        if state == ConferenceState.OPENING:
            self.opening_complete()  # type: ignore[attr-defined]
            await self._notify_state_change()
            await self._route_transition()

        elif state == ConferenceState.INTRODUCING_SPEAKER:
            self.introduction_complete()  # type: ignore[attr-defined]
            await self._notify_state_change()

        elif state == ConferenceState.TIME_WARNING:
            self.warning_delivered()  # type: ignore[attr-defined]
            await self._notify_state_change()

        elif state == ConferenceState.THANKING_SPEAKER:
            self.thank_complete()  # type: ignore[attr-defined]
            await self._notify_state_change()
            await self._route_transition()

        elif state == ConferenceState.BREAK_ANNOUNCEMENT:
            self.break_announced()  # type: ignore[attr-defined]
            await self._notify_state_change()

        elif state == ConferenceState.BREAK_ENDING:
            self.context.current_session_index += 1
            self.context.elapsed_seconds = 0
            self.context.time_warning_issued = False
            self.break_over()  # type: ignore[attr-defined]
            await self._notify_state_change()
            await self._route_transition()

        elif state == ConferenceState.CLOSING:
            self.closing_complete()  # type: ignore[attr-defined]
            await self._notify_state_change()

    async def handle_advance_session(self, reason: str = "speaker_finished") -> None:
        """Called by function calling tool to advance to next session."""
        state = self.current_state

        if state in (
            ConferenceState.SPEAKER_ACTIVE,
            ConferenceState.INTERACTING,
            ConferenceState.TIME_WARNING,
        ):
            self.speaker_finished()  # type: ignore[attr-defined]
            await self._notify_state_change()

        elif state == ConferenceState.OPENING:
            self.opening_complete()  # type: ignore[attr-defined]
            await self._notify_state_change()
            await self._route_transition()

    async def handle_operator_next(self) -> None:
        """Operator pressed Next button."""
        try:
            self.operator_next()  # type: ignore[attr-defined]
            await self._notify_state_change()
        except Exception:
            logger.warning("operator_next not valid from state=%s", self.current_state)

    async def handle_time_warning(self) -> None:
        """Timer triggered time warning."""
        state = self.current_state
        if state in (ConferenceState.SPEAKER_ACTIVE, ConferenceState.INTERACTING):
            self.context.time_warning_issued = True
            self.time_warning()  # type: ignore[attr-defined]
            await self._notify_state_change()

    async def handle_time_expired(self) -> None:
        """Timer expired for current session."""
        state = self.current_state
        if state in (
            ConferenceState.SPEAKER_ACTIVE,
            ConferenceState.INTERACTING,
            ConferenceState.TIME_WARNING,
        ):
            self.speaker_finished()  # type: ignore[attr-defined]
            await self._notify_state_change()
        elif state == ConferenceState.BREAK_ACTIVE:
            self.break_ending_soon()  # type: ignore[attr-defined]
            await self._notify_state_change()

    async def handle_toggle_interact(self) -> None:
        """Toggle between SPEAKER_ACTIVE and INTERACTING."""
        state = self.current_state
        if state == ConferenceState.SPEAKER_ACTIVE:
            self.enter_interaction()  # type: ignore[attr-defined]
            await self._notify_state_change()
        elif state == ConferenceState.INTERACTING:
            self.exit_interaction()  # type: ignore[attr-defined]
            await self._notify_state_change()

    # --- Internal routing ---

    async def _route_transition(self) -> None:
        """When in TRANSITIONING, decide where to go next based on the agenda."""
        if self.current_state != ConferenceState.TRANSITIONING:
            return

        # Move index to next session (if coming from a completed session)
        ctx = self.context
        next_session = ctx.next_session

        if next_session is None:
            # No more sessions -> closing
            self.start_closing()  # type: ignore[attr-defined]
            await self._notify_state_change()
            return

        # Advance to next session
        ctx.current_session_index += 1
        ctx.elapsed_seconds = 0
        ctx.time_warning_issued = False
        session = ctx.current_session

        if session is None:
            self.start_closing()  # type: ignore[attr-defined]
            await self._notify_state_change()
            return

        if session.type == SessionType.BREAK:
            self.announce_break()  # type: ignore[attr-defined]
            await self._notify_state_change()
        elif session.type == SessionType.CLOSING:
            self.start_closing()  # type: ignore[attr-defined]
            await self._notify_state_change()
        elif session.type in (
            SessionType.KEYNOTE,
            SessionType.TALK,
            SessionType.PANEL,
            SessionType.QA,
        ):
            self.introduce_speaker()  # type: ignore[attr-defined]
            await self._notify_state_change()
        else:
            # Opening type mid-conference (unlikely) -> treat as talk
            self.introduce_speaker()  # type: ignore[attr-defined]
            await self._notify_state_change()
