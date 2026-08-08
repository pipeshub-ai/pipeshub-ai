"""Unit tests for `AgentContext.enable_record_id_shortening` wiring.

TEMPORARY token-savings experiment — see `RecordIdShortener` in
`app.utils.chat_helpers`. Opt-in via `ChatQuery.enableRecordIdShortening`,
disabled by default: these pin the default-off behavior end to end through
`AgentContext` construction, `from_chat_state()`, and `_seed_tool_state()`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.agent_loop.context import AgentContext


def _minimal_context(**overrides) -> AgentContext:
    defaults = {
        "org_id": "org-1", "user_id": "user-1", "user_email": "user@example.com",
        "logger": MagicMock(),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class TestEnableRecordIdShorteningDefault:
    def test_direct_construction_defaults_to_false(self):
        context = _minimal_context()
        assert context.enable_record_id_shortening is False

    def test_seed_tool_state_mirrors_the_field(self):
        context = _minimal_context(enable_record_id_shortening=True)
        assert context.tool_state["enable_record_id_shortening"] is True

    def test_seed_tool_state_mirrors_false_by_default(self):
        context = _minimal_context()
        assert context.tool_state["enable_record_id_shortening"] is False


class TestFromChatState:
    def test_flag_absent_in_chat_state_defaults_to_false(self):
        context = AgentContext.from_chat_state({})
        assert context.enable_record_id_shortening is False
        assert context.tool_state["enable_record_id_shortening"] is False

    def test_flag_true_in_chat_state_is_threaded_through(self):
        state = {"enable_record_id_shortening": True}
        context = AgentContext.from_chat_state(state)
        assert context.enable_record_id_shortening is True
        assert context.tool_state["enable_record_id_shortening"] is True

    def test_flag_false_in_chat_state_stays_false(self):
        state = {"enable_record_id_shortening": False}
        context = AgentContext.from_chat_state(state)
        assert context.enable_record_id_shortening is False
