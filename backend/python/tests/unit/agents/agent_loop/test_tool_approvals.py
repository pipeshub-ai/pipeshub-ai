"""Per-tool approvals (`app/agents/agent_loop/tool_approvals.py`): the decision, the stores, the
PreToolUse gate, and answering a card on the next turn, end to end through a real agent run."""
from __future__ import annotations

import asyncio
import copy
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType, ToolCallStatus
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import (
    CompanyPolicy,
    CompanyToolRule,
    ToolApprovalAnswer,
    ToolRules,
    decide,
)
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _Store:
    """`ConfigurationService` as the approvals use it."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.writes_fail = False
        self.reads_fail = False

    async def get_config(
        self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False, keep_in_cache: bool = True,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        assert use_cache is False and keep_in_cache is False
        if self.reads_fail:
            # Like the real store: a failed read is the default unless the caller asks for the error.
            if raise_on_error:
                raise ConnectionError("the store is down")
            return default
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True) -> bool:  # noqa: ANN401
        assert keep_in_cache is False
        await asyncio.sleep(0)  # a real write yields; parallel calls interleave here
        if self.writes_fail:
            return False
        self.values[key] = copy.deepcopy(value)
        return True

    async def create_config_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def pending(self) -> list[dict[str, Any]]:
        return [v for k, v in self.values.items() if "/tool-approvals/pending/" in k]


_ASK, _ALLOW, _BLOCK = "ask", "allow", "block"


class TestDecide:
    @pytest.mark.parametrize("kind,expected", [("read", _ALLOW), ("write", _ASK), ("destructive", _BLOCK)])
    def test_the_starting_rule_follows_what_the_tool_does(self, kind: str, expected: str) -> None:
        assert decide(own=None, kind=kind, company=None, watched=True).rule == expected  # type: ignore[arg-type]

    def test_a_deleting_tool_is_denied_by_default_until_someone_allows_it(self) -> None:
        assert decide(own=None, kind="destructive", company=None, watched=True) == approvals.Verdict(_BLOCK, "default")
        assert decide(own=_ASK, kind="destructive", company=None, watched=True).rule == _ASK
        assert decide(own=_ALLOW, kind="destructive", company=None, watched=True).rule == _ALLOW
        # A company "Allow on approval" is a floor, not a loosening of the default.
        assert decide(own=None, kind="destructive", company=CompanyToolRule(rule="ask"), watched=True).rule == _BLOCK
        assert decide(own=_ALLOW, kind="destructive", company=CompanyToolRule(rule="ask"), watched=True).rule == _ASK

    @pytest.mark.parametrize("own", [_ALLOW, _ASK, _BLOCK])
    def test_the_own_rule_replaces_the_starting_one(self, own: str) -> None:
        assert decide(own=own, kind="read", company=None, watched=True).rule == own  # type: ignore[arg-type]

    @pytest.mark.parametrize("own,company,expected", [
        (_ALLOW, "ask", _ASK),
        (_ALLOW, "block", _BLOCK),
        (_ASK, "block", _BLOCK),
        (_BLOCK, "ask", _BLOCK),
        (_ALLOW, None, _ALLOW),
    ])
    def test_the_strictest_rule_wins_and_the_company_rule_is_a_floor(self, own: str, company: str | None, expected: str) -> None:
        verdict = decide(own=own, kind="write", company=CompanyToolRule(rule=company), watched=True)  # type: ignore[arg-type]
        assert verdict.rule == expected

    def test_a_block_says_whose_it_is(self) -> None:
        assert decide(own=_ALLOW, kind="write", company=CompanyToolRule(rule="block"), watched=True).reason == "company"
        assert decide(own=_BLOCK, kind="write", company=None, watched=True).reason == "own"
        assert decide(own=_BLOCK, kind="destructive", company=None, watched=True).reason == "own"
        assert decide(own=None, kind="destructive", company=CompanyToolRule(rule="block"), watched=True).reason == "company"

    def test_a_chat_grant_allows_unless_the_company_always_asks(self) -> None:
        assert decide(own=None, kind="write", company=None, watched=True, granted_for_chat=True).rule == _ALLOW
        always_ask = CompanyToolRule(rule="ask")
        assert decide(own=None, kind="write", company=always_ask, watched=True, granted_for_chat=True).rule == _ASK

    def test_the_approved_call_runs_even_when_the_company_always_asks(self) -> None:
        """Answering the card is the asking."""
        always_ask = CompanyToolRule(rule="ask")
        assert decide(own=None, kind="write", company=always_ask, watched=True, approved_call=True).rule == _ALLOW

    def test_the_approved_call_is_still_stopped_by_a_block(self) -> None:
        verdict = decide(own=None, kind="write", company=CompanyToolRule(rule="block"), watched=True, approved_call=True)
        assert verdict.rule == _BLOCK

    def test_nobody_watching_turns_ask_into_block_unless_allowed_unattended(self) -> None:
        assert decide(own=None, kind="write", company=None, watched=False) == approvals.Verdict(_BLOCK, "unattended")
        unattended = CompanyToolRule(unattended=True)
        assert decide(own=None, kind="write", company=unattended, watched=False).rule == _ALLOW
        # Allow and Block don't depend on anyone watching.
        assert decide(own=_ALLOW, kind="write", company=None, watched=False).rule == _ALLOW
        assert decide(own=_BLOCK, kind="write", company=unattended, watched=False).rule == _BLOCK


def _context(store: _Store | None = None, **overrides: Any) -> Any:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "config_service": store or _Store(), "client_name": "pipeshub-ai", "chat_streaming": True,
        "conversation_id": "conv-1", "agent_key": "agent-1", "can_edit_agent": True,
    }
    defaults.update(overrides)
    return make_context(**defaults)


class TestWhoIsWatching:
    @pytest.mark.parametrize("overrides,expected", [
        ({}, True),
        ({"client_name": "slack"}, False),
        ({"client_name": None}, False),
        ({"chat_streaming": False}, False),
        ({"conversation_id": None}, False),
        ({"is_service_account": True}, False),
    ])
    def test_only_a_streaming_web_chat_can_answer_a_card(self, overrides: dict[str, Any], expected: bool) -> None:
        assert approvals.watched(_context(**overrides)) is expected


class TestWhoseRules:
    def test_an_agent_chat_uses_the_agents_rules(self) -> None:
        assert approvals.rules_owner(_context()) == ("agent-1", "user-1")

    def test_an_assistant_chat_uses_the_persons_own(self) -> None:
        assert approvals.rules_owner(_context(is_assistant=True)) == (None, "user-1")
        assert approvals.rules_owner(_context(agent_key=None)) == (None, "user-1")

    def test_always_allow_is_for_agent_editors_or_ones_own_rules(self) -> None:
        assert approvals.can_always_allow(_context())
        assert not approvals.can_always_allow(_context(can_edit_agent=False))
        assert approvals.can_always_allow(_context(can_edit_agent=False, is_assistant=True))


def _adapter(*, read_only: bool | None = None, name: str = "create_issue", instance_id: str = "inst-1") -> MCPToolAdapter:
    server = ResolvedMCPServer(
        instance_id=instance_id, name="Jira", display_name="Jira", instance={"_id": instance_id, "authMode": "none"},
        auth={}, owner_id="user-1", attached_tools=None,
    )
    annotations = {"readOnlyHint": read_only, "title": "Create an issue"} if read_only is not None else None
    info = MCPToolInfo(name=name, namespaced_name=f"mcp_jira_{name}", input_schema={"type": "object"}, annotations=annotations)
    return MCPToolAdapter(server, info, session_manager=None)  # type: ignore[arg-type]


def _gate_ctx(adapter: MCPToolAdapter, **arguments: Any) -> ToolCallContext:  # noqa: ANN401
    return ToolCallContext(tool_path=adapter.path, tool_input=dict(arguments))


async def _gate(context: Any, adapter: MCPToolAdapter, **arguments: Any) -> ToolCallContext:  # noqa: ANN401
    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
    ctx = _gate_ctx(adapter, **arguments)
    reached: list[bool] = []

    async def _next() -> None:
        reached.append(True)

    await approvals.approval_gate(context)(ctx, _next)
    assert reached == [True], "the gate passes the call on; the executor acts on the decision"
    return ctx


class TestTheGate:
    async def test_a_read_only_tool_runs(self) -> None:
        ctx = await _gate(_context(), _adapter(read_only=True), q="x")
        assert ctx.decision == PreDecision.ALLOW

    async def test_another_kind_of_tool_isnt_touched(self) -> None:
        context = _context()
        ctx = ToolCallContext(tool_path="/toolsets/jira/create_issue", tool_input={})

        async def _next() -> None:
            return None

        await approvals.approval_gate(context)(ctx, _next)
        assert ctx.decision == PreDecision.ALLOW

    async def test_ask_saves_the_call_and_leaves_it_pending(self) -> None:
        store = _Store()
        ctx = await _gate(_context(store), _adapter(read_only=False), title="Bug")

        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        details = ctx.pending_approval.details
        (saved,) = store.pending()
        assert saved["approvalId"] == details["approvalId"]
        assert (saved["toolName"], saved["instanceId"], saved["arguments"]) == ("create_issue", "inst-1", {"title": "Bug"})
        assert (saved["userId"], saved["conversationId"], saved["orgId"]) == ("user-1", "conv-1", "org-1")
        assert details["arguments"] == {"title": "Bug"}
        assert details["toolTitle"] == "Create an issue"
        assert details["canAlwaysAllow"] is True
        assert details["companyAlwaysAsk"] is False
        assert details["expiresAt"] > time.time() * 1000

    async def test_a_company_always_ask_offers_no_lasting_choice(self) -> None:
        store = _Store()
        await approvals.save_company_policy(store, "inst-1", CompanyPolicy(tools={"create_issue": CompanyToolRule(rule="ask")}))  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=True), title="Bug")

        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        details = ctx.pending_approval.details
        assert details["companyAlwaysAsk"] is True
        assert details["canAlwaysAllow"] is False

    async def test_only_one_call_waits_per_request(self) -> None:
        context = _context()
        await _gate(context, _adapter(read_only=False), title="one")
        second = await _gate(context, _adapter(read_only=False, name="update_issue"), key="X-1")

        assert second.decision == PreDecision.DENY
        assert "already waiting" in (second.decision_reason or "")

    async def test_a_deleting_tool_is_denied_and_the_model_is_told_how_to_allow_it(self) -> None:
        store = _Store()
        assistant = await _gate(_context(store, is_assistant=True), _adapter(read_only=False, name="delete_issue"), key="X-1")
        agent_chat = await _gate(_context(store), _adapter(name="deleteIssue"), key="X-1")

        assert assistant.decision == agent_chat.decision == PreDecision.DENY
        assert "can delete data" in (assistant.decision_reason or "")
        assert "your tool approval rules" in (assistant.decision_reason or "")
        assert "this agent's tool approval rules" in (agent_chat.decision_reason or "")
        assert store.pending() == []

    async def test_a_deleting_tool_someone_set_to_ask_shows_its_kind_on_the_card(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"delete_issue": "ask"}), agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=True, name="delete_issue"), key="X-1")

        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        assert ctx.pending_approval.details["kind"] == "destructive"
        assert ctx.pending_approval.details["readOnly"] is False

    async def test_nobody_watching_blocks_with_a_reason(self) -> None:
        ctx = await _gate(_context(client_name="slack"), _adapter(read_only=False), title="Bug")
        assert ctx.decision == PreDecision.DENY
        assert "approve it in the PipesHub app" in (ctx.decision_reason or "")

    async def test_an_admin_can_allow_a_tool_unattended(self) -> None:
        store = _Store()
        await approvals.save_company_policy(store, "inst-1", CompanyPolicy(tools={"create_issue": CompanyToolRule(unattended=True)}))  # type: ignore[arg-type]
        ctx = await _gate(_context(store, client_name="slack"), _adapter(read_only=False), title="Bug")
        assert ctx.decision == PreDecision.ALLOW

    async def test_a_company_block_says_so(self) -> None:
        store = _Store()
        await approvals.save_company_policy(store, "inst-1", CompanyPolicy(tools={"create_issue": CompanyToolRule(rule="block")}))  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=True), title="Bug")
        assert ctx.decision == PreDecision.DENY
        assert "organization's settings" in (ctx.decision_reason or "")

    async def test_the_agents_own_block_says_so(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "block"}), agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=False))
        assert "blocked for this agent" in (ctx.decision_reason or "")

    async def test_a_persons_own_rules_apply_in_their_assistant_chats(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "allow"}), agent_key=None, user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assistant = await _gate(_context(store, is_assistant=True), _adapter(read_only=False))
        agent_chat = await _gate(_context(store), _adapter(read_only=False))
        assert assistant.decision == PreDecision.ALLOW
        assert agent_chat.decision == PreDecision.ASK

    async def test_a_call_that_cant_be_saved_isnt_left_pending(self) -> None:
        store = _Store()
        store.writes_fail = True
        ctx = await _gate(_context(store), _adapter(read_only=False))
        assert ctx.decision == PreDecision.DENY
        assert ctx.pending_approval is None

    async def test_rules_that_cant_be_read_refuse_every_tool_even_a_read_only_one(self) -> None:
        store = _Store()
        store.reads_fail = True
        ctx = await _gate(_context(store), _adapter(read_only=True), q="x")
        assert ctx.decision == PreDecision.DENY
        assert "couldn't be checked" in (ctx.decision_reason or "")

    async def test_two_asks_in_one_wave_leave_one_waiting(self) -> None:
        store = _Store()
        context = _context(store)
        first, second = await asyncio.gather(
            _gate(context, _adapter(read_only=False), title="one"),
            _gate(context, _adapter(read_only=False, name="update_issue"), key="X-1"),
        )
        assert sorted(c.decision for c in (first, second)) == sorted([PreDecision.ASK, PreDecision.DENY])
        assert len(store.pending()) == 1

    async def test_a_failed_save_frees_the_slot_for_the_next_ask(self) -> None:
        store = _Store()
        context = _context(store)
        store.writes_fail = True
        assert (await _gate(context, _adapter(read_only=False))).decision == PreDecision.DENY
        store.writes_fail = False
        assert (await _gate(context, _adapter(read_only=False, name="update_issue"))).decision == PreDecision.ASK

    async def test_the_kill_switch_lets_everything_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TOOL_APPROVALS", "false")
        ctx = await _gate(_context(), _adapter(read_only=False))
        assert ctx.decision == PreDecision.ALLOW


class TestClaimingAnApproval:
    async def _saved(self, store: _Store) -> str:
        ctx = await _gate(_context(store), _adapter(read_only=False), title="Bug")
        assert ctx.pending_approval is not None
        return ctx.pending_approval.details["approvalId"]

    async def test_an_approval_is_used_once(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        first = await approvals.claim_pending(store, approval_id, org_id="org-1", user_id="user-1", conversation_id="conv-1")  # type: ignore[arg-type]
        second = await approvals.claim_pending(store, approval_id, org_id="org-1", user_id="user-1", conversation_id="conv-1")  # type: ignore[arg-type]
        assert first is not None and first.arguments == {"title": "Bug"}
        assert second is None

    @pytest.mark.parametrize("who", [
        {"org_id": "org-2", "user_id": "user-1", "conversation_id": "conv-1"},
        {"org_id": "org-1", "user_id": "user-2", "conversation_id": "conv-1"},
        {"org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-2"},
        {"org_id": "org-1", "user_id": "user-1", "conversation_id": None},
    ])
    async def test_only_the_person_in_that_conversation_can_answer(self, who: dict[str, Any]) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        assert await approvals.claim_pending(store, approval_id, **who) is None  # type: ignore[arg-type]
        # A wrong answer doesn't use up the approval for the right person.
        assert await approvals.claim_pending(store, approval_id, org_id="org-1", user_id="user-1", conversation_id="conv-1") is not None  # type: ignore[arg-type]

    async def test_an_expired_approval_is_refused(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        later = time.time() + approvals.APPROVAL_TTL_SECONDS + 1
        with pytest.MonkeyPatch.context() as m:
            m.setattr(approvals.time, "time", lambda: later)
            assert await approvals.claim_pending(store, approval_id, org_id="org-1", user_id="user-1", conversation_id="conv-1") is None  # type: ignore[arg-type]

    async def test_an_unknown_approval_is_refused(self) -> None:
        assert await approvals.claim_pending(_Store(), "nope", org_id="org-1", user_id="user-1", conversation_id="conv-1") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End to end: an agent run asks, the next one answers.
# ---------------------------------------------------------------------------

class _Server:
    """The MCP server behind the adapter: what each call was sent with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, server: Any, tool_name: str, arguments: dict[str, Any], **_kwargs: Any) -> Any:  # noqa: ANN401
        from mcp.types import CallToolResult, TextContent

        self.calls.append({"tool": tool_name, **arguments})
        return CallToolResult(content=[TextContent(type="text", text=f"created {arguments.get('title')}")])


