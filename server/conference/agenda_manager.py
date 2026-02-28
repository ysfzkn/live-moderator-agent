"""Manages agenda loading, validation, and session information queries."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from server.models.agenda import ConferenceAgenda, ConferenceSession, SessionType
from server.models.state import ConferenceContext

logger = logging.getLogger(__name__)


class AgendaManager:
    """Loads and provides access to conference agenda data."""

    def __init__(self) -> None:
        self.agenda: ConferenceAgenda | None = None

    def load_from_dict(self, data: dict) -> ConferenceAgenda:
        """Load and validate agenda from a dictionary."""
        self.agenda = ConferenceAgenda.model_validate(data)
        logger.info(
            "Agenda loaded: %s (%d sessions, %d minutes)",
            self.agenda.title,
            len(self.agenda.sessions),
            self.agenda.total_duration_minutes,
        )
        return self.agenda

    def load_from_file(self, path: str | Path) -> ConferenceAgenda:
        """Load and validate agenda from a JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return self.load_from_dict(data)

    def get_session_info(self, context: ConferenceContext, which: str = "current") -> dict:
        """Get formatted session info for the AI model."""
        if which == "next":
            session = context.next_session
            index = context.current_session_index + 1
        else:
            session = context.current_session
            index = context.current_session_index

        if session is None:
            return {"error": "Oturum bulunamadi"}

        info: dict = {
            "session_index": index,
            "session_id": session.id,
            "type": session.type.value,
            "title": session.title,
            "duration_minutes": session.duration_minutes,
        }

        if session.speaker:
            info["speaker"] = {
                "name": session.speaker.name,
                "title": session.speaker.title,
                "organization": session.speaker.organization,
                "talk_title": session.speaker.talk_title,
                "bio": session.speaker.bio or "",
            }

        if session.panelists:
            info["panelists"] = [
                {
                    "name": p.name,
                    "title": p.title,
                    "organization": p.organization,
                }
                for p in session.panelists
            ]

        if session.notes:
            info["notes"] = session.notes

        if session.description:
            info["description"] = session.description

        return info

    def get_time_remaining(self, context: ConferenceContext) -> dict:
        """Get time remaining for the current session."""
        session = context.current_session
        if session is None:
            return {"error": "Aktif oturum yok"}

        total = session.duration_minutes * 60
        remaining = context.remaining_seconds
        elapsed = context.elapsed_seconds

        return {
            "session_title": session.title,
            "total_seconds": total,
            "elapsed_seconds": round(elapsed),
            "remaining_seconds": round(remaining),
            "remaining_minutes": round(remaining / 60, 1),
            "progress_percent": round(context.progress_ratio * 100, 1),
        }
