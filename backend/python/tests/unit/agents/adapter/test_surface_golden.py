"""Golden snapshots of the system prompt and tool grant for every agent-loop
configuration that is NOT the enterprise-search chat mode.

The focused `internal_search` surface is composed conditionally from the
resolved chat-mode policy; these snapshots pin that the condition never
leaks into any other mode. A diff here means a prompt or tool grant changed
for `web_search`/`agent` chat, or for a custom agent in quick/react/
planExecute/deep — review it as a product change, then regenerate with:

    python -m tests.unit.agents.adapter.test_surface_golden
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.chat_modes.bridge import (
    _apply_policy_to_chat_state,
    _with_mode_instructions,
)
from app.agents.chat_modes.policy import (
    AGENT_POLICY,
    INTERNAL_SEARCH_POLICY,
    WEB_SEARCH_POLICY,
    AgentCapabilities,
    ChatModePolicy,
    resolve_agent_policy,
)
from app.agents.registry.toolset_registry import ToolsetRegistry
from tests.unit.agents.adapter.conftest import FakeChatModel

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "agent_surface_golden.json"

# Same order `ToolsetRegistry.auto_discover_toolsets` imports them in, so the
# loader walks them in production order.
_INTERNAL_TOOLSET_MODULES = (
    ("app.agents.actions.retrieval.retrieval", "Retrieval"),
    ("app.agents.actions.calculator.calculator", "Calculator"),
    ("app.agents.actions.calculator.date_calculator", "DateCalculator"),
    ("app.agents.actions.knowledge_hub.knowledge_hub", "KnowledgeHub"),
    ("app.agents.actions.knowledge_graph.knowledge_graph", "KnowledgeGraph"),
    ("app.agents.actions.coding_sandbox.coding_sandbox", "CodingSandbox"),
    ("app.agents.actions.database_sandbox.database_sandbox", "DatabaseSandbox"),
    ("app.agents.actions.image_generator.image_generator", "ImageGenerator"),
    ("app.agents.actions.artifacts.artifacts", "ArtifactManager"),
    ("app.agents.actions.internal_tools.intrim_tools", "InternalTools"),
)


class FakeTicketsToolset:
    """A connector with no client factory, so the loader grants it in every
    mode — one read tool and one write tool."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    @tool(
        path="/tools/tickets/search_tickets",
        short_description="Search support tickets",
        description="Search the support ticket system.",
    )
    async def search_tickets(self, query: str) -> tuple[bool, str]:
        return True, "[]"

    @tool(
        path="/tools/tickets/create_ticket",
        short_description="Create a support ticket",
        description="Create a new support ticket.",
    )
    async def create_ticket(self, title: str) -> tuple[bool, str]:
        return True, "{}"


def build_toolset_registry() -> MagicMock:
    import importlib

    private = object.__new__(ToolsetRegistry)
    private._toolsets = {}
    for module_path, class_name in _INTERNAL_TOOLSET_MODULES:
        private.register_toolset(getattr(importlib.import_module(module_path), class_name))
    private._toolsets["tickets"] = {
        "class": FakeTicketsToolset,
        "isInternal": False,
        "essential": False,
        "description": "Support ticket system",
    }
    fake = MagicMock()
    fake.get_all_toolsets.return_value = dict(private._toolsets)
    return fake


class _FakeSkillMetadata:
    name = "pptx"
    description = "Use this skill whenever the user asks for a PowerPoint presentation."
    category = "office"


class _FakeSkillManager:
    class config:
        catalog_render_limit = 40

    def catalog_snapshot(self) -> list[_FakeSkillMetadata]:
        return [_FakeSkillMetadata()]


async def _fake_build_skill_manager(_context: Any, _transport_registry: Any) -> _FakeSkillManager:
    return _FakeSkillManager()


_KNOWLEDGE = [{
    "displayName": "Handbook", "name": "Handbook", "_id": "kb-1", "connectorId": "kb-1", "type": "KB",
}]
_WEB_CONFIG = {"provider": "duckduckgo", "configuration": {}}
_CUSTOM_AGENT_PROMPT = "You are the Acme support agent. Help staff resolve customer tickets."


def _base_state() -> dict[str, Any]:
    return {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "user@example.com",
        "user_info": {"userId": "user-1", "orgId": "org-1", "fullName": "Test User"},
        "org_info": {"name": "TestOrg"},
        "logger": MagicMock(),
        "llm": FakeChatModel(),
        "retrieval_service": MagicMock(),
        "graph_provider": MagicMock(),
        "config_service": MagicMock(),
        "blob_store": MagicMock(),
        "current_time": "2026-03-04T10:00:00Z",
        "timezone": "UTC",
        "conversation_id": "conv-1",
        "entity_vector_store": MagicMock(),
    }