class _Events:
    def __init__(self, *, fail_on_approved_end: bool = False) -> None:
        self.events: list[AgentEvent] = []
        self.fail_on_approved_end = fail_on_approved_end

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.fail_on_approved_end and event.event_type == EventType.TOOL_CALL_END and str(event.payload.get("tool_call_id", "")).startswith("approved_"):
            raise RuntimeError("the stream went away")

    def of(self, event_type: EventType) -> list[dict[str, Any]]:
        return [e.payload for e in self.events if e.event_type == event_type]


async def _turn(context: Any, mcp: _Server, *model_calls: ToolCall, events: _Events | None = None) -> tuple[Any, _Events, Goal]:  # noqa: ANN401
    server = ResolvedMCPServer(
        instance_id="inst-1", name="Jira", display_name="Jira", instance={"_id": "inst-1", "authMode": "none"},
        auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(name="create_issue", namespaced_name="mcp_jira_create_issue", input_schema={
        "type": "object", "properties": {"title": {"type": "string"}},
    })
    adapter = MCPToolAdapter(server, info, SimpleNamespace(call=mcp.call), context=context)  # type: ignore[arg-type]
    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter

    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    registry.register_tool(adapter)
    hooks = HookRegistry()
    hooks.on(HookEvent.PRE_TURN).use(approvals.run_approved_call(context))
    hooks.on(HookEvent.PRE_TOOL_USE).use(approvals.approval_gate(context))
    transport = ScriptedTransport()
    for call in model_calls:
        transport.add_tool_call(call)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    events = events or _Events()
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, hooks=hooks, event_emitter=events)  # type: ignore[arg-type]
    spec = AgentSpec(name="a", system_prompt="s", model=ModelSpec(provider="scripted", model="m"), max_turns=5)
    goal = Goal(description="file a bug")
    result = await Agent(spec, runtime).run(goal)
    return result, events, goal


