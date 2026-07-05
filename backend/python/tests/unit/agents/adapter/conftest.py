"""Shared fixtures for the agent-loop adapter-layer test suite (Phase 9).

`make_context()` builds a minimal, valid `AgentContext` directly (not via
`build_initial_state()` + `AgentContext.from_chat_state()`, which is what
Phase 8's route integration uses) — adapter-layer tests care about
tool/hook/prompt behavior in isolation, not about reproducing every field
`chat_stream` derives from a request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.agents.agent_loop.context import AgentContext


class FakeChatModel:
    """Minimal LangChain `BaseChatModel` double: no network, deterministic
    replies, configurable per-test via `.responses`."""

    def __init__(self, responses: list[AIMessage] | None = None) -> None:
        self.responses = list(responses or [AIMessage(content="ok")])
        self._index = 0
        self.bound_tools: list[Any] | None = None
        self.ainvoke_calls: list[list] = []

    def bind_tools(self, tools: list[Any]) -> "FakeChatModel":
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list, config: dict | None = None) -> AIMessage:
        self.ainvoke_calls.append(messages)
        if self._index >= len(self.responses):
            return self.responses[-1]
        response = self.responses[self._index]
        self._index += 1
        return response

    def with_structured_output(self, schema: Any, include_raw: bool = False) -> "FakeChatModel":
        return self


@pytest.fixture
def fake_chat_model() -> FakeChatModel:
    return FakeChatModel()


def make_context(**overrides: Any) -> AgentContext:
    defaults: dict[str, Any] = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "user@example.com",
        "user_info": {"userId": "user-1", "orgId": "org-1"},
        "org_info": {"name": "TestOrg"},
        "logger": MagicMock(),
        "llm": FakeChatModel(),
        "retrieval_service": MagicMock(),
        "graph_provider": MagicMock(),
        "config_service": MagicMock(),
        "blob_store": MagicMock(),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


@pytest.fixture
def agent_context() -> AgentContext:
    return make_context()