def chat_state_for_policy(policy: ChatModePolicy) -> dict[str, Any]:
    """The state `bridge.run_chat_stream` hands the factory for `policy`."""
    state = _base_state()
    state["agent_knowledge"] = list(_KNOWLEDGE)
    _apply_policy_to_chat_state(state, policy, dict(_WEB_CONFIG))
    state["instructions"] = _with_mode_instructions(None, policy)
    return state


def custom_agent_state() -> dict[str, Any]:
    state = _base_state()
    state.update({
        "has_knowledge": True,
        "agent_knowledge": list(_KNOWLEDGE),
        "web_search_config": dict(_WEB_CONFIG),
        "system_prompt": _CUSTOM_AGENT_PROMPT,
        "instructions": "Always include the ticket number.",
    })
    return state


# name -> (state builder, loop chat_mode passed to the factory)
GOLDEN_CASES: dict[str, tuple[Any, str]] = {
    "chat_web_search": (lambda: chat_state_for_policy(WEB_SEARCH_POLICY), "quick"),
    "chat_agent": (lambda: chat_state_for_policy(AGENT_POLICY), "quick"),
    "chat_agent_internal_only": (
        lambda: chat_state_for_policy(resolve_agent_policy(AgentCapabilities(web_search=False))),
        "quick",
    ),
    "custom_agent_quick": (custom_agent_state, "quick"),
    "custom_agent_react": (custom_agent_state, "react"),
    "custom_agent_plan_execute": (custom_agent_state, "planExecute"),
    "custom_agent_deep": (custom_agent_state, "deep"),
}

# Not pinned (its surface is intentionally different); kept buildable so
# the focused-surface tests and size measurement share this harness.
INTERNAL_SEARCH_CASE = (lambda: chat_state_for_policy(INTERNAL_SEARCH_POLICY), "quick")

_UNSET_ENV = (
    "PIPESHUB_ENABLE_CODE_EXECUTION", "SANDBOX_ALLOW_NETWORK", "PIPESHUB_ENABLE_LAZY_TOOLS",
    "PIPESHUB_LAZY_TOOLS_THRESHOLD", "PIPESHUB_LAZY_TOOLS_SCOPE", "PIPESHUB_ENABLE_CONFIDENCE",
    "PIPESHUB_ENABLE_FINAL_ANSWER", "PIPESHUB_USE_COMPOSED_AGENTS",
    "PIPESHUB_AGENT_DISABLED_TOOLSETS", "PIPESHUB_AGENT_TRANSPORT",
)


def pinned_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _UNSET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SANDBOX_MODE", "local")
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
    monkeypatch.setattr("app.agents.agent_loop.factory.is_skills_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("app.agents.agent_loop.factory.build_skill_manager", _fake_build_skill_manager)
    monkeypatch.setattr(
        "app.agents.registry.toolset_registry.get_toolset_registry", build_toolset_registry,
    )
    monkeypatch.setattr(
        "app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", lambda _name: None,
    )


async def build_case(state_builder: Any, chat_mode: str) -> dict[str, Any]:
    """Runs the real factory and renders the top-level prompt it would send."""
    context = AgentContext.from_chat_state(state_builder())
    agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
        context, context.llm, chat_mode, query="What is the parental leave policy?",
    )
    spec = agent.spec
    prompt = spec.system_prompt.build(
        spec, runtime, Goal(description="What is the parental leave policy?"), [], {},
    )
    return {
        "tool_names": list(spec.tool_names),
        "tool_disclosure": spec.tool_disclosure,
        "pinned_toolsets": list(spec.pinned_toolsets),
        "registry_toolsets": sorted(g.name for g in runtime.tool_registry.toolsets()),
        "prompt": prompt,
    }


@pytest.fixture
def golden_env(monkeypatch: pytest.MonkeyPatch) -> None:
    pinned_environment(monkeypatch)


@pytest.mark.parametrize("case_name", sorted(GOLDEN_CASES))
async def test_non_enterprise_search_surface_is_unchanged(golden_env: None, case_name: str) -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text())[case_name]
    actual = await build_case(*GOLDEN_CASES[case_name])

    assert actual["tool_names"] == expected["tool_names"]
    assert actual["tool_disclosure"] == expected["tool_disclosure"]
    assert actual["pinned_toolsets"] == expected["pinned_toolsets"]
    assert actual["registry_toolsets"] == expected["registry_toolsets"]
    assert actual["prompt"] == expected["prompt"]


def _regenerate() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        pinned_environment(monkeypatch)
        snapshots = {
            name: asyncio.run(build_case(*GOLDEN_CASES[name])) for name in sorted(GOLDEN_CASES)
        }
    finally:
        monkeypatch.undo()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshots, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    with patch("logging.Logger.info"):
        _regenerate()
