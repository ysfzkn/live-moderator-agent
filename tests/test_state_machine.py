"""Tests for the conference state machine."""

import pytest

from server.conference.state_machine import ConferenceStateMachine
from server.models.agenda import ConferenceAgenda, ConferenceSession, SessionType, SpeakerInfo
from server.models.state import ConferenceState


def make_test_agenda() -> ConferenceAgenda:
    return ConferenceAgenda(
        id="test",
        title="Test Conference",
        date="2026-01-01",
        venue="Test Venue",
        language="tr",
        moderator_voice="coral",
        sessions=[
            ConferenceSession(
                id="opening",
                type=SessionType.OPENING,
                title="Acilis",
                duration_minutes=3,
            ),
            ConferenceSession(
                id="keynote",
                type=SessionType.KEYNOTE,
                title="Keynote",
                duration_minutes=20,
                speaker=SpeakerInfo(
                    name="Dr. Test",
                    title="Prof",
                    organization="TestCo",
                    talk_title="Test Talk",
                ),
            ),
            ConferenceSession(
                id="break",
                type=SessionType.BREAK,
                title="Mola",
                duration_minutes=10,
            ),
            ConferenceSession(
                id="closing",
                type=SessionType.CLOSING,
                title="Kapanis",
                duration_minutes=3,
            ),
        ],
    )


@pytest.fixture
def sm():
    machine = ConferenceStateMachine()
    machine.context.agenda = make_test_agenda()
    return machine


class TestInitialState:
    def test_starts_idle(self, sm: ConferenceStateMachine):
        assert sm.current_state == ConferenceState.IDLE

    def test_is_not_speaking(self, sm: ConferenceStateMachine):
        assert not sm.is_speaking_state


class TestStartConference:
    @pytest.mark.asyncio
    async def test_start_transitions_to_opening(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        assert sm.current_state == ConferenceState.OPENING

    @pytest.mark.asyncio
    async def test_opening_is_speaking(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        assert sm.is_speaking_state


class TestOpeningFlow:
    @pytest.mark.asyncio
    async def test_opening_complete_goes_to_transitioning_then_introducing(
        self, sm: ConferenceStateMachine
    ):
        await sm.handle_start()
        assert sm.current_state == ConferenceState.OPENING

        await sm.handle_response_done()
        # Should auto-route through TRANSITIONING to INTRODUCING_SPEAKER
        assert sm.current_state == ConferenceState.INTRODUCING_SPEAKER
        assert sm.context.current_session_index == 1  # keynote


class TestSpeakerFlow:
    @pytest.mark.asyncio
    async def test_introduction_complete_goes_to_speaker_active(
        self, sm: ConferenceStateMachine
    ):
        await sm.handle_start()
        await sm.handle_response_done()  # OPENING -> TRANSITIONING -> INTRODUCING
        assert sm.current_state == ConferenceState.INTRODUCING_SPEAKER

        await sm.handle_response_done()  # INTRODUCING -> SPEAKER_ACTIVE
        assert sm.current_state == ConferenceState.SPEAKER_ACTIVE
        assert not sm.is_speaking_state

    @pytest.mark.asyncio
    async def test_speaker_finished_goes_to_thanking(
        self, sm: ConferenceStateMachine
    ):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE

        await sm.handle_advance_session("speaker_finished")
        assert sm.current_state == ConferenceState.THANKING_SPEAKER

    @pytest.mark.asyncio
    async def test_time_warning(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE

        await sm.handle_time_warning()
        assert sm.current_state == ConferenceState.TIME_WARNING
        assert sm.context.time_warning_issued

    @pytest.mark.asyncio
    async def test_warning_delivered_returns_to_speaker_active(
        self, sm: ConferenceStateMachine
    ):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        await sm.handle_time_warning()   # -> TIME_WARNING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        assert sm.current_state == ConferenceState.SPEAKER_ACTIVE


class TestInteractionToggle:
    @pytest.mark.asyncio
    async def test_toggle_to_interacting(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE

        await sm.handle_toggle_interact()
        assert sm.current_state == ConferenceState.INTERACTING

    @pytest.mark.asyncio
    async def test_toggle_back_to_speaker_active(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        await sm.handle_toggle_interact()  # -> INTERACTING
        await sm.handle_toggle_interact()  # -> SPEAKER_ACTIVE
        assert sm.current_state == ConferenceState.SPEAKER_ACTIVE


class TestOperatorNext:
    @pytest.mark.asyncio
    async def test_operator_next_from_speaker_active(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE

        await sm.handle_operator_next()
        assert sm.current_state == ConferenceState.THANKING_SPEAKER


class TestBreakFlow:
    @pytest.mark.asyncio
    async def test_transition_to_break(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # OPENING -> INTRODUCING
        await sm.handle_response_done()  # INTRODUCING -> SPEAKER_ACTIVE
        await sm.handle_advance_session("speaker_finished")  # -> THANKING
        await sm.handle_response_done()  # THANKING -> TRANSITIONING -> BREAK_ANNOUNCEMENT
        assert sm.current_state == ConferenceState.BREAK_ANNOUNCEMENT
        assert sm.context.current_session_index == 2  # break session

    @pytest.mark.asyncio
    async def test_break_announcement_to_active(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        await sm.handle_advance_session("speaker_finished")  # -> THANKING
        await sm.handle_response_done()  # -> BREAK_ANNOUNCEMENT

        await sm.handle_response_done()  # -> BREAK_ACTIVE
        assert sm.current_state == ConferenceState.BREAK_ACTIVE


class TestClosingFlow:
    @pytest.mark.asyncio
    async def test_full_flow_to_closing(self, sm: ConferenceStateMachine):
        # Opening
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING (keynote)

        # Keynote
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        await sm.handle_advance_session("speaker_finished")  # -> THANKING
        await sm.handle_response_done()  # -> BREAK_ANNOUNCEMENT

        # Break
        await sm.handle_response_done()  # -> BREAK_ACTIVE
        await sm.handle_time_expired()   # -> BREAK_ENDING
        await sm.handle_response_done()  # -> TRANSITIONING -> CLOSING

        assert sm.current_state == ConferenceState.CLOSING

    @pytest.mark.asyncio
    async def test_closing_to_ended(self, sm: ConferenceStateMachine):
        await sm.handle_start()
        await sm.handle_response_done()  # -> INTRODUCING
        await sm.handle_response_done()  # -> SPEAKER_ACTIVE
        await sm.handle_advance_session("speaker_finished")  # -> THANKING
        await sm.handle_response_done()  # -> BREAK_ANNOUNCEMENT
        await sm.handle_response_done()  # -> BREAK_ACTIVE
        await sm.handle_time_expired()   # -> BREAK_ENDING
        await sm.handle_response_done()  # -> CLOSING
        await sm.handle_response_done()  # -> ENDED

        assert sm.current_state == ConferenceState.ENDED
