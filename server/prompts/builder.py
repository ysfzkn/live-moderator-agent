"""Dynamic prompt builder that assembles instructions based on current state and context."""

from __future__ import annotations

from server.models.agenda import SessionType
from server.models.state import ConferenceContext, ConferenceState
from server.prompts.system import BASE_SYSTEM_PROMPT
from server.prompts.templates import PANEL_ADDENDUM, QA_ADDENDUM, STATE_PROMPTS


def build_prompt(state: ConferenceState, context: ConferenceContext) -> str:
    """Build the full system prompt for the current state and context."""
    agenda = context.agenda
    if not agenda:
        return "Konferans ajandassi yuklenmedi."

    session = context.current_session
    next_session = context.next_session

    # Base prompt with conference info
    prompt = BASE_SYSTEM_PROMPT.format(
        conference_title=agenda.title,
        date=agenda.date,
        venue=agenda.venue,
        total_sessions=len(agenda.sessions),
        total_duration=agenda.total_duration_minutes,
    )

    # State-specific template
    state_template = STATE_PROMPTS.get(state, "")

    # Build replacement values
    replacements = _build_replacements(session, next_session, context)

    # Apply replacements safely - only replace known keys
    for key, value in replacements.items():
        placeholder = "{" + key + "}"
        state_template = state_template.replace(placeholder, str(value))

    prompt += "\n\n" + state_template

    # Add panel/QA addendums
    if session:
        if session.type == SessionType.PANEL and session.panelists:
            panelist_list = "\n".join(
                f"- {p.name} ({p.title}, {p.organization})"
                for p in session.panelists
            )
            prompt += PANEL_ADDENDUM.replace("{panelist_list}", panelist_list)

        if session.type == SessionType.QA:
            prompt += QA_ADDENDUM

    return prompt


def _build_replacements(session, next_session, context: ConferenceContext) -> dict:
    """Build a dict of replacement values for prompt templates."""
    r: dict[str, str] = {
        "conference_title": context.agenda.title if context.agenda else "",
    }

    if session:
        r["session_title"] = session.title
        r["duration_minutes"] = str(session.duration_minutes)
        r["break_title"] = session.title
        r["notes"] = f"\nOZEL NOTLAR: {session.notes}" if session.notes else ""

        if session.speaker:
            r["speaker_name"] = session.speaker.name
            r["speaker_title"] = session.speaker.title
            r["speaker_organization"] = session.speaker.organization
            r["talk_title"] = session.speaker.talk_title
            r["speaker_bio"] = session.speaker.bio or "Bilgi mevcut degil."
            r["speaker_info"] = (
                f"Konusmaci: {session.speaker.name} - {session.speaker.title}, "
                f"{session.speaker.organization}"
            )
        else:
            r["speaker_name"] = ""
            r["speaker_title"] = ""
            r["speaker_organization"] = ""
            r["talk_title"] = session.title
            r["speaker_bio"] = ""
            r["speaker_info"] = ""

    # Time-related
    remaining = context.remaining_seconds
    r["minutes_remaining"] = str(max(1, round(remaining / 60)))

    # Next session info
    if next_session:
        r["next_session_title"] = next_session.title
        r["next_session_type"] = next_session.type.value

        if next_session.speaker:
            r["next_speaker_info"] = (
                f"Sonraki konusmaci: {next_session.speaker.name} - "
                f"{next_session.speaker.title}, {next_session.speaker.organization}\n"
                f"Konu: {next_session.speaker.talk_title}"
            )
        elif next_session.panelists:
            names = ", ".join(p.name for p in next_session.panelists)
            r["next_speaker_info"] = f"Panelistler: {names}"
        else:
            r["next_speaker_info"] = ""
    else:
        r["next_session_title"] = "Kapanis"
        r["next_session_type"] = "closing"
        r["next_speaker_info"] = ""

    return r
