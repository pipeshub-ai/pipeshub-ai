"""`AgentContext.include_retrieval_context` wiring: default off, threaded
through `from_chat_state()` and mirrored into `tool_state`."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.agent_loop.context import AgentContext


def _minimal_context(**overrides: Any) -> AgentContext:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "org_id": "org-1", "user_id": "user-1", "user_email": "user@example.com",
        "logger": MagicMock(),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class TestIncludeRetrievalContextFlag:
    def test_defaults_to_false(self) -> None:
        context = _minimal_context()
        assert context.include_retrieval_context is False
        assert context.tool_state["include_retrieval_context"] is False

    def test_direct_construction_mirrors_into_tool_state(self) -> None:
        context = _minimal_context(include_retrieval_context=True)
        assert context.tool_state["include_retrieval_context"] is True

    @pytest.mark.parametrize("value", [True, False])
    def test_from_chat_state_threads_the_flag(self, value: bool) -> None:
        context = AgentContext.from_chat_state({"include_retrieval_context": value})
        assert context.include_retrieval_context is value
        assert context.tool_state["include_retrieval_context"] is value

    def test_from_chat_state_absent_flag_is_false(self) -> None:
        context = AgentContext.from_chat_state({})
        assert context.include_retrieval_context is False

    def test_ledger_is_created_once_per_context(self) -> None:
        context = _minimal_context()
        assert context.retrieval_ledger is context.retrieval_ledger
        assert _minimal_context().retrieval_ledger is not context.retrieval_ledger
