"""Asyncio-based precision timer for tracking session durations."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable, Coroutine, Any

from server.config import get_settings

if TYPE_CHECKING:
    from server.conference.state_machine import ConferenceStateMachine

logger = logging.getLogger(__name__)

# Callback types
TickCallback = Callable[[float, float, float, float], Coroutine[Any, Any, None]]
# args: elapsed_seconds, remaining_seconds, total_seconds, progress_ratio


class SessionTimer:
    """Manages timing for conference sessions.

    Fires tick callbacks every second and triggers time warnings
    and expiration events on the state machine.
    """

    def __init__(self, state_machine: ConferenceStateMachine) -> None:
        self._sm = state_machine
        self._task: asyncio.Task | None = None
        self._tick_callbacks: list[TickCallback] = []
        self._paused = False
        self._pause_accumulated = 0.0
        self._pause_start: float | None = None

    def on_tick(self, callback: TickCallback) -> None:
        """Register a callback for timer ticks."""
        self._tick_callbacks.append(callback)

    def start(self) -> None:
        """Start the timer for the current session."""
        self.stop()
        self._paused = False
        self._pause_accumulated = 0.0
        self._pause_start = None
        self._sm.context.session_start_time = time.monotonic()
        self._sm.context.elapsed_seconds = 0
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Stop the timer."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def pause(self) -> None:
        """Pause the timer."""
        if not self._paused:
            self._paused = True
            self._pause_start = time.monotonic()

    def resume(self) -> None:
        """Resume the timer."""
        if self._paused and self._pause_start is not None:
            self._pause_accumulated += time.monotonic() - self._pause_start
            self._pause_start = None
            self._paused = False

    async def _run(self) -> None:
        """Timer loop - ticks every second."""
        ctx = self._sm.context
        session = ctx.current_session
        if not session:
            return

        total_seconds = session.duration_minutes * 60.0

        try:
            while True:
                await asyncio.sleep(1.0)

                if self._paused:
                    continue

                if ctx.session_start_time is None:
                    continue

                elapsed = time.monotonic() - ctx.session_start_time - self._pause_accumulated
                ctx.elapsed_seconds = elapsed
                remaining = max(0.0, total_seconds - elapsed)
                progress = min(1.0, elapsed / total_seconds) if total_seconds > 0 else 1.0

                # Fire tick callbacks
                for cb in self._tick_callbacks:
                    try:
                        await cb(elapsed, remaining, total_seconds, progress)
                    except Exception:
                        logger.exception("Tick callback error")

                # Check warning threshold
                settings = get_settings()
                if (
                    not ctx.time_warning_issued
                    and progress >= settings.time_warning_threshold
                    and remaining > 0
                ):
                    await self._sm.handle_time_warning()

                # Check expiration
                if remaining <= 0:
                    await self._sm.handle_time_expired()
                    return  # Stop timer after expiration

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Timer error")
