"""Tests for tool handler."""

import pytest

from server.conference.agenda_manager import AgendaManager
from server.conference.state_machine import ConferenceStateMachine
from server.conference.tools import ToolHandler
from server.models.agenda import ConferenceAgenda, ConferenceSession, SessionType, SpeakerInfo


def make_test_setup():
    sm = ConferenceStateMachine()
    agenda = ConferenceAgenda(
        id="test",
        title="Test",
        date="2026-01-01",
        venue="Test",
        language="tr",
        sessions=[
            ConferenceSession(id="opening", type=SessionType.OPENING, title="Acilis", duration_minutes=3),
            ConferenceSession(
                id="talk",
                type=SessionType.TALK,
                title="Test Talk",
                duration_minutes=20,
                speaker=SpeakerInfo(
                    name="Test Speaker",
                    title="Dr",
                    organization="Org",
                    talk_title="Topic",
                ),
            ),
        ],
    )
    sm.context.agenda = agenda
    am = AgendaManager()
    am.agenda = agenda
    handler = ToolHandler(sm, am)
    return sm, handler


class TestToolHandler:
    @pytest.mark.asyncio
    async def test_check_time_remaining(self):
        sm, handler = make_test_setup()
        sm.context.current_session_index = 1
        sm.context.elapsed_seconds = 600

        result = await handler.handle_function_call("check_time_remaining", {})
        assert result["total_seconds"] == 1200
        assert result["elapsed_seconds"] == 600
        assert result["remaining_seconds"] == 600

    @pytest.mark.asyncio
    async def test_get_session_info_current(self):
        sm, handler = make_test_setup()
        sm.context.current_session_index = 1

        result = await handler.handle_function_call("get_session_info", {"which": "current"})
        assert result["title"] == "Test Talk"
        assert result["speaker"]["name"] == "Test Speaker"

    @pytest.mark.asyncio
    async def test_get_session_info_next(self):
        sm, handler = make_test_setup()
        sm.context.current_session_index = 0

        result = await handler.handle_function_call("get_session_info", {"which": "next"})
        assert result["title"] == "Test Talk"

    @pytest.mark.asyncio
    async def test_announce_time_warning(self):
        sm, handler = make_test_setup()
        sm.context.current_session_index = 1

        result = await handler.handle_function_call(
            "announce_time_warning", {"minutes_remaining": 5}
        )
        assert result["status"] == "ok"
        assert result["minutes_remaining"] == 5
        assert "Test Speaker" in result["speaker_name"]

    @pytest.mark.asyncio
    async def test_unknown_function(self):
        _, handler = make_test_setup()
        result = await handler.handle_function_call("unknown_func", {})
        assert "error" in result
