from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from server.models.agenda import ConferenceAgenda


class ConferenceState(str, Enum):
    IDLE = "idle"
    OPENING = "opening"
    INTRODUCING_SPEAKER = "introducing_speaker"
    SPEAKER_ACTIVE = "speaker_active"
    INTERACTING = "interacting"
    TIME_WARNING = "time_warning"
    THANKING_SPEAKER = "thanking_speaker"
    TRANSITIONING = "transitioning"
    BREAK_ANNOUNCEMENT = "break_announcement"
    BREAK_ACTIVE = "break_active"
    BREAK_ENDING = "break_ending"
    CLOSING = "closing"
    ENDED = "ended"


# States where agent actively speaks (auto_respond = True)
ACTIVE_SPEAKING_STATES = {
    ConferenceState.OPENING,
    ConferenceState.INTRODUCING_SPEAKER,
    ConferenceState.INTERACTING,
    ConferenceState.TIME_WARNING,
    ConferenceState.THANKING_SPEAKER,
    ConferenceState.TRANSITIONING,
    ConferenceState.BREAK_ANNOUNCEMENT,
    ConferenceState.BREAK_ENDING,
    ConferenceState.CLOSING,
}

# States where agent stays silent (auto_respond = False)
SILENT_STATES = {
    ConferenceState.IDLE,
    ConferenceState.SPEAKER_ACTIVE,
    ConferenceState.BREAK_ACTIVE,
    ConferenceState.ENDED,
}


@dataclass
class ConferenceContext:
    agenda: Optional[ConferenceAgenda] = None
    current_session_index: int = 0
    session_start_time: Optional[float] = None
    elapsed_seconds: float = 0.0
    time_warning_issued: bool = False
    conference_start_time: Optional[float] = None
    is_paused: bool = False

    @property
    def current_session(self):
        if self.agenda and 0 <= self.current_session_index < len(self.agenda.sessions):
            return self.agenda.sessions[self.current_session_index]
        return None

    @property
    def next_session(self):
        if self.agenda and self.current_session_index + 1 < len(self.agenda.sessions):
            return self.agenda.sessions[self.current_session_index + 1]
        return None

    @property
    def remaining_seconds(self) -> float:
        if self.current_session:
            total = self.current_session.duration_minutes * 60
            return max(0, total - self.elapsed_seconds)
        return 0

    @property
    def progress_ratio(self) -> float:
        if self.current_session and self.current_session.duration_minutes > 0:
            total = self.current_session.duration_minutes * 60
            return min(1.0, self.elapsed_seconds / total)
        return 0

    @property
    def has_next_session(self) -> bool:
        if self.agenda:
            return self.current_session_index + 1 < len(self.agenda.sessions)
        return False
