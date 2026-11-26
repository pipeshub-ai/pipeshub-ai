"""DUR1-P2..P4: differences between the durable path and the legacy factory, closed.

DUR1-P1 (sandbox tool order) is held by C-24, whose wave 1 cases compare the tools both loops offer, in order.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry

from . import world as w
from .test_parity import _durable_turn_one, _full_grant

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def test_dur1_p2_domain_agents_get_the_factorys_lazy_decider_and_shared_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.agent_loop import domain_agents
    from app.agents.agent_loop.factory import _DOMAIN_SHARED_NAV_TOOL_NAMES

    seen: dict[str, Any] = {}
    real = domain_agents.register_domain_agents

    def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(domain_agents, "register_domain_agents", spy)
    agent = w.agent_doc()
    _ = await _durable_turn_one(w.Services(agent=agent), w.pinned_spec(**_full_grant(agent)), "react", "find the bug")
    assert callable(seen["lazy_tools"]), "each domain agent decides its own disclosure, as in the factory"
    # No skill manager in this world: the navigation tools only, as the factory shares them.
    assert seen["shared_tool_names"] == _DOMAIN_SHARED_NAV_TOOL_NAMES


async def test_dur1_p2_with_skills_the_read_only_skill_tools_are_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop import domain_agents
    from app.agents.agent_loop.skills_wiring import DOMAIN_SHARED_SKILL_TOOL_NAMES
    from app.harness.assembly import loop

    seen: dict[str, Any] = {}

    def spy(*_args: Any, **kwargs: Any) -> list[str]:  # noqa: ANN401
        seen.update(kwargs)
        return []

    monkeypatch.setattr(domain_agents, "register_domain_agents", spy)
    monkeypatch.setattr(domain_agents, "plan_domain_agents", lambda _registry, *_definitions: domain_agents.DomainAgentPlan(
        definitions=(), registered_names=(), claims={},
    ))
    env = SimpleNamespace(context=SimpleNamespace(essential_toolset_names=[]))
    run = SimpleNamespace(loop_mode="react", model=SimpleNamespace(model_name="m", model="m"))
    _ = loop.build_composition(env, run, ToolRegistry(), None, allowed=set(), skills=True)  # type: ignore[arg-type]
    assert seen["shared_tool_names"] >= DOMAIN_SHARED_SKILL_TOOL_NAMES


async def test_dur1_p3_the_runs_transport_is_capped_and_traced_as_the_factory_builds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.agent_loop.langchain_transport import LangChainTransport
    from app.agents.agent_loop.output_cap_transport import OutputCappedTransport
    from app.agents.agent_loop.usage_transport import UsageReportingTransport
    from app.harness import unpinned
    from app.harness.assembly import loop
    from app.utils.image_policy import resolve_image_policy
    from harness_core.models.transports.opik_tracing import OpikTracingTransport
    from tests.unit.agents.adapter.conftest import FakeChatModel

    async def llm_for_chat(*_args: Any, **_kwargs: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:  # noqa: ANN401
        return FakeChatModel(), {"provider": "anthropic", "isMultimodal": True}, {}

    monkeypatch.setattr("app.api.routes.chatbot.get_llm_for_chat", llm_for_chat)
    monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "langchain")
    monkeypatch.setattr(loop, "opik_tracing", lambda: (True, "eval-project"))
    run = SimpleNamespace(
        model=SimpleNamespace(model_key="k", model_name="claude-x", model="claude-x", provider="anthropic",
                              reasoning_effort=None),
        chat_mode="quick",
        org_id="org-1",
        session_id="conv-1",
    )
    resolved = await unpinned.resolve_model(run, SimpleNamespace(config_service=lambda: None))  # type: ignore[arg-type]
    assert isinstance(resolved.transport, OpikTracingTransport), "the run's own model call is traced, as legacy's"
    capped = resolved.transport._inner  # pyright: ignore[reportPrivateUsage]
    # Legacy's order: traced outside the per-turn output cap (`with_output_cap`), which wraps the transport.
    assert isinstance(capped, OutputCappedTransport)
    inner = capped._inner  # pyright: ignore[reportPrivateUsage]
    assert isinstance(inner, LangChainTransport)
    cap = resolve_image_policy(provider="anthropic", is_multimodal=True).max_images_per_request
    assert cap is not None
    assert inner._max_images_per_request == cap  # pyright: ignore[reportPrivateUsage]
    # Child agents and the intent call share the traced transport; it is not traced twice.
    registry = loop.run_transports(resolved, run)  # type: ignore[arg-type]
    # Legacy's order: the usage report (each call's `llm_call.end`) outside the traced transport.
    reported = registry.resolve("langchain")
    assert isinstance(reported, UsageReportingTransport)
    assert reported._inner is resolved.transport  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("report", [True, False])
async def test_dur1_p4_confidence_on_agent_runs_only(monkeypatch: pytest.MonkeyPatch, report: bool) -> None:
    from app.agents.agent_loop import respond
    from app.agents.agent_loop.confidence import normalize
    from app.harness.presentation import PipesHubPresenter

    seen: dict[str, Any] = {}

    class Finalizer:
        def __init__(self, *_args: Any) -> None:  # noqa: ANN401
            pass

        async def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            seen.update(kwargs)
            return {"answer": "ok", "parts": [], "citations": []}

    monkeypatch.setattr(respond, "AnswerFinalizer", Finalizer)
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None, run_id="run-1")
    presenter = PipesHubPresenter(context, ToolRegistry(), report_confidence=report)
    _ = await presenter.finalize(RunEnding(outcome="answered", texts=("ok",), confidence="high"))
    assert normalize("high") is not None
    assert seen["agent_confidence"] == (normalize("high") if report else None)
