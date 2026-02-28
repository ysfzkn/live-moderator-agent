from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SessionType(str, Enum):
    OPENING = "opening"
    KEYNOTE = "keynote"
    TALK = "talk"
    PANEL = "panel"
    BREAK = "break"
    QA = "qa"
    CLOSING = "closing"


class SpeakerInfo(BaseModel):
    name: str
    title: str
    organization: str
    talk_title: str
    bio: Optional[str] = None
    pronunciation_hint: Optional[str] = None


class ConferenceSession(BaseModel):
    id: str
    type: SessionType
    title: str
    duration_minutes: int = Field(gt=0)
    description: Optional[str] = None
    speaker: Optional[SpeakerInfo] = None
    panelists: Optional[list[SpeakerInfo]] = None
    notes: Optional[str] = None


class ConferenceAgenda(BaseModel):
    id: str
    title: str
    date: str
    venue: str
    language: str = "tr"
    moderator_voice: str = "coral"
    sessions: list[ConferenceSession] = Field(min_length=1)

    @property
    def total_duration_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.sessions)

    @property
    def speaker_sessions(self) -> list[ConferenceSession]:
        return [
            s
            for s in self.sessions
            if s.type
            in (SessionType.KEYNOTE, SessionType.TALK, SessionType.PANEL, SessionType.QA)
        ]
