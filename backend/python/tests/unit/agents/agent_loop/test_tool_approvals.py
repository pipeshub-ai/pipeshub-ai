"""Per-tool approvals (`app/agents/agent_loop/tool_approvals.py`): the decision, the stores, the
PreToolUse gate, and answering a card, end to end through real agent runs: one request stops on
the card, and the answer resumes it in the same turn."""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
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
from app.agents.agent_loop import parked_turn
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.factory import PipesHubAgentFactory, last_real_user_query
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import (
    ToolApprovalAnswer,
    ToolRules,
    decide,
)
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)


class _Store:
    """`ConfigurationService` as the approvals use it."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int | None] = {}
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
        self.ttls[key] = ttl_seconds
        return True

    async def create_config_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        if key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ttl_seconds
        return True

    def pending(self) -> list[dict[str, Any]]:
        return [v for k, v in self.values.items() if "/tool-approvals/pending/" in k]


_ASK, _ALLOW, _BLOCK = "ask", "allow", "block"


class TestDecide:
    @pytest.mark.parametrize("kind,expected", [("read", _ALLOW), ("write", _ASK), ("destructive", _BLOCK)])
    def test_the_starting_rule_follows_what_the_tool_does(self, kind: str, expected: str) -> None:
        assert decide(own=None, kind=kind, watched=True).rule == expected  # type: ignore[arg-type]

    def test_a_deleting_tool_is_denied_by_default_until_someone_allows_it(self) -> None:
        assert decide(own=None, kind="destructive", watched=True) == approvals.Verdict(_BLOCK, "default")
        assert decide(own=_ASK, kind="destructive", watched=True).rule == _ASK
        assert decide(own=_ALLOW, kind="destructive", watched=True).rule == _ALLOW

    @pytest.mark.parametrize("own", [_ALLOW, _ASK, _BLOCK])
    def test_the_own_rule_replaces_the_starting_one(self, own: str) -> None:
        assert decide(own=own, kind="read", watched=True).rule == own  # type: ignore[arg-type]

    def test_a_block_says_whose_it_is(self) -> None:
        assert decide(own=_BLOCK, kind="write", watched=True).reason == "own"
        assert decide(own=_BLOCK, kind="destructive", watched=True).reason == "own"
        assert decide(own=None, kind="destructive", watched=True).reason == "default"

    def test_the_approved_call_runs(self) -> None:
        """Answering the card is the asking."""
        assert decide(own=None, kind="write", watched=True, approved_call=True).rule == _ALLOW

    def test_the_approved_call_is_still_stopped_by_a_block(self) -> None:
        verdict = decide(own=_BLOCK, kind="write", watched=True, approved_call=True)
        assert verdict.rule == _BLOCK

    def test_nobody_watching_turns_ask_into_block(self) -> None:
        assert decide(own=None, kind="write", watched=False) == approvals.Verdict(_BLOCK, "unattended")
        # Only what the person pre-approved runs where nobody can answer.
        assert decide(own=_ALLOW, kind="write", watched=False).rule == _ALLOW
        assert decide(own=None, kind="read", watched=False).rule == _ALLOW
        assert decide(own=_BLOCK, kind="write", watched=False).rule == _BLOCK


def _context(store: _Store | None = None, **overrides: Any) -> Any:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "config_service": store or _Store(), "client_name": "pipeshub-ai", "chat_streaming": True,
        "conversation_id": "conv-1", "agent_key": "agent-1",
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
    async def test_the_persons_own_rules_apply_in_agent_and_assistant_chats_alike(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "allow"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assistant = await _gate(_context(store, is_assistant=True), _adapter(read_only=False))
        agent_chat = await _gate(_context(store), _adapter(read_only=False))
        assert assistant.decision == agent_chat.decision == PreDecision.ALLOW
        assert "/services/mcp/user-tool-rules/user-1/inst-1" in store.values
        assert not [k for k in store.values if "agent-tool-rules" in k]

    async def test_someone_elses_rules_dont_apply(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "allow"}), user_id="user-2", instance_id="inst-1")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=False))
        assert ctx.decision == PreDecision.ASK


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
        assert saved["source"] == "mcp" and details["canRemember"] is True
        assert (saved["toolName"], saved["instanceId"], saved["arguments"]) == ("create_issue", "inst-1", {"title": "Bug"})
        assert (saved["userId"], saved["conversationId"], saved["orgId"]) == ("user-1", "conv-1", "org-1")
        assert details["arguments"] == {"title": "Bug"}
        assert details["toolTitle"] == "Create an issue"
        assert "canAlwaysAllow" not in details and "companyAlwaysAsk" not in details
        assert details["expiresAt"] > time.time() * 1000

    async def test_the_saved_call_keeps_the_models_id_for_it(self) -> None:
        store, adapter = _Store(), _adapter(read_only=False)
        context = _context(store)
        context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
        ctx = _gate_ctx(adapter, title="Bug")
        ctx.scope = SimpleNamespace(call=ToolCall(id="toolu_1", name=adapter.name, arguments={"title": "Bug"}))  # type: ignore[assignment]

        async def _next() -> None:
            return None

        await approvals.approval_gate(context)(ctx, _next)
        (saved,) = store.pending()
        assert saved["toolCallId"] == "toolu_1"

    async def test_only_one_call_waits_per_request(self) -> None:
        context = _context()
        await _gate(context, _adapter(read_only=False), title="one")
        second = await _gate(context, _adapter(read_only=False, name="update_issue"), key="X-1")

        assert second.decision == PreDecision.DENY
        reason = second.decision_reason or ""
        assert "Not run yet" in reason and "another one is waiting" in reason
        # Read when the other card's answer resumes the turn: it has to ask again then.
        assert "call update_issue again with the same arguments" in reason

    async def test_a_deleting_tool_is_denied_and_the_model_is_told_how_to_allow_it(self) -> None:
        store = _Store()
        assistant = await _gate(_context(store, is_assistant=True), _adapter(read_only=False, name="delete_issue"), key="X-1")
        agent_chat = await _gate(_context(store), _adapter(name="deleteIssue"), key="X-1")

        assert assistant.decision == agent_chat.decision == PreDecision.DENY
        assert "can delete data" in (assistant.decision_reason or "")
        assert "the user's tool approval rules" in (assistant.decision_reason or "")
        assert agent_chat.decision_reason == assistant.decision_reason.replace("delete_issue", "deleteIssue")
        assert store.pending() == []

    async def test_a_deleting_tool_someone_set_to_ask_shows_its_kind_on_the_card(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"delete_issue": "ask"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=True, name="delete_issue"), key="X-1")

        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        assert ctx.pending_approval.details["kind"] == "destructive"
        assert ctx.pending_approval.details["readOnly"] is False

    async def test_nobody_watching_blocks_with_a_reason(self) -> None:
        ctx = await _gate(_context(client_name="slack"), _adapter(read_only=False), title="Bug")
        assert ctx.decision == PreDecision.DENY
        assert "approve it in the PipesHub app" in (ctx.decision_reason or "")

    async def test_the_persons_own_block_says_so(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "block"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), _adapter(read_only=False))
        assert "blocked in the user's tool approval rules" in (ctx.decision_reason or "")

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

    async def _claim(self, store: _Store, approval_id: str, **who: Any) -> approvals.Claim:  # noqa: ANN401
        who = {"org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1", **who}
        return await approvals.claim_pending(store, approval_id, **who)  # type: ignore[arg-type]

    async def test_an_approval_is_settled_once_and_then_shows_what_came_of_it(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        first = await self._claim(store, approval_id)
        second = await self._claim(store, approval_id)
        assert first.status == "claimed" and first.pending is not None and first.pending.arguments == {"title": "Bug"}
        assert (second.status, second.outcome) == ("used", None)

        outcome = approvals.Outcome(answer="allowed", status="completed", result="created", ran=True)
        await approvals._keep_outcome(store, approval_id, outcome)  # type: ignore[arg-type]
        assert (await self._claim(store, approval_id)).outcome == outcome

    @pytest.mark.parametrize("who", [
        {"org_id": "org-2"}, {"user_id": "user-2"}, {"conversation_id": "conv-2"}, {"conversation_id": None},
    ])
    async def test_only_the_person_in_that_conversation_can_answer(self, who: dict[str, Any]) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        assert (await self._claim(store, approval_id, **who)) == approvals.Claim("unknown")
        # A wrong answer doesn't use up the approval for the right person.
        assert (await self._claim(store, approval_id)).status == "claimed"

    async def test_an_expired_approval_is_refused_but_still_names_its_call(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        later = time.time() + approvals.APPROVAL_TTL_SECONDS + 1
        with pytest.MonkeyPatch.context() as m:
            m.setattr(approvals.time, "time", lambda: later)
            claim = await self._claim(store, approval_id)
        assert claim.status == "expired" and claim.pending is not None
        assert not [k for k in store.values if "/claims/" in k]

    async def test_one_that_ran_shows_what_came_of_it_even_after_it_expired(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        await self._claim(store, approval_id)
        later = time.time() + approvals.APPROVAL_TTL_SECONDS + 1
        with pytest.MonkeyPatch.context() as m:
            m.setattr(approvals.time, "time", lambda: later)
            assert (await self._claim(store, approval_id)).status == "used"

    async def test_the_records_outlive_the_window_they_can_be_answered_in(self) -> None:
        store = _Store()
        approval_id = await self._saved(store)
        await self._claim(store, approval_id)
        assert set(store.ttls.values()) == {approvals._RECORD_TTL_SECONDS}
        assert approvals._RECORD_TTL_SECONDS > approvals.APPROVAL_TTL_SECONDS

    async def test_an_unknown_approval_is_refused(self) -> None:
        assert (await self._claim(_Store(), "nope")) == approvals.Claim("unknown")


# ---------------------------------------------------------------------------
# End to end: a request stops on the card; the answer resumes it.
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
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def of(self, event_type: EventType, call_id: str | None = None) -> list[dict[str, Any]]:
        return [
            e.payload for e in self.events
            if e.event_type == event_type and (call_id is None or e.payload.get("tool_call_id") == call_id)
        ]


@dataclass
class _Request:
    result: Any
    events: _Events
    goal: Goal
    # What the model was sent, per step.
    sent: list[list[Any]]


_QUESTION = "file a bug"
_FILE_IT = ToolCall(id="c1", name="mcp_jira_create_issue", arguments={"title": "Login is broken"})
_DONE = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})
_WAITING = "Waiting for your approval to run create_issue on Jira."


def _stopped_turn(*, with_call: bool = True) -> list[dict[str, Any]]:
    """The conversation as Node sends it after the request that stopped on the card."""
    bot: dict[str, Any] = {"role": "bot_response", "content": _WAITING}
    if with_call:
        bot["tool_results"] = [{
            "tool_id": "c1", "tool_name": "mcp_jira_create_issue", "args": {"title": "Login is broken"},
            "result": _WAITING, "status": "error",
        }]
    return [{"role": "user_query", "content": _QUESTION}, bot]


class _PlanFirst(ReActLoop):
    """Like planExecute: the loop adds its own message before the first step."""

    async def run(self, agent: Any, goal: Goal) -> Any:  # noqa: ANN401
        await agent.inject_user_message("Make a plan first.")
        return await super().run(agent, goal)


async def _request(
    context: Any, mcp: _Server, *model_calls: ToolCall | AssistantMessage, history: list[dict[str, Any]] | None = None,  # noqa: ANN401
    query: str = _QUESTION, loop: Any = None,  # noqa: ANN401
) -> _Request:
    """One chat request, wired the way the factory wires one: history, resume, the two hooks."""
    server = ResolvedMCPServer(
        instance_id="inst-1", name="Jira", display_name="Jira", instance={"_id": "inst-1", "authMode": "none"},
        auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(name="create_issue", namespaced_name="mcp_jira_create_issue", input_schema={
        "type": "object", "properties": {"title": {"type": "string"}},
    })
    adapter = MCPToolAdapter(server, info, SimpleNamespace(call=mcp.call), context=context)  # type: ignore[arg-type]
    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
    return await run_request(context, [adapter], *model_calls, history=history, query=query, loop=loop)


async def run_request(
    context: Any, tools: list[Any], *model_calls: ToolCall | AssistantMessage,  # noqa: ANN401
    history: list[dict[str, Any]] | None = None, query: str = _QUESTION, loop: Any = None,  # noqa: ANN401
) -> _Request:
    """A chat request with these tools, wired the way the factory wires one: history, resume, the two hooks."""
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    for tool in tools:
        registry.register_tool(tool)
    hooks = HookRegistry()
    hooks.on(HookEvent.PRE_AGENT).use(approvals.resume_parked_call(context))
    hooks.on(HookEvent.PRE_TOOL_USE).use(approvals.approval_gate(context))
    transport = ScriptedTransport()
    for call in model_calls:
        if isinstance(call, AssistantMessage):
            transport._script.append(ScriptedStep(message=call))
        else:
            transport.add_tool_call(call)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    events = _Events()
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, hooks=hooks, event_emitter=events)  # type: ignore[arg-type]
    spec = AgentSpec(
        name="a", system_prompt="s", model=ModelSpec(provider="scripted", model="m"), max_turns=5, loop=loop or ReActLoop(),
    )
    agent = Agent(spec, runtime)
    context.previous_conversations = history or []
    resuming = bool(history) and approvals.begin_resume(context, query)
    goal = Goal(description=last_real_user_query(history, query) if resuming else query)
    if history:
        await PipesHubAgentFactory._seed_conversation_history(agent, history, context)
        approvals.hold_history(context, agent)
    result = await agent.run(goal, _skip_user_message=resuming)
    # As the stream bridge does after a run.
    await approvals.keep_stopped_turn(context, agent)
    return _Request(result, events, goal, [list(call["messages"]) for call in transport.calls])


async def _ask(store: _Store, mcp: _Server) -> str:
    asked = await _request(_context(store), mcp, _FILE_IT, _DONE)
    assert mcp.calls == []
    assert asked.result.output == _WAITING
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL
    return blocked["approval"]["approvalId"]


async def _answer(
    store: _Store, mcp: _Server, approval_id: str, decision: str, *model_calls: ToolCall,
    remember: bool = False, history: list[dict[str, Any]] | None = None, loop: Any = None, **overrides: Any,  # noqa: ANN401
) -> _Request:
    answer = ToolApprovalAnswer(approval_id=approval_id, decision=decision, remember=remember)  # type: ignore[arg-type]
    context = _context(store, tool_approval=answer, **overrides)
    return await _request(
        context, mcp, *(model_calls or (_DONE,)), history=history or _stopped_turn(), query=f"Tool approval: {decision}",
        loop=loop,
    )


def _result_of(sent: list[Any], call_id: str = "c1") -> ToolMessage:
    (message,) = [m for m in sent if isinstance(m, ToolMessage) and m.tool_call_id == call_id]
    return message


class TestAnsweringTheCard:
    async def test_the_approved_call_runs_once_and_answers_the_call_the_model_made(self) -> None:
        """The bug this fixes: the answer was a new message, the model called the tool again, and
        every Allow asked again and ran it again (three pages for one request)."""
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow")

        assert mcp.calls == [{"tool": "create_issue", "title": "Login is broken"}]
        first_step = answered.sent[0]
        assert [m.content for m in first_step if isinstance(m, UserMessage)] == [_QUESTION]
        assert isinstance(first_step[-2], AssistantMessage) and [c.id for c in first_step[-2].tool_calls] == ["c1"]
        assert first_step[-1] == _result_of(first_step)
        assert (first_step[-1].content, first_step[-1].is_error) == ("created Login is broken", False)
        assert not any(isinstance(m, AssistantMessage) and _WAITING in (m.text or "") for m in first_step)
        assert answered.goal.description == _QUESTION and answered.goal.constraints == []
        assert answered.events.of(EventType.TOOL_BLOCKED) == []
        assert answered.result.output == "done"

    async def test_the_answer_settles_the_cards_own_row(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow")

        (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
        assert (started["approved"], started["approval_answer"], started["args"]) == (True, "allowed", {"title": "Login is broken"})
        (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
        assert (ended["status"], ended["content"]) == (ToolCallStatus.SUCCESS, "created Login is broken")

    async def test_a_model_that_calls_it_again_is_asked_again_rather_than_running_it_twice(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        again = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Login is broken"})
        answered = await _answer(store, mcp, approval_id, "allow", again, _DONE)

        assert len(mcp.calls) == 1
        (blocked,) = answered.events.of(EventType.TOOL_BLOCKED)
        assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL

    @pytest.mark.parametrize("decision,ran", [("allow", [{"tool": "create_issue", "title": "Login is broken"}]), ("deny", [])])
    async def test_two_actions_asked_for_at_once_get_a_card_each_in_turn(self, decision: str, ran: list[dict[str, Any]]) -> None:
        """Bug bash: "create two pages" asked for the first, then dropped the second."""
        store, mcp = _Store(), _Server()
        second = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Signup is broken"})
        both = AssistantMessage(content="", tool_calls=[_FILE_IT, second])
        asked = await _request(_context(store), mcp, both, _DONE)
        (blocked,) = [e for e in asked.events.of(EventType.TOOL_BLOCKED) if e.get("approval")]
        assert mcp.calls == [] and len(store.pending()) == 1

        again = ToolCall(id="c4", name="mcp_jira_create_issue", arguments={"title": "Signup is broken"})
        answered = await _answer(store, mcp, blocked["approval"]["approvalId"], decision, again, _DONE)

        assert mcp.calls == ran
        first_step = answered.sent[0]
        assert "call create_issue again" in str(_result_of(first_step, "c3").content)
        if decision == "deny":
            # About that call: it doesn't tell the model to leave the tool alone.
            assert "don't make that call again" in str(_result_of(first_step, "c1").content)
        (next_card,) = answered.events.of(EventType.TOOL_BLOCKED, "c4")
        assert next_card["status"] == ToolCallStatus.AWAITING_APPROVAL
        assert [p["arguments"] for p in store.pending() if p["toolCallId"] == "c4"] == [{"title": "Signup is broken"}]

    async def test_the_same_answer_twice_shows_what_happened_and_runs_nothing(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _answer(store, mcp, approval_id, "allow")

        again = await _answer(store, mcp, approval_id, "allow")

        assert len(mcp.calls) == 1
        assert _result_of(again.sent[0]).content == "created Login is broken"
        (started,) = again.events.of(EventType.TOOL_CALL_START, "c1")
        assert started["approved"] is True
        (ended,) = again.events.of(EventType.TOOL_CALL_END, "c1")
        assert ended["status"] == ToolCallStatus.SUCCESS

    async def test_an_answer_still_being_applied_isnt_applied_again(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await approvals.claim_pending(store, approval_id, org_id="org-1", user_id="user-1", conversation_id="conv-1")  # type: ignore[arg-type]

        answered = await _answer(store, mcp, approval_id, "allow")

        assert mcp.calls == []
        assert "may still be running" in _result_of(answered.sent[0]).content
        assert answered.events.of(EventType.TOOL_CALL_START, "c1") == []

    async def test_deny_runs_nothing_and_tells_the_model(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "deny")

        assert mcp.calls == []
        result = _result_of(answered.sent[0])
        assert "declined" in result.content and result.is_error
        (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
        assert started["approval_answer"] == "declined" and "approved" not in started
        (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
        assert ended["status"] == ToolCallStatus.BLOCKED
        assert await approvals.load_own_rules(store, user_id="user-1", instance_id="inst-1") == ToolRules()  # type: ignore[arg-type]

    async def test_allow_and_dont_ask_again_pre_approves_the_tool_everywhere(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _answer(store, mcp, approval_id, "allow", remember=True)

        rules = await approvals.load_own_rules(store, user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {"create_issue": "allow"}
        elsewhere = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Elsewhere"})
        await _request(_context(store, conversation_id="conv-9"), mcp, elsewhere, _DONE)
        assert [c["title"] for c in mcp.calls] == ["Login is broken", "Elsewhere"]

    async def test_deny_and_dont_ask_again_denies_the_tool_from_now_on(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await _answer(store, mcp, approval_id, "deny", remember=True)

        rules = await approvals.load_own_rules(store, user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {"create_issue": "block"}

    async def test_an_expired_card_runs_nothing_and_settles_its_row(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        later = time.time() + approvals.APPROVAL_TTL_SECONDS + 1

        with pytest.MonkeyPatch.context() as m:
            m.setattr(approvals.time, "time", lambda: later)
            answered = await _answer(store, mcp, approval_id, "allow")

        assert mcp.calls == []
        assert "expired" in _result_of(answered.sent[0]).content
        (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
        assert started["approval_answer"] == "expired"

    async def test_someone_else_answering_runs_nothing(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow", user_id="user-2")

        assert mcp.calls == []
        assert "wasn't found" in _result_of(answered.sent[0]).content
        assert answered.events.of(EventType.TOOL_CALL_START, "c1") == []

    async def test_a_block_set_since_the_card_was_shown_still_stops_the_call(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "block"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]

        answered = await _answer(store, mcp, approval_id, "allow", remember=True)

        assert mcp.calls == []
        assert "blocked in the user's tool approval rules" in _result_of(answered.sent[0]).content
        (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
        assert ended["status"] == ToolCallStatus.BLOCKED
        rules = await approvals.load_own_rules(store, user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
        assert rules.tools == {"create_issue": "block"}

    async def test_a_store_that_cant_be_read_claims_nothing_and_the_card_can_be_answered_again(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)
        store.reads_fail = True
        answered = await _answer(store, mcp, approval_id, "allow")
        store.reads_fail = False

        assert mcp.calls == []
        assert "couldn't be applied" in _result_of(answered.sent[0]).content
        await _answer(store, mcp, approval_id, "allow")
        assert len(mcp.calls) == 1

    async def test_a_failure_while_it_runs_says_it_may_have_run(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        class _Broken:
            def __init__(self, *_args: Any) -> None:  # noqa: ANN401
                pass

            async def call_tool(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
                raise RuntimeError("the connection dropped")

        with pytest.MonkeyPatch.context() as m:
            m.setattr(approvals, "ToolExecutor", _Broken)
            answered = await _answer(store, mcp, approval_id, "allow")

        assert "may or may not have run" in _result_of(answered.sent[0]).content
        (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
        assert started["approved"] is True
        (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
        assert ended["status"] == ToolCallStatus.ERROR

    async def test_a_loop_that_speaks_first_still_gets_the_result_right_after_the_call(self) -> None:
        """planExecute adds a planning message before its first step: the result is bound before
        that, in place, so the call's id isn't added a second time."""
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow", loop=_PlanFirst())

        first_step = answered.sent[0]
        ids = [call.id for m in first_step if isinstance(m, AssistantMessage) for call in m.tool_calls or []]
        assert ids.count("c1") == 1
        assert [type(m).__name__ for m in first_step[-3:]] == ["AssistantMessage", "ToolMessage", "UserMessage"]
        assert first_step[-2].content == "created Login is broken" and first_step[-1].content == "Make a plan first."
        assert not any(isinstance(m, AssistantMessage) and _WAITING in (m.text or "") for m in first_step)
        assert len(mcp.calls) == 1

    async def test_a_run_stopped_mid_call_still_says_what_may_have_happened(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        class _Stopped:
            def __init__(self, *_args: Any) -> None:  # noqa: ANN401
                pass

            async def call_tool(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
                raise asyncio.CancelledError

        with pytest.MonkeyPatch.context() as m, pytest.raises(asyncio.CancelledError):
            m.setattr(approvals, "ToolExecutor", _Stopped)
            await _answer(store, mcp, approval_id, "allow")

        again = await _answer(store, mcp, approval_id, "allow")
        assert "may or may not have run" in _result_of(again.sent[0]).content
        assert mcp.calls == []

    async def test_a_call_the_history_doesnt_have_is_added_with_its_result(self) -> None:
        """A sub-agent's call: its row is nested, so the root's history doesn't hold it."""
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow", history=_stopped_turn(with_call=False))

        first_step = answered.sent[0]
        assert isinstance(first_step[-2], AssistantMessage) and [c.id for c in first_step[-2].tool_calls] == ["c1"]
        assert first_step[-1].content == "created Login is broken"
        assert not any(isinstance(m, AssistantMessage) and _WAITING in (m.text or "") for m in first_step)


