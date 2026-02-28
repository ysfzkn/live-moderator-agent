from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


# --- Client -> Server ---


class ClientMessage(BaseModel):
    type: Literal[
        "LOAD_AGENDA",
        "CONNECT_AI",
        "START_CONFERENCE",
        "PAUSE",
        "RESUME",
        "NEXT_SESSION",
        "SPEAKER_FINISHED",
        "TOGGLE_INTERACT",
        "OVERRIDE_MESSAGE",
    ]
    payload: Optional[dict[str, Any]] = None


# --- Server -> Client ---


class StateUpdatePayload(BaseModel):
    state: str
    session_index: int
    session_title: Optional[str] = None
    speaker_name: Optional[str] = None
    is_paused: bool = False


class TimerTickPayload(BaseModel):
    elapsed_seconds: float
    remaining_seconds: float
    total_seconds: float
    session_index: int
    progress_ratio: float


class AudioDataPayload(BaseModel):
    data: str  # base64-encoded PCM audio
    mimeType: str = "audio/pcm;rate=24000"


class ServerMessage(BaseModel):
    type: Literal[
        "AI_CONNECTED",
        "AUDIO_DATA",
        "STATE_UPDATE",
        "TIMER_TICK",
        "MODERATOR_STATUS",
        "TRANSCRIPT",
        "AGENDA_LOADED",
        "ERROR",
        "CONFERENCE_ENDED",
    ]
    payload: dict[str, Any]
