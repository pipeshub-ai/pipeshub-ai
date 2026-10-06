"""AB-14: `agent_builder` loads only for the assistant with the flag on."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, _patch, patch

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.domain_agents import plan_domain_agents
from app.agents.agent_loop.factory import agent_builder_skip_apps
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.registry.toolset_registry import get_toolset_registry

TOOL = "agent_builder__draft_agent"


def _context(invocation: str | None = None) -> AgentContext:
    kwargs = {} if invocation is None else {"invocation": invocation}
    return AgentContext(
        org_id="org1", user_id="u1", user_email="u@x", config_service=MagicMock(), logger=MagicMock(), **kwargs,
    )


@pytest.fixture(autouse=True)
def _discover_toolsets() -> None:
    get_toolset_registry().auto_discover_toolsets()


def _flag(value: bool) -> _patch:
    return patch("app.agents.agent_loop.factory.is_chat_agent_builder_enabled", AsyncMock(return_value=value))


def test_invocation_defaults_to_saved_agent() -> None:
    assert _context().invocation == "saved_agent"
    assert _context().tool_state["invocation"] == "saved_agent"


@pytest.mark.parametrize("invocation", ["saved_agent", "sub_agent"])
async def test_skipped_for_saved_agents_and_sub_agents_even_with_the_flag_on(invocation: str) -> None:
    with _flag(True):
        assert "agent_builder" in await agent_builder_skip_apps(_context(invocation))


async def test_skipped_for_the_assistant_when_the_flag_is_off() -> None:
    with _flag(False):
        assert "agent_builder" in await agent_builder_skip_apps(_context("assistant"))


async def test_loaded_for_the_assistant_when_the_flag_is_on() -> None:
    with _flag(True):
        assert await agent_builder_skip_apps(_context("assistant")) == set()


async def _loaded_tools(skip: set[str], invocation: str) -> list[str]:
    registry = await PipesHubToolLoader().load(_context(invocation), skip_apps=skip)
    return registry.names()


async def test_the_loader_registers_draft_agent_unless_skipped() -> None:
    assert TOOL in await _loaded_tools(set(), "assistant")
    with _flag(False):
        skip = await agent_builder_skip_apps(_context("assistant"))
    assert TOOL not in await _loaded_tools(skip, "assistant")


async def test_no_domain_sub_agent_claims_the_draft_tool() -> None:
    registry = await PipesHubToolLoader().load(_context("assistant"), skip_apps=set())
    plan = plan_domain_agents(registry)
    assert TOOL not in [n for names in plan.claims.values() for n in names]
    assert TOOL in plan.top_level_names
