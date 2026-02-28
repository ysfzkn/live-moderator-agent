"""Tests for the agenda manager."""

import pytest

from server.conference.agenda_manager import AgendaManager
from server.models.agenda import ConferenceAgenda
from server.models.state import ConferenceContext


SAMPLE_AGENDA = {
    "id": "test",
    "title": "Test Konferans",
    "date": "2026-01-01",
    "venue": "Test Mekan",
    "language": "tr",
    "moderator_voice": "coral",
    "sessions": [
        {
            "id": "opening",
            "type": "opening",
            "title": "Acilis",
            "duration_minutes": 5,
        },
        {
            "id": "talk-1",
            "type": "talk",
            "title": "Test Sunum",
            "duration_minutes": 20,
            "speaker": {
                "name": "Ali Veli",
                "title": "Muhendis",
                "organization": "TestCo",
                "talk_title": "Test Konusu",
                "bio": "Test bio",
            },
        },
        {
            "id": "closing",
            "type": "closing",
            "title": "Kapanis",
            "duration_minutes": 3,
        },
    ],
}


class TestAgendaLoading:
    def test_load_from_dict(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        assert isinstance(agenda, ConferenceAgenda)
        assert agenda.title == "Test Konferans"
        assert len(agenda.sessions) == 3

    def test_total_duration(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        assert agenda.total_duration_minutes == 28  # 5 + 20 + 3

    def test_speaker_sessions(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        assert len(agenda.speaker_sessions) == 1

    def test_invalid_agenda_raises(self):
        mgr = AgendaManager()
        with pytest.raises(Exception):
            mgr.load_from_dict({"invalid": True})

    def test_empty_sessions_raises(self):
        mgr = AgendaManager()
        with pytest.raises(Exception):
            mgr.load_from_dict({**SAMPLE_AGENDA, "sessions": []})


class TestSessionInfo:
    def test_get_current_session_info(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        ctx = ConferenceContext(agenda=agenda, current_session_index=1)

        info = mgr.get_session_info(ctx, "current")
        assert info["title"] == "Test Sunum"
        assert info["speaker"]["name"] == "Ali Veli"

    def test_get_next_session_info(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        ctx = ConferenceContext(agenda=agenda, current_session_index=0)

        info = mgr.get_session_info(ctx, "next")
        assert info["title"] == "Test Sunum"

    def test_no_next_session(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        ctx = ConferenceContext(agenda=agenda, current_session_index=2)

        info = mgr.get_session_info(ctx, "next")
        assert "error" in info


class TestTimeRemaining:
    def test_time_remaining(self):
        mgr = AgendaManager()
        agenda = mgr.load_from_dict(SAMPLE_AGENDA)
        ctx = ConferenceContext(
            agenda=agenda,
            current_session_index=1,
            elapsed_seconds=300,  # 5 minutes elapsed
        )

        info = mgr.get_time_remaining(ctx)
        assert info["total_seconds"] == 1200  # 20 min
        assert info["elapsed_seconds"] == 300
        assert info["remaining_seconds"] == 900
        assert info["progress_percent"] == 25.0
