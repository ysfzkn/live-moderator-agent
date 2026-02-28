"""OpenAI Realtime API function calling tool definitions and handlers."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from server.conference.agenda_manager import AgendaManager
    from server.conference.state_machine import ConferenceStateMachine

logger = logging.getLogger(__name__)

# Tool definitions sent to OpenAI via session.update
CONFERENCE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "advance_to_next_session",
        "description": "Bir sonraki oturuma gecis yapar. Mevcut konusmaci tamamlandiginda, sure doldiginda veya mola bittiginde cagrilir.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "speaker_finished",
                        "time_expired",
                        "break_over",
                        "operator_skip",
                    ],
                    "description": "Gecis nedeni",
                }
            },
            "required": ["reason"],
        },
    },
    {
        "type": "function",
        "name": "check_time_remaining",
        "description": "Mevcut oturum icin kalan sureyi kontrol eder.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_session_info",
        "description": "Mevcut veya sonraki oturum ve konusmaci bilgilerini getirir.",
        "parameters": {
            "type": "object",
            "properties": {
                "which": {
                    "type": "string",
                    "enum": ["current", "next"],
                    "description": "Hangi oturum bilgisi isteniyor",
                }
            },
            "required": ["which"],
        },
    },
    {
        "type": "function",
        "name": "announce_time_warning",
        "description": "Konusmaciya sure uyarisi verir. Kalan sure az oldugunda cagrilir.",
        "parameters": {
            "type": "object",
            "properties": {
                "minutes_remaining": {
                    "type": "number",
                    "description": "Kalan dakika sayisi",
                }
            },
            "required": ["minutes_remaining"],
        },
    },
]


class ToolHandler:
    """Handles function calls from the OpenAI Realtime API."""

    def __init__(
        self,
        state_machine: ConferenceStateMachine,
        agenda_manager: AgendaManager,
    ) -> None:
        self._sm = state_machine
        self._am = agenda_manager

    async def handle_function_call(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a function call and return the result."""
        logger.info("Tool call: %s(%s)", name, arguments)

        if name == "advance_to_next_session":
            return await self._advance_to_next_session(arguments)
        elif name == "check_time_remaining":
            return self._check_time_remaining()
        elif name == "get_session_info":
            return self._get_session_info(arguments)
        elif name == "announce_time_warning":
            return await self._announce_time_warning(arguments)
        else:
            return {"error": f"Bilinmeyen fonksiyon: {name}"}

    async def _advance_to_next_session(self, args: dict) -> dict:
        reason = args.get("reason", "speaker_finished")
        await self._sm.handle_advance_session(reason)
        next_info = self._am.get_session_info(self._sm.context, "current")
        return {
            "status": "ok",
            "message": "Sonraki oturuma gecildi",
            "new_session": next_info,
        }

    def _check_time_remaining(self) -> dict:
        return self._am.get_time_remaining(self._sm.context)

    def _get_session_info(self, args: dict) -> dict:
        which = args.get("which", "current")
        return self._am.get_session_info(self._sm.context, which)

    async def _announce_time_warning(self, args: dict) -> dict:
        minutes = args.get("minutes_remaining", 5)
        ctx = self._sm.context
        session = ctx.current_session
        speaker_name = ""
        if session and session.speaker:
            speaker_name = session.speaker.name
        return {
            "status": "ok",
            "speaker_name": speaker_name,
            "minutes_remaining": minutes,
            "message": f"{speaker_name} icin {minutes} dakika kaldi",
        }