_LOOKED_UP = "The issue tracker's project for this is PIPE, id 4242."


class TestTheStoppedTurnCarriesOver:
    """The answer is a new request, and the history Node sends it has each earlier result only as
    its summary: the turn the model had is kept with the card and put back."""

    async def _ask_after_looking_up(self, store: _Store, mcp: _Server) -> str:
        step = AssistantMessage(content=_LOOKED_UP, tool_calls=[_FILE_IT])
        asked = await _request(_context(store), mcp, step, _DONE)
        (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
        return blocked["approval"]["approvalId"]

    async def test_the_answer_carries_on_with_the_turn_as_the_model_had_it(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await self._ask_after_looking_up(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow")

        first_step = answered.sent[0]
        assert any(isinstance(m, AssistantMessage) and _LOOKED_UP in (m.text or "") for m in first_step)
        assert first_step[-1] == _result_of(first_step) and first_step[-1].content == "created Login is broken"
        assert [m.content for m in first_step if isinstance(m, UserMessage)] == [_QUESTION]
        ids = [call.id for m in first_step if isinstance(m, AssistantMessage) for call in m.tool_calls or []]
        assert ids.count("c1") == 1
        kept = [k for k in store.values if "/parked-turns/" in k]
        assert kept and store.ttls[kept[0]] == approvals.TURN_TTL_SECONDS

    async def test_a_turn_too_large_to_keep_resumes_from_the_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(parked_turn, "_MAX_BYTES", 10)
        store, mcp = _Store(), _Server()
        approval_id = await self._ask_after_looking_up(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow")

        first_step = answered.sent[0]
        assert not any(isinstance(m, AssistantMessage) and _LOOKED_UP in (m.text or "") for m in first_step)
        assert _result_of(first_step).content == "created Login is broken"

    async def test_a_kept_turn_without_the_stopped_call_is_not_used(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await self._ask_after_looking_up(store, mcp)
        (key,) = [k for k in store.values if "/parked-turns/" in k]
        store.values[key]["messages"] = [{"role": "assistant", "content": "something else entirely"}]

        answered = await _answer(store, mcp, approval_id, "allow")

        assert not any(isinstance(m, AssistantMessage) and "something else" in (m.text or "") for m in answered.sent[0])
        assert _result_of(answered.sent[0]).content == "created Login is broken"

    async def test_someone_elses_answer_gets_no_turn(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await self._ask_after_looking_up(store, mcp)

        answered = await _answer(store, mcp, approval_id, "allow", user_id="user-2")

        assert not any(isinstance(m, AssistantMessage) and _LOOKED_UP in (m.text or "") for m in answered.sent[0])

    async def test_the_tools_the_turn_could_see_are_visible_again(self) -> None:
        run = SimpleNamespace(
            visible_tools={"fetch_tools"}, spec=None,
            runtime=SimpleNamespace(tool_registry=SimpleNamespace(names=lambda: ["mcp_jira_create_issue", "fetch_tools"])),
        )

        approvals._show_tools(run, ["mcp_jira_create_issue", "gone_tool"])  # type: ignore[arg-type]

        assert run.visible_tools == {"fetch_tools", "mcp_jira_create_issue"}


class TestWhatCountsAsAnAnswer:
    @pytest.mark.parametrize("overrides,query", [
        ({}, "Allow it please"),
        ({"tool_approval": None}, "Tool approval: allow"),
        ({"client_name": "api"}, "Tool approval: allow"),
        ({"chat_streaming": False}, "Tool approval: allow"),
    ])
    def test_only_the_apps_answer_from_a_watched_chat_resumes(self, overrides: dict[str, Any], query: str) -> None:
        answer = ToolApprovalAnswer(approval_id="ap-1", decision="allow")
        context = _context(**{"tool_approval": answer, **overrides})
        assert approvals.begin_resume(context, query) is False
        assert approvals.RESUMING not in context.tool_state

    def test_the_apps_answer_resumes(self) -> None:
        context = _context(tool_approval=ToolApprovalAnswer(approval_id="ap-1", decision="deny"))
        assert approvals.begin_resume(context, "  Tool approval: deny") is True
        assert context.tool_state[approvals.RESUMING] is True

    async def test_an_answer_that_isnt_a_resume_leaves_the_card_unanswered(self) -> None:
        store, mcp = _Store(), _Server()
        approval_id = await _ask(store, mcp)

        context = _context(store, tool_approval=ToolApprovalAnswer(approval_id=approval_id, decision="allow"), client_name="api")
        await _request(context, mcp, _DONE, history=_stopped_turn(), query="Tool approval: allow")

        assert mcp.calls == []
        assert not [k for k in store.values if "/claims/" in k]
