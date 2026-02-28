"""Tests for the prompt builder."""

from server.models.agenda import ConferenceAgenda, ConferenceSession, SessionType, SpeakerInfo
from server.models.state import ConferenceContext, ConferenceState
from server.prompts.builder import build_prompt


def make_context() -> ConferenceContext:
    agenda = ConferenceAgenda(
        id="test",
        title="Test Zirvesi",
        date="2026-03-15",
        venue="Test Mekan",
        language="tr",
        moderator_voice="coral",
        sessions=[
            ConferenceSession(
                id="opening",
                type=SessionType.OPENING,
                title="Acilis",
                duration_minutes=5,
                notes="Hosgeldiniz deyin.",
            ),
            ConferenceSession(
                id="keynote",
                type=SessionType.KEYNOTE,
                title="YZ ve Gelecek",
                duration_minutes=30,
                speaker=SpeakerInfo(
                    name="Dr. Ayse",
                    title="Profesor",
                    organization="TestUni",
                    talk_title="Yapay Zeka",
                    bio="YZ uzmani",
                ),
            ),
            ConferenceSession(
                id="closing",
                type=SessionType.CLOSING,
                title="Kapanis",
                duration_minutes=3,
            ),
        ],
    )
    return ConferenceContext(agenda=agenda)


class TestPromptBuilder:
    def test_idle_prompt_contains_conference_info(self):
        ctx = make_context()
        prompt = build_prompt(ConferenceState.IDLE, ctx)
        assert "Test Zirvesi" in prompt
        assert "Test Mekan" in prompt

    def test_opening_prompt_has_notes(self):
        ctx = make_context()
        prompt = build_prompt(ConferenceState.OPENING, ctx)
        assert "Hosgeldiniz" in prompt
        assert "ACILIS" in prompt

    def test_introducing_prompt_has_speaker_info(self):
        ctx = make_context()
        ctx.current_session_index = 1
        prompt = build_prompt(ConferenceState.INTRODUCING_SPEAKER, ctx)
        assert "Dr. Ayse" in prompt
        assert "Profesor" in prompt
        assert "TestUni" in prompt
        assert "Yapay Zeka" in prompt

    def test_speaker_active_has_duration(self):
        ctx = make_context()
        ctx.current_session_index = 1
        prompt = build_prompt(ConferenceState.SPEAKER_ACTIVE, ctx)
        assert "30" in prompt
        assert "SESSIZ KAL" in prompt

    def test_closing_prompt(self):
        ctx = make_context()
        ctx.current_session_index = 2
        prompt = build_prompt(ConferenceState.CLOSING, ctx)
        assert "KAPANIS" in prompt

    def test_no_agenda_returns_error(self):
        ctx = ConferenceContext()
        prompt = build_prompt(ConferenceState.IDLE, ctx)
        assert "yuklenmedi" in prompt