_FILE_IT = ToolCall(id="c1", name="mcp_jira_create_issue", arguments={"title": "Login is broken"})
_DONE = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})


async def _ask(store: _Store, mcp: _Server) -> str:
    result, events, _ = await _turn(_context(store), mcp, _FILE_IT, _DONE)
    assert mcp.calls == []
    assert result.output == "Waiting for your approval to run create_issue on Jira."
    (blocked,) = events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL
    return blocked["approval"]["approvalId"]


def _answered(store: _Store, approval_id: str, decision: str, **overrides: Any) -> Any:  # noqa: ANN401
    return _context(store, tool_approval=ToolApprovalAnswer(approval_id=approval_id, decision=decision), **overrides)  # type: ignore[arg-type]


class TestAnsweringTheCard:
    async def test_allow_once_runs_the_saved_call_exactly_once_and_tells_the_model(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        # The model, left to itself, would have sent different arguments; the saved ones run.
        result, events, goal = await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE)

        assert mcp.calls == [{"tool": "create_issue", "title": "Login is broken"}]
        assert result.success is True
        assert any("created Login is broken" in c and "don't run it again" in c for c in goal.constraints)
        (started,) = [e for e in events.of(EventType.TOOL_CALL_START) if e["tool"] == "mcp_jira_create_issue"]
        assert started["args"] == {"title": "Login is broken"}
        assert started["approved"] is True
        (ended,) = [e for e in events.of(EventType.TOOL_CALL_END) if e["tool"] == "mcp_jira_create_issue"]
        assert ended["status"] == ToolCallStatus.SUCCESS
        assert ended["tool_call_id"] == started["tool_call_id"]

    async def test_the_same_answer_twice_runs_nothing_the_second_time(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE)

        _, _, goal = await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE)

        assert len(mcp.calls) == 1
        assert any("expired or was already used" in c for c in goal.constraints)

    async def test_someone_else_answering_runs_nothing(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        await _turn(_answered(store, approval_id, "allow_once", user_id="user-2"), mcp, _DONE)

        assert mcp.calls == []

    async def test_deny_runs_nothing_and_tells_the_model(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        _, _, goal = await _turn(_answered(store, approval_id, "deny"), mcp, _DONE)

        assert mcp.calls == []
        assert any("declined" in c for c in goal.constraints)

    async def test_allow_for_this_chat_stops_later_calls_from_asking(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _turn(_answered(store, approval_id, "allow_chat"), mcp, _DONE)

        result, _, _ = await _turn(_context(store), mcp, ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Next"}), _DONE)

        assert [c["title"] for c in mcp.calls] == ["Login is broken", "Next"]
        assert result.output == "done"

    async def test_always_allow_by_an_editor_sets_the_agents_rule(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _turn(_answered(store, approval_id, "always"), mcp, _DONE)

        rules = await approvals.load_own_rules(store, agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {"create_issue": "allow"}
        # And in another conversation too.
        await _turn(_context(store, conversation_id="conv-9"), mcp, ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Elsewhere"}), _DONE)
        assert [c["title"] for c in mcp.calls] == ["Login is broken", "Elsewhere"]

    async def test_always_allow_by_someone_who_cant_edit_the_agent_covers_this_chat_only(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _turn(_answered(store, approval_id, "always", can_edit_agent=False), mcp, _DONE)

        rules = await approvals.load_own_rules(store, agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {}
        assert await approvals.load_chat_grants(store, "conv-1", "inst-1") == {"create_issue"}  # type: ignore[arg-type]

    async def test_a_block_set_since_the_card_was_shown_still_stops_the_call(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await approvals.save_company_policy(store, "inst-1", CompanyPolicy(tools={"create_issue": CompanyToolRule(rule="block")}))  # type: ignore[arg-type]

        _, _, goal = await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE)

        assert mcp.calls == []
        assert any("organization's settings" in c for c in goal.constraints)

    async def test_always_allow_doesnt_undo_a_block_set_since_the_card_was_shown(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "block"}), agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]

        _, events, goal = await _turn(_answered(store, approval_id, "always"), mcp, _DONE)

        assert mcp.calls == []
        rules = await approvals.load_own_rules(store, agent_key="agent-1", user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {"create_issue": "block"}
        assert any("wasn't run" in c and "blocked for this agent" in c for c in goal.constraints)
        (ended,) = [e for e in events.of(EventType.TOOL_CALL_END) if e["tool"] == "mcp_jira_create_issue"]
        assert ended["status"] == ToolCallStatus.BLOCKED

    async def test_an_answer_while_the_rules_cant_be_read_saves_and_runs_nothing(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        store.reads_fail = True

        _, _, goal = await _turn(_answered(store, approval_id, "always"), mcp, _DONE)

        store.reads_fail = False
        assert mcp.calls == []
        assert await approvals.load_own_rules(store, agent_key="agent-1", user_id="user-1", instance_id="inst-1") == ToolRules()  # type: ignore[arg-type]
        assert any("nothing ran" in c for c in goal.constraints)

    async def test_an_answer_from_a_run_nobody_watches_runs_nothing_and_the_card_stays_answerable(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        _, _, goal = await _turn(_answered(store, approval_id, "allow_once", client_name="api"), mcp, _DONE)
        assert mcp.calls == []
        assert any("only be answered in the PipesHub app" in c for c in goal.constraints)

        await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE)
        assert len(mcp.calls) == 1

    async def test_a_failure_after_the_call_ran_says_it_ran(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        _, _, goal = await _turn(_answered(store, approval_id, "allow_once"), mcp, _DONE, events=_Events(fail_on_approved_end=True))

        assert len(mcp.calls) == 1
        assert any("it ran, but its result couldn't be read back" in c and "Don't run it again" in c for c in goal.constraints)
