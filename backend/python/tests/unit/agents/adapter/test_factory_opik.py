"""`PipesHubAgentFactory.create()` Opik wiring (`app/agents/agent_loop/
factory.py`) — Phase 1c of the Opik tracing plan.

Complements `test_factory_wiring.py` (which covers the non-Opik shape of
`create()`'s output) by pinning: `is_opik_configured()` gates whether the
`"langchain"` transport gets wrapped in `OpikTracingTransport`, and whether
`runtime.opik_enabled`/`opik_project_name` get set — both derived from the
SAME env-var check, so they can never disagree."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.transport.opik_tracing import OpikTracingTransport
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.langchain_transport import LangChainTransport
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


@contextlib.contextmanager
def _fake_span_cm(**_kwargs: Any):
    yield SimpleNamespace()


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opik-wiring tests assert transport wrapping only. The skills
    subsystem (on by default) would await real graph calls on
    `make_context`'s MagicMock graph_provider and blow up — pin it off."""
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


@pytest.fixture(autouse=True)
def _no_dynamic_tools() -> None:
    """`PipesHubToolLoader.load()`'s dynamic tools (SQL/Slack-context/
    web-search — see `tool_loader._build_dynamic_tools()`) are each gated
    on a `context.tool_state` flag `make_context()` never sets, so they're
    already absent with no patch needed — see `test_factory_wiring.py`'s
    identical fixture for the full rationale."""


@pytest.fixture(autouse=True)
def _clean_opik_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this module controls Opik env vars explicitly —
    scrub ambient `OPIK_*` vars first so a developer's real Opik Cloud
    credentials (if configured locally) can never leak into these tests
    and flip a "no env set" test into a false positive."""
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)
    monkeypatch.delenv("OPIK_PROJECT_NAME", raising=False)


@pytest.fixture(autouse=True)
def _no_real_opik_network_calls():
    """`create()` calls `router.select_loop_and_goal()` -> `intent.
    parse_intent_and_route()` for EVERY chat mode (even "react" — see
    `router.py`'s docstring), which opens its own `maybe_start_named_span`
    once `is_opik_configured()` is true. Tests below intentionally set a
    real-looking `OPIK_API_KEY`/`OPIK_URL_OVERRIDE` to exercise that gate,
    which would otherwise make a genuine (and, in a sandboxed test run,
    failing) network call to Opik — stub the two real entry points
    process-wide for this module so no test here ever reaches the network."""
    with (
        patch("opik.start_as_current_span", side_effect=lambda **kw: _fake_span_cm(**kw)),
        patch("opik.start_as_current_trace", side_effect=lambda **kw: _fake_span_cm(**kw)),
    ):
        yield


class TestFactoryOpikWiring:
    async def test_factory_wraps_transport_when_opik_configured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "react", query="hello",
        )

        transport = runtime.transport_registry.resolve("langchain")
        assert isinstance(transport, OpikTracingTransport)

    async def test_factory_sets_opik_enabled_on_runtime(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        monkeypatch.setenv("OPIK_PROJECT_NAME", "my-project")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "react", query="hello",
        )

        assert runtime.opik_enabled is True
        assert runtime.opik_project_name == "my-project"

    async def test_factory_no_opik_when_env_missing(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "react", query="hello",
        )

        transport = runtime.transport_registry.resolve("langchain")
        assert type(transport) is LangChainTransport
        assert runtime.opik_enabled is False
        assert runtime.opik_project_name is None

    async def test_factory_recognizes_self_hosted_url_override_without_api_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://localhost:5173")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "react", query="hello",
        )

        assert runtime.opik_enabled is True
        transport = runtime.transport_registry.resolve("langchain")
        assert isinstance(transport, OpikTracingTransport)

    async def test_project_name_none_when_opik_configured_but_no_project_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "react", query="hello",
        )

        assert runtime.opik_enabled is True
        assert runtime.opik_project_name is None

    async def test_plan_execute_mode_shared_transport_wrapped_when_opik_configured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`planExecute` now plans via the `create_plan`/`critique_plan`/
        `verify_result` TOOLS on the top-level agent's OWN model (see
        `PlanCritiqueExecuteLoop` in `loops/plan_execute.py`) rather than a
        construction-time `PlanAheadPlanner` with its own second transport
        — so there is only the ONE `"langchain"` registry entry to wrap,
        same gate `test_factory_wraps_transport_when_opik_configured`
        above already pins for every other mode."""
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "planExecute", query="hello",
        )

        transport = runtime.transport_registry.resolve("langchain")
        assert isinstance(transport, OpikTracingTransport)

    async def test_plan_execute_mode_shared_transport_unwrapped_when_opik_not_configured(
        self,
    ) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "planExecute", query="hello",
        )

        transport = runtime.transport_registry.resolve("langchain")
        assert type(transport) is LangChainTransport
