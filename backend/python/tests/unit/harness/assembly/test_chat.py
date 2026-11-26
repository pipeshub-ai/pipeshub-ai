"""PC-2: the built-in assistant runs durably with the tools legacy `/chat/stream` gives it.

The oracle is legacy itself: the tool set `PipesHubAgentFactory` builds for a
`/chat/stream` turn (`chat_modes.setup.prepare_chat_state` in the same world).
The pin decides what the durable run may use: the mode's knowledge search and
the web tool grant; the client cannot widen either.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.chat_modes.policy import resolve_chat_mode_policy
from app.agents.chat_modes.setup import prepare_chat_state
from app.harness.assembly.context import RunContext
from tests.unit.agents.adapter.conftest import FakeChatModel

from . import world as w
from .test_parity import SANDBOX, _durable_turn_one

if TYPE_CHECKING:
    from collections.abc import Iterator

WEB = "web@default/web_search"
WEB_TOOLS = {"dynamic__web_search", "dynamic__fetch_url"}
KNOWLEDGE_TOOLS = {"knowledgegraph__search"}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _services() -> w.Services:
    services = w.Services(agent={})
    services.retrieval.search_with_filters = AsyncMock(
        return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    )
    return services


def _spec(mode: str, *, web: bool | None = None, internal: bool | None = None) -> Any:  # noqa: ANN401
    return w.pinned_spec(
        tool_ids=[WEB] if (mode == "web_search" if web is None else web) else [],
        kb=[],
        agent=False,
        chat={"mode": mode, "internalSearch": mode == "internal_search" if internal is None else internal},
        strict_scope=False,
    )


async def _legacy_tools(services: w.Services, mode: str) -> set[str]:
    """What legacy `/chat/stream` offers in `mode` (the factory's grant)."""
    policy = resolve_chat_mode_policy(mode)
    setup = await prepare_chat_state(
        {"query": "find the bug", "chatMode": mode, "filters": {}, "attachments": [], "previous_conversations": []},
        {"userId": w.ACTOR, "orgId": w.ORG, "userEmail": "user@corp.example", "sendUserInfo": True},
        FakeChatModel(),
        policy,
        logging.getLogger("assembly.tests"),
        retrieval_service=services.retrieval,
        graph_provider=services.graph,
        reranker_service=None,
        config_service=services.config,
        org_info=None,
        model_name="gpt-x",
        model_key="model-1",
        is_multimodal_llm=False,
        system_prompts_config={},
        client_name="pipeshub-ai",  # the chat UI (Node's WEB_CLIENT_NAME): durable runs answer ask_user_question as it does
        org_id=w.ORG,  # what the chat route binds from its token
    )
    context = AgentContext.from_chat_state(setup.chat_state, protocol="agui", llm_provider="openai")
    agent, _runtime, _goal, _questions = await PipesHubAgentFactory().create(
        context, FakeChatModel(), policy.loop_chat_mode, query="find the bug", model_name="gpt-x", model_key="model-1"
    )
    return set(agent.spec.tool_names)


async def _durable_tools(services: w.Services, spec: Any, mode: str) -> set[str]:  # noqa: ANN401
    _runtime, _slots, shaped = await _durable_turn_one(services, spec, mode, "find the bug")
    return {schema.name for schema in shaped.tools or []}


@pytest.mark.parametrize("mode", ["internal_search", "web_search"])
async def test_pc2_the_assistant_gets_the_tools_legacy_chat_gives_it(mode: str) -> None:
    durable = await _durable_tools(_services(), _spec(mode), mode)
    legacy = await _legacy_tools(_services(), mode)
    assert durable == legacy - SANDBOX
    if mode == "internal_search":
        assert KNOWLEDGE_TOOLS <= durable and not WEB_TOOLS & durable
    else:
        assert WEB_TOOLS <= durable and not KNOWLEDGE_TOOLS & durable


async def test_pc2_web_search_not_set_up_answers_without_it_and_says_so_as_legacy_does() -> None:
    """Main #5722: no default provider is web search off. The pinned web grant is dropped, not ``CapabilityLost``,
    and the model is told web search is not set up, as legacy `/chat/stream` tells it."""
    services = _services()
    services.config.put("/services/webSearch", {"providers": []})
    _runtime, _slots, shaped = await _durable_turn_one(services, _spec("web_search"), "web_search", "find the bug")
    durable = {schema.name for schema in shaped.tools or []}
    legacy = await _legacy_tools(services, "web_search")
    assert durable == legacy - SANDBOX
    assert not WEB_TOOLS & durable
    assert "not set up" in str(shaped.system).lower()


async def test_pc2_a_pin_without_the_web_grant_never_offers_web_search() -> None:
    """The pin is the ceiling: web search only when `grants.tools` names it."""
    durable = await _durable_tools(_services(), _spec("web_search", web=False), "web_search")
    assert not WEB_TOOLS & durable


async def test_pc2_a_pin_without_knowledge_never_searches_it() -> None:
    services = _services()
    durable = await _durable_tools(services, _spec("internal_search", internal=False), "internal_search")
    assert not KNOWLEDGE_TOOLS & durable
    services.retrieval.search_with_filters.assert_not_awaited()


async def test_pc2_the_run_is_built_from_the_pin_and_runs_the_quick_loop() -> None:
    claim = w.claim(_spec("internal_search"), chat_mode="internal_search")
    run = RunContext.from_claim(claim)
    assert run.chat is not None and run.agent is None
    assert (run.chat.mode, run.chat.internal_search, run.chat.web_search) == ("internal_search", True, False)
    assert run.loop_mode == "quick" and run.chat_mode == "internal_search"
    policy = run.chat.policy()
    assert (policy.has_knowledge, policy.include_web_search, policy.prefetch_retrieval) == (True, False, True)
    # The same pin names the same run, attempt after attempt.
    assert RunContext.from_claim(claim).spec_digest == run.spec_digest


async def test_pc2_the_prefetch_runs_in_the_goal_step_once_and_journals_its_sources() -> None:
    services = _services()
    _runtime, slots, _shaped = await _durable_turn_one(services, _spec("internal_search"), "internal_search", "q")
    services.retrieval.search_with_filters.assert_awaited_once()
    assert "pre_run.goal" in slots


async def test_pc2_the_chat_decisions_are_logged_with_the_run_ids_and_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ground rule 9: which chat grant the run was built with, and what its
    prefetch found, are logged by run and carry no query or context text."""
    caplog.set_level(logging.DEBUG)
    sentinel = "SENTINEL-CHAT-4417"
    services = _services()
    _ = await _durable_turn_one(services, _spec("internal_search"), "internal_search", f"find {sentinel} please")
    events = {getattr(r, "harness_event", None): r for r in caplog.records}
    environment = events["run.chat_environment"].harness  # type: ignore[attr-defined]
    assert (environment["runId"], environment["orgId"], environment["mode"]) == ("run-1", w.ORG, "internal_search")
    assert (environment["knowledge"], environment["webSearch"], environment["prefetch"]) == (True, False, True)
    chat = events["prerun.chat"].harness  # type: ignore[attr-defined]
    assert (chat["runId"], chat["attemptId"], chat["prefetch"]) == ("run-1", "att-1", True)
    harness_lines = [r for r in caplog.records if getattr(r, "harness_event", None)]
    assert all(sentinel not in r.getMessage() and sentinel not in str(getattr(r, "harness", "")) for r in harness_lines)
