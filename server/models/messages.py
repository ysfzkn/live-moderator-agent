from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


# --- Client -> Server ---


class ClientMessage(BaseModel):
    type: Literal[
        "LOAD_AGENDA",
        "START_CONFERENCE",
        "PAUSE",
        "RESUME",
        "NEXT_SESSION",
        "TOGGLE_INTERACT",
        "OVERRIDE_MESSAGE",
        "REQUEST_TOKEN",
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


class TokenReadyPayload(BaseModel):
    token: str
    endpoint_url: str
    voice: str


class ServerMessage(BaseModel):
    type: Literal[
        "TOKEN_READY",
        "STATE_UPDATE",
        "TIMER_TICK",
        "MODERATOR_STATUS",
        "TRANSCRIPT",
        "AGENDA_LOADED",
        "ERROR",
        "CONFERENCE_ENDED",
    ]
    payload: dict[str, Any]
