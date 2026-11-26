"""FG2 LF, TR, XS-8: today's loop applies an org's and an agent's own flow rule with the deployment's switch off
(QA10-F1), counts every result as read (QA10-F2), and refuses a read that would carry data to an outside host after a
read; the product's own web search and page fetch always run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast, override

import pytest

from app.agents.agent_loop.hooks.flow_guard import (
    LEGACY_ADDRESS_REFUSAL,
    LEGACY_FLOW_REFUSAL,
    LEGACY_SEND_REFUSAL,
    LEGACY_WEB_REFUSAL,
    register_legacy_flow_guard,
    request_label,
)
from harness_core.kernel.hooks.events import HookEvent
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.tools.base import Tag
from harness_core.tools.registry import ToolRegistry
from tests.support.config_service import FakeConfigurationService
from tests.support.web_tools import product_web_tools

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.kernel.hooks.middleware.pipeline import Pipeline

APPROVALS = "/services/agentHarness/approvals"
ORG, AGENT = "org-1", "agent-1"
SEND = ("/toolsets/slack/send_message", (Tag("effect", "write"),))
COMMENT = ("/toolsets/jira/add_comment", (Tag("effect", "write"),))
MCP = ("/mcp/acme/post_note", ())
READ = ("/toolsets/jira/get_issue", (Tag("effect", "read"),))
DRAFT = ("/toolsets/gmail/draft_email", (Tag("effect", "write"), Tag("sink", "self")))
PLAN = ("/toolsets/builtin/create_plan", (Tag("planning", "create_plan"),))
# A read whose request goes to an outside host that is not the product's own web tool (an open-world MCP read).
OUTSIDE_READ = ("/mcp/acme/lookup", (Tag("effect", "read"), Tag("sink", "external")))

_WEB = product_web_tools()
# The product's dynamic web tools as a request registers them: their paths, tags and the names the registry resolves.
FETCH = (_WEB["dynamic__fetch_url"].path, tuple(_WEB["dynamic__fetch_url"].tags))
WEB_SEARCH = (_WEB["dynamic__web_search"].path, tuple(_WEB["dynamic__web_search"].tags))
HISTORY = [{"role": "user_query", "content": "Q3 plan?"}, {"role": "bot_response", "content": "…"}]
# The request's message names one page; a fetch of it runs after a private read, a fetch of any other is refused.
GIVEN_PAGE = "https://docs.example.com/guide"
GIVEN = {"url": GIVEN_PAGE}
MADE_UP = {"url": "https://made.example.net/?q=Q3-plan"}


class _Unreadable(FakeConfigurationService):
    @override
    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False) -> Any:
        if key == APPROVALS:
            raise ConnectionError("config store unavailable")
        return await super().get_config(key, default, use_cache, raise_on_error=raise_on_error)


def _context(
    document: JsonValue = None,
    *,
    agent_key: str | None = AGENT,
    history: list[dict[str, str]] | None = None,
    config: FakeConfigurationService | None = None,
) -> SimpleNamespace:
    if config is None:
        config = FakeConfigurationService()
        if document is not None:
            config.put(APPROVALS, document)
    return SimpleNamespace(
        org_id=ORG,
        agent_key=agent_key,
        config_service=config,
        previous_conversations=history or [],
        tool_state={},
        query=f"Compare {GIVEN_PAGE} with our Q3 plan",
    )


class Loop:
    """Today's loop's PRE_TOOL_USE hooks for one request, as the legacy factory registers the guard."""

    def __init__(self, context: SimpleNamespace) -> None:
        self.context = context
        self.hooks = HookRegistry()
        self.registered = False
        registry = ToolRegistry()
        for tool in _WEB.values():
            registry.register_tool(tool)
        self.scope = SimpleNamespace(turn=SimpleNamespace(run=SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry))))

    async def build(self) -> Loop:
        self.registered = await register_legacy_flow_guard(self.hooks, self.context)
        return self

    async def call(self, tool: tuple[str, tuple[Tag, ...]], args: dict[str, Any] | None = None) -> ToolCallContext:
        # The hook library's Pipeline is not generic over its context.
        pre = cast("Pipeline[ToolCallContext]", self.hooks.on(HookEvent.PRE_TOOL_USE))  # pyright: ignore[reportUnknownMemberType]
        # The web tools resolve through a request's registry; the others are named by their path, as without one.
        scope = self.scope if tool[0].startswith("/dynamic/") or tool[0].startswith("/unresolved/") else None
        ctx = ToolCallContext(tool_path=tool[0], tool_input=dict(args or GIVEN), tags=tool[1], scope=scope)  # pyright: ignore[reportArgumentType]
        return await pre.dispatch(ctx)


@pytest.fixture
def switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")
    for name in ("HARNESS_FLOW_GUARD_LEVEL", "HARNESS_FLOW_GUARD_ABOVE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def switch_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HARNESS_FLOW_GUARD", "HARNESS_FLOW_GUARD_LEVEL", "HARNESS_FLOW_GUARD_ABOVE"):
        monkeypatch.delenv(name, raising=False)


def _refused(ctx: ToolCallContext, reason: str = LEGACY_FLOW_REFUSAL) -> bool:
    return ctx.decision == PreDecision.DENY and ctx.decision_reason == reason


@pytest.mark.usefixtures("switch_off")
@pytest.mark.parametrize(
    "document",
    [
        {"agents": {AGENT: {"flow": {"level": "ask"}}}},
        {"default": {"flow": {"level": "deny"}}},
        {"default": {"flow": {"level": "deny", "above": "private"}}},
    ],
    ids=["agent", "default", "default-above-private"],
)
async def test_lf2_a_document_rule_applies_on_its_agents_chat_with_the_switch_off(document: JsonValue) -> None:
    loop = await Loop(_context(document, history=HISTORY)).build()
    assert loop.registered
    assert _refused(await loop.call(SEND))


@pytest.mark.usefixtures("switch_off")
@pytest.mark.parametrize("agent_key", ["agent-2", None], ids=["another-agent", "plain-chat"])
async def test_lf2_an_agents_rule_does_not_reach_another_agent_or_plain_chat(agent_key: str | None) -> None:
    loop = await Loop(_context({"agents": {AGENT: {"flow": {"level": "ask"}}}}, agent_key=agent_key, history=HISTORY)).build()
    assert not loop.registered
    assert (await loop.call(SEND)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_off")
@pytest.mark.parametrize("document", [None, {}, {"default": {"flow": {"level": "off"}}}], ids=["absent", "empty", "off"])
async def test_lf3_no_rule_registers_nothing(document: JsonValue) -> None:
    loop = await Loop(_context(document, history=HISTORY)).build()
    assert not loop.registered
    assert (await loop.call(SEND)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_off")
@pytest.mark.parametrize(
    "context",
    [
        _context(config=_Unreadable()),
        _context({"default": {"writes": "maybe"}}),
        _context({"org": {ORG: {"writes": "allow"}}}),
    ],
    ids=["store-fails", "invalid-layer", "invalid-document"],
)
async def test_lf4_a_document_nobody_can_read_fails_closed(context: SimpleNamespace) -> None:
    context.previous_conversations = HISTORY
    loop = await Loop(context).build()
    assert loop.registered
    assert _refused(await loop.call(SEND))


@pytest.mark.usefixtures("switch_off")
async def test_lf5_the_legacy_factory_builds_the_loop_with_the_document_s_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    config = FakeConfigurationService()
    config.put(APPROVALS, {"default": {"flow": {"level": "ask"}}})
    context = make_context(llm=FakeChatModel(), config_service=config, agent_key=AGENT)
    context.previous_conversations = HISTORY
    _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(context, context.llm, "react", query="post it")
    ctx = ToolCallContext(tool_path=SEND[0], tool_input={}, tags=SEND[1])
    assert runtime.hooks is not None
    await cast("Pipeline[ToolCallContext]", runtime.hooks.on(HookEvent.PRE_TOOL_USE)).dispatch(ctx)  # pyright: ignore[reportUnknownMemberType]
    assert _refused(ctx)


@pytest.mark.usefixtures("switch_default")
async def test_tr2_the_second_send_in_a_request_is_refused_and_says_why() -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(SEND)).decision == PreDecision.ALLOW
    second = await loop.call(COMMENT)
    assert _refused(second, LEGACY_SEND_REFUSAL)
    assert "in their language" in LEGACY_SEND_REFUSAL and LEGACY_SEND_REFUSAL.startswith("approval_unavailable:")


@pytest.mark.usefixtures("switch_default")
@pytest.mark.parametrize("first", [MCP, DRAFT, READ], ids=["untagged", "own-space-write", "read"])
async def test_tr2_any_result_counts_as_read(first: tuple[str, tuple[Tag, ...]]) -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(first)).decision == PreDecision.ALLOW
    assert (await loop.call(SEND)).decision == PreDecision.DENY


@pytest.mark.usefixtures("switch_default")
@pytest.mark.parametrize("first", [PLAN, WEB_SEARCH, FETCH], ids=["machinery", "web-search", "web-page"])
async def test_tr3_machinery_and_public_web_results_carry_nothing_private(first: tuple[str, tuple[Tag, ...]]) -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(first)).decision == PreDecision.ALLOW
    assert (await loop.call(SEND)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_default")
async def test_xs8_an_outside_read_after_a_read_is_refused_with_its_own_explanation() -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(READ)).decision == PreDecision.ALLOW
    refused = await loop.call(OUTSIDE_READ)
    assert _refused(refused, LEGACY_WEB_REFUSAL)
    assert "in their language" in LEGACY_WEB_REFUSAL


@pytest.mark.usefixtures("switch_default")
async def test_xs8_the_product_web_tools_run_after_a_read() -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(READ)).decision == PreDecision.ALLOW
    for call in (FETCH, WEB_SEARCH):
        assert (await loop.call(call)).decision == PreDecision.ALLOW
    assert _refused(await loop.call(FETCH, MADE_UP), LEGACY_ADDRESS_REFUSAL)
    assert (await loop.call(WEB_SEARCH, {"query": "Q3 plan numbers"})).decision == PreDecision.ALLOW
    assert _refused(await loop.call(SEND), LEGACY_FLOW_REFUSAL)


@pytest.mark.usefixtures("switch_default")
async def test_xs8_a_made_up_address_runs_before_any_private_read() -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(FETCH, MADE_UP)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_off")
async def test_xs8_with_the_switch_off_and_no_rule_a_made_up_address_runs() -> None:
    loop = await Loop(_context()).build()
    assert (await loop.call(READ)).decision == PreDecision.ALLOW
    assert (await loop.call(FETCH, MADE_UP)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_default")
async def test_xs8_plain_browsing_runs() -> None:
    loop = await Loop(_context()).build()
    for call in (WEB_SEARCH, FETCH, FETCH, WEB_SEARCH):
        assert (await loop.call(call)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_default")
async def test_xs8_a_conversation_with_history_cannot_vouch_for_what_it_read() -> None:
    loop = await Loop(_context(history=HISTORY)).build()
    assert _refused(await loop.call(OUTSIDE_READ), LEGACY_WEB_REFUSAL)
    assert (await loop.call(FETCH)).decision == PreDecision.ALLOW
    assert _refused(await loop.call(FETCH, MADE_UP), LEGACY_ADDRESS_REFUSAL)


def test_lf6_the_agent_route_tells_todays_loop_which_agent_runs() -> None:
    from tests.support.agent_routes import InMemoryGraph, as_user, make_client
    from tests.unit.api.routes.test_agent_chat_route import FakeLoop

    graph = InMemoryGraph()
    graph.add_agent("shared", "alice", share_with_org=True)
    loop = FakeLoop()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.api.routes.agent.run_agent_loop_stream", loop)

        async def llm(*_a: object, **_k: object) -> tuple[object, dict[str, object], dict[str, object]]:
            return object(), {"isMultimodal": False, "provider": "openai"}, {}

        patch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
        client, _ = make_client(graph)
        body = {"query": "hi", "previousConversations": HISTORY, "historyLabel": PUBLIC_HISTORY}
        response = client.post("/api/v1/agent/shared/chat/stream", headers=as_user("bob"), json=body)
    assert response.status_code == 200
    (call,) = loop.calls
    assert call["query_info"]["agentKey"] == "shared"
    assert call["query_info"]["historyLabel"] == PUBLIC_HISTORY


def test_lf6_the_agent_key_reaches_the_loops_context() -> None:
    from app.agents.agent_loop.context import AgentContext

    state: dict[str, Any] = {"org_id": ORG, "user_id": "u", "user_email": "u@x", "agent_key": AGENT}
    assert AgentContext.from_chat_state(state).agent_key == AGENT
    assert AgentContext.from_chat_state({**state, "agent_key": None}).agent_key is None
    assert AgentContext.from_chat_state({**state, "history_label": PUBLIC_HISTORY}).history_label == PUBLIC_HISTORY


@pytest.mark.usefixtures("switch_default")
async def test_tr3_a_call_to_a_tool_the_run_lacks_carries_nothing() -> None:
    """Found while building: the model names a tool the run does not have; it never runs, and the loop answers with
    its own error text, so it neither taints the request nor is refused by the guard."""
    loop = await Loop(_context()).build()
    unknown = ("/unresolved/nosuch__tool", ())
    assert (await loop.call(unknown)).decision == PreDecision.ALLOW
    assert (await loop.call(unknown)).decision == PreDecision.ALLOW
    assert (await loop.call(SEND)).decision == PreDecision.ALLOW


# HL: a follow-up decides from the history's label Node passes, not from having history at all.

SEARCH_KNOWLEDGE = ("/toolsets/retrieval/search_internal_knowledge", (Tag("effect", "read"),))
PUBLIC_HISTORY: dict[str, JsonValue] = {"complete": True, "sources": []}
PRIVATE_HISTORY: dict[str, JsonValue] = {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}]}
UNKNOWN_HISTORY: dict[str, JsonValue] = {"complete": False, "sources": []}


def _follow_up(label: JsonValue) -> SimpleNamespace:
    context = _context(history=HISTORY)
    context.history_label = label
    return context


@pytest.mark.usefixtures("switch_default")
@pytest.mark.parametrize("call", [FETCH, WEB_SEARCH, SEND], ids=["fetch", "web-search", "send"])
async def test_hl1_a_follow_up_after_public_or_no_reads_goes_through(call: tuple[str, tuple[Tag, ...]]) -> None:
    loop = await Loop(_follow_up(PUBLIC_HISTORY)).build()
    assert (await loop.call(call)).decision == PreDecision.ALLOW


@pytest.mark.usefixtures("switch_default")
@pytest.mark.parametrize("label", [PRIVATE_HISTORY, UNKNOWN_HISTORY, None, {"complete": "yes"}], ids=["private", "unknown", "none", "unreadable"])
async def test_hl2_a_follow_up_after_a_private_or_unvouched_read_is_still_held(label: JsonValue) -> None:
    loop = await Loop(_follow_up(label)).build()
    assert _refused(await loop.call(OUTSIDE_READ), LEGACY_WEB_REFUSAL)
    assert _refused(await loop.call(SEND), LEGACY_FLOW_REFUSAL)
    for call in (FETCH, WEB_SEARCH):
        assert (await loop.call(call)).decision == PreDecision.ALLOW
    assert _refused(await loop.call(FETCH, MADE_UP), LEGACY_ADDRESS_REFUSAL)


async def test_hl3_a_rule_from_above_private_holds_unknown_history_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_FLOW_GUARD", raising=False)
    monkeypatch.setenv("HARNESS_FLOW_GUARD_ABOVE", "private")
    private = await Loop(_follow_up(PRIVATE_HISTORY)).build()
    assert (await private.call(OUTSIDE_READ)).decision == PreDecision.ALLOW
    unknown = await Loop(_follow_up(UNKNOWN_HISTORY)).build()
    assert _refused(await unknown.call(OUTSIDE_READ), LEGACY_WEB_REFUSAL)


@pytest.mark.usefixtures("switch_default")
async def test_hl4_the_answer_records_what_the_request_read() -> None:
    browsing = await Loop(_context()).build()
    for call in (WEB_SEARCH, FETCH, PLAN):
        _ = await browsing.call(call)
    assert request_label(browsing.context) == {"complete": True, "sources": []}

    searched = await Loop(_context()).build()
    _ = await searched.call(SEARCH_KNOWLEDGE)
    searched.context.tool_state["virtual_record_id_to_result"] = {"vr-1": {"id": "rec-1"}}
    assert request_label(searched.context) == {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}]}
    _ = await searched.call(READ)
    assert request_label(searched.context) == {"complete": False, "sources": []}


def test_hl4_a_request_the_guard_did_not_watch_records_no_label() -> None:
    assert request_label(_context()) is None


@pytest.mark.usefixtures("switch_default")
async def test_hl5_the_answers_label_makes_the_next_turns_history() -> None:
    """Round trip as Node does it: the first turn browsed only; its answer's label is the second turn's history."""
    first = await Loop(_context()).build()
    _ = await first.call(FETCH)
    label = request_label(first.context)
    second = await Loop(_follow_up(label)).build()
    assert (await second.call(FETCH)).decision == PreDecision.ALLOW
