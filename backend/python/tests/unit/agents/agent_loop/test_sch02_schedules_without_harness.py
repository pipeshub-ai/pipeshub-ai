"""ADR-SCH-02: schedules whether or not an org uses the agent harness.

Scheduling is the Labs flag alone (D1). A turn a schedule fired keeps its asks for the person instead of refusing
them (D4), and is never offered ``schedule_create``. Classic chat has ``schedule_create`` too (D5): the harness
tool's arguments and checks (one module), asked every time on classic chat's card, and created with the person's
own request through the public schedules API.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import schedule_tool
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.schedule_tool import (
    SCHEDULES_API_PATH,
    ClassicScheduleCreateTool,
    SchedulesApi,
    register_schedule_tool,
)
from app.agents.agent_loop.tool_approvals import ToolApprovalAnswer
from app.harness import schedules as harness_schedules
from app.schedules import arguments as shared
from app.schedules.firing import scheduled_turn
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_scheduled_runs_on,
)
from tests.unit.agents.agent_loop.test_tool_approvals import (
    _context,
    _result_of,
    _Store,
    run_request,
)

_AUTH = "Bearer person-token"
_DONE = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})


def _in(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _once(minutes: int = 10) -> dict[str, Any]:
    return {
        "name": "Joke later",
        "ask": "Tell me a short, clean joke.",
        "when": {"kind": "once", "at": _in(minutes), "timeZone": "America/Los_Angeles"},
    }


class _Node:
    """Node's public schedules API: answers each create with ``status`` and ``body``, or is unreachable."""

    def __init__(self, status: int = 201, body: object = None, *, down: bool = False) -> None:
        self.status, self.body, self.down = status, body, down
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.down:
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(self.status, json=self.body)

        return httpx.MockTransport(handle)


def _view(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": "sch-1", "name": arguments["name"], "trigger": arguments["when"], "nextFireAt": None, "status": "active"}


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def on(_config: object) -> bool:
        return True

    async def endpoint(_config: object) -> str:
        return "http://node:3000"

    monkeypatch.setattr(schedule_tool, "is_scheduled_runs_on", on)
    monkeypatch.setattr(schedule_tool, "nodejs_endpoint", endpoint)


def _chat(store: _Store | None = None, *, authorization: str | None = _AUTH, **overrides: Any) -> AgentContext:  # noqa: ANN401
    context = _context(store, **overrides)
    context._node_authorization = authorization  # pyright: ignore[reportPrivateUsage]
    context.tool_state.update({"chat_mode": "quick", "model_key": "mk-1", "model_name": "gpt-x"})
    return context


async def _offered(context: AgentContext, node: _Node | None = None) -> ClassicScheduleCreateTool | None:
    registry = ToolRegistry()
    api = SchedulesApi(context.config_service, transport=(node or _Node()).transport())
    if not await register_schedule_tool(registry, context, api=api):
        return None
    tool = registry.resolve_by_name(shared.SCHEDULE_CREATE_TOOL)
    assert isinstance(tool, ClassicScheduleCreateTool)
    return tool


async def _gate(context: AgentContext, arguments: dict[str, Any]) -> ToolCallContext:
    ctx = ToolCallContext(tool_path=schedule_tool.SCHEDULE_TOOL_PATH, tool_input=dict(arguments))

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


def _scheduled(deadline_in: float = 3600, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"schedule_firing_id": "fir-1", "ask_deadline": time.time() + deadline_in, **overrides}


# --- D1: the gate -----------------------------------------------------------------------------------------------------


class _Config:
    def __init__(self, flags: dict[str, bool], *, broken: bool = False) -> None:
        self.flags, self.broken = flags, broken

    async def get_config(self, key: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        if self.broken:
            raise RuntimeError("store down")
        return {"featureFlags": self.flags} if key == PLATFORM_SETTINGS_KEY else default


@pytest.mark.parametrize("worker", ["true", "false"])
@pytest.mark.parametrize(
    ("flags", "on"),
    [
        ({"ENABLE_SCHEDULED_RUNS": True}, True),
        ({"ENABLE_SCHEDULED_RUNS": True, "ENABLE_AGENT_HARNESS": False}, True),
        ({"ENABLE_AGENT_HARNESS": True}, False),
        ({}, False),
    ],
)
async def test_sch02_scheduling_is_the_labs_flag_alone(
    monkeypatch: pytest.MonkeyPatch, worker: str, flags: dict[str, bool], on: bool
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", worker)
    assert await is_scheduled_runs_on(_Config(flags)) is on  # type: ignore[arg-type]


async def test_sch02_an_unreadable_or_missing_setting_is_off() -> None:
    assert await is_scheduled_runs_on(_Config({"ENABLE_SCHEDULED_RUNS": True}, broken=True)) is False  # type: ignore[arg-type]
    assert await is_scheduled_runs_on(None) is False


# --- one module for both tools ----------------------------------------------------------------------------------------


def test_sch02_both_tools_share_the_contract_checks_and_words() -> None:
    harness_tool = harness_schedules.ScheduleCreateTool(object())  # type: ignore[arg-type]
    classic_tool = ClassicScheduleCreateTool(_chat(), SchedulesApi(None))
    assert harness_tool.raw_input_schema == classic_tool.raw_input_schema == shared.contract_schema()
    assert harness_tool.description == classic_tool.description == shared.DESCRIPTION
    assert harness_tool.name == classic_tool.name == shared.SCHEDULE_CREATE_TOOL
    assert harness_schedules.schedule_problem is shared.schedule_problem
    assert harness_schedules.SCHEDULE_TOOL_ID == schedule_tool.SCHEDULE_TOOL_ID == shared.SCHEDULE_TOOL_ID
    for rule in ("never invent", "current time and time zone", "later or repeated"):
        assert rule in shared.DESCRIPTION


_VECTORS = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parents[6] / "contracts/harness/vectors/schedule-create-arguments.json")
    .read_text(encoding="utf-8")
)["vectors"]


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
async def test_sch02_every_contract_vector_the_schema_refuses_is_refused_on_classic_without_a_card(
    vector: dict[str, Any],
) -> None:
    if vector["valid"]:
        pytest.skip("a valid vector's time is fixed; its classic verdict follows the lead-time check")
    store = _Store()
    context = _chat(store)
    assert await _offered(context) is not None
    ctx = await _gate(context, vector["arguments"])
    assert ctx.decision == PreDecision.DENY and "Nothing was scheduled." in (ctx.decision_reason or "")
    assert ctx.pending_approval is None and store.pending() == []


# --- D5: offered ------------------------------------------------------------------------------------------------------


async def test_sch02_a_persons_classic_chat_is_offered_it_with_its_platform_target() -> None:
    context = _chat()
    tool = await _offered(context)
    assert tool is not None
    target = context.tool_state[approvals.PLATFORM_TOOLS_BY_PATH][tool.path]
    assert (target.source, target.tool_id, target.own_rules) == ("platform", shared.SCHEDULE_TOOL_ID, False)


@pytest.mark.parametrize(
    "overrides",
    [
        _scheduled(),
        {"client_name": "slack"},
        {"client_name": "api"},
        {"is_service_account": True},
        {"harness_run": True},
        {"durable_run_id": "run-1"},
    ],
)
async def test_sch02_not_offered_to_a_scheduled_turn_or_a_turn_nobody_can_approve(overrides: dict[str, Any]) -> None:
    assert await _offered(_chat(**overrides)) is None


async def test_sch02_not_offered_with_the_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    async def off(_config: object) -> bool:
        return False

    monkeypatch.setattr(schedule_tool, "is_scheduled_runs_on", off)
    assert await _offered(_chat()) is None


async def test_sch02_not_offered_without_the_requests_own_credential() -> None:
    assert await _offered(_chat(authorization=None)) is None


# --- D5: asks every time ----------------------------------------------------------------------------------------------


async def test_sch02_it_asks_every_time_and_the_card_cannot_remember(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.edition_config.organization_toolset_tool_rules", True, raising=False)
    store = _Store()
    # An org rule or a person's rule for the name cannot answer it: no rule is read for a platform tool.
    await approvals.save_instance_rules(store, approvals.ToolRules(tools={"schedule_create": "allow"}), instance_id="schedules")  # type: ignore[arg-type]
    for _ in range(2):
        context = _chat(store)
        assert await _offered(context) is not None
        ctx = await _gate(context, _once())
        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        details = ctx.pending_approval.details
        assert (details["canRemember"], details["toolId"], details["source"]) == (False, shared.SCHEDULE_TOOL_ID, "platform")
    assert len(store.pending()) == 2


async def test_sch02_env_switches_for_mcp_and_toolsets_do_not_turn_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLSET_TOOL_APPROVALS", "false")
    monkeypatch.setenv("MCP_TOOL_APPROVALS", "false")
    context = _chat()
    assert await _offered(context) is not None
    assert (await _gate(context, _once())).decision == PreDecision.ASK


async def test_sch02_remember_never_keeps_a_rule_for_it() -> None:
    store = _Store()
    pending = approvals.PendingCall(
        approval_id="a1", org_id="org-1", user_id="user-1", conversation_id="conv-1", source="platform",
        instance_id="schedules", server_name="PipesHub", tool_name="schedule_create", created_at=0, expires_at=1,
    )
    assert await approvals._remember(_chat(store), pending, "allow") == "not_allowed"  # pyright: ignore[reportPrivateUsage]
    assert not [k for k in store.values if "rules" in k]


@pytest.mark.parametrize(
    "arguments",
    [
        {**_once(), "when": {"kind": "once", "at": _in(0), "timeZone": "UTC"}},
        {**_once(), "when": {"kind": "cron", "cron": "* * * * *", "timeZone": "UTC"}},
        {**_once(), "when": {"kind": "once", "at": _in(10), "timeZone": "Pacific Time"}},
        {"name": "x"},
    ],
)
async def test_sch02_an_invalid_call_is_refused_to_the_model_without_a_card(arguments: dict[str, Any]) -> None:
    store = _Store()
    context = _chat(store)
    assert await _offered(context) is not None
    ctx = await _gate(context, arguments)
    assert ctx.decision == PreDecision.DENY
    assert ctx.pending_approval is None and store.pending() == []
    assert "Nothing was scheduled." in (ctx.decision_reason or "")


# --- D5: the approved call --------------------------------------------------------------------------------------------


def _stopped(call: ToolCall) -> list[dict[str, Any]]:
    waiting = "Waiting for your approval to run schedule_create on PipesHub."
    return [
        {"role": "user_query", "content": "tell me a joke in 10 minutes"},
        {"role": "bot_response", "content": waiting, "tool_results": [{
            "tool_id": call.id, "tool_name": call.name, "args": call.arguments, "result": waiting, "status": "error",
        }]},
    ]


async def _ask_then_answer(node: _Node, arguments: dict[str, Any], decision: str = "allow") -> tuple[_Store, Any]:
    store = _Store()
    call = ToolCall(id="c1", name="schedule_create", arguments=arguments)
    asking = _chat(store)
    tool = await _offered(asking, node)
    assert tool is not None
    asked = await run_request(asking, [tool], call, _DONE)
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL and node.requests == []
    approval_id = blocked["approval"]["approvalId"]

    answer = ToolApprovalAnswer(approval_id=approval_id, decision=decision)  # type: ignore[arg-type]
    answering = _chat(store, tool_approval=answer)
    tool = await _offered(answering, node)
    assert tool is not None
    answered = await run_request(
        answering, [tool], _DONE, history=_stopped(call), query=f"Tool approval: {decision}",
    )
    return store, answered


async def test_sch02_the_approved_call_posts_the_chats_schedule_with_the_requests_own_credential() -> None:
    arguments = {**_once(), "notify": ["in_app"]}
    node = _Node(201, _view(arguments))
    _store, answered = await _ask_then_answer(node, arguments)

    (request,) = node.requests
    assert request.url.path == SCHEDULES_API_PATH and request.method == "POST"
    assert request.headers["authorization"] == _AUTH
    assert json.loads(request.content) == {
        "name": arguments["name"],
        "agentKey": "agent-1",
        "input": {"query": arguments["ask"], "chatMode": "quick", "modelKey": "mk-1", "modelName": "gpt-x"},
        "trigger": {"kind": "once", "at": arguments["when"]["at"], "timeZone": "America/Los_Angeles"},
        "notify": ["in_app"],
        # ADR-SCH-03 D3: the chat it was made in, so "that one" can be found from it.
        "fromConversation": {"conversationId": "conv-1"},
    }
    result = _result_of(answered.sent[0])
    assert not result.is_error and "Scheduled “Joke later”: once, on" in str(result.content)


async def test_sch02_a_declined_call_posts_nothing() -> None:
    node = _Node(201, _view(_once()))
    await _ask_then_answer(node, _once(), "deny")
    assert node.requests == []


@pytest.mark.parametrize(
    ("node", "says"),
    [
        (_Node(400, {"error": {"code": "SCHEDULE_LIMIT", "message": "You have too many schedules."}}),
         "Not scheduled: You have too many schedules. Do not retry the same call"),
        (_Node(503, {"error": {"code": "UNAVAILABLE"}}), "may not have been set up"),
        (_Node(down=True), "may not have been set up"),
    ],
)
async def test_sch02_a_refusal_or_an_outage_is_told_plainly(node: _Node, says: str) -> None:
    _store, answered = await _ask_then_answer(node, _once())
    result = _result_of(answered.sent[0])
    assert result.is_error and says in str(result.content)


async def test_sch02_a_one_off_approved_after_its_time_is_still_sent() -> None:
    tool = ClassicScheduleCreateTool(_chat(), SchedulesApi(None, transport=(node := _Node(201, _view(_once()))).transport()))
    late = {**_once(), "when": {"kind": "once", "at": _in(-1), "timeZone": "UTC"}}
    output = await tool.execute(**late)
    assert output.success and len(node.requests) == 1


async def test_sch02_resume_finds_the_platform_target() -> None:
    context = _chat()
    tool = await _offered(context)
    pending = approvals.PendingCall(
        approval_id="a1", org_id="org-1", user_id="user-1", conversation_id="conv-1", source="platform",
        instance_id="schedules", server_name="PipesHub", tool_name="schedule_create", created_at=0, expires_at=1,
    )
    found = approvals._target_for(context, pending)  # pyright: ignore[reportPrivateUsage]
    assert found is not None and found.tool is tool


# --- D4: a scheduled turn's ask waits ---------------------------------------------------------------------------------


def test_sch02_the_firing_marks_read_only_when_both_are_there() -> None:
    at = "2026-11-27T18:00:00.000Z"
    assert scheduled_turn({"x-pipeshub-schedule-firing": "fir-1", "x-pipeshub-ask-deadline": at}) == {
        "firingId": "fir-1", "askDeadline": datetime(2026, 11, 27, 18, tzinfo=UTC).timestamp(),
    }
    assert scheduled_turn({"x-pipeshub-schedule-firing": "fir-1"}) is None
    assert scheduled_turn({"x-pipeshub-ask-deadline": at}) is None
    assert scheduled_turn({"x-pipeshub-schedule-firing": "fir 1;", "x-pipeshub-ask-deadline": at}) is None
    assert scheduled_turn({"x-pipeshub-schedule-firing": "fir-1", "x-pipeshub-ask-deadline": "tomorrow"}) is None


def test_sch02_the_marks_reach_the_context_and_the_credential_leaves_the_state() -> None:
    state: dict[str, Any] = {
        "org_id": "org-1", "user_id": "user-1", "schedule_firing": {"firingId": "fir-1", "askDeadline": 1.5e9},
        "node_authorization": _AUTH,
    }
    context = AgentContext.from_chat_state(state)
    assert (context.schedule_firing_id, context.ask_deadline, context.node_authorization()) == ("fir-1", 1.5e9, _AUTH)
    assert "node_authorization" not in context.tool_state
    # Private: no field of the context, which is what a dump or a log of it reads, holds it.
    assert _AUTH not in repr(context.__dict__)


async def _mcp_write(context: AgentContext) -> ToolCallContext:
    """A write on an MCP server: Ask by default, so it shows what an unwatched turn does with an Ask."""
    from tests.unit.agents.agent_loop.test_tool_approvals import _adapter

    adapter = _adapter(read_only=False)
    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
    ctx = ToolCallContext(tool_path=adapter.path, tool_input={"title": "Bug"})

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


async def test_sch02_a_scheduled_turn_parks_its_ask_until_the_deadline() -> None:
    store = _Store()
    deadline = time.time() + 3 * 3600
    context = _context(store, client_name=None, chat_streaming=False, schedule_firing_id="fir-1", ask_deadline=deadline)
    ctx = await _mcp_write(context)

    assert ctx.decision == PreDecision.ASK and ctx.pending_approval is not None
    (saved,) = store.pending()
    assert saved["expiresAt"] == pytest.approx(deadline)
    assert ctx.pending_approval.details["expiresAt"] == int(deadline * 1000)
    (ttl,) = [t for k, t in store.ttls.items() if "/tool-approvals/pending/" in k]
    assert ttl is not None and ttl > 3 * 3600
    assert context.tool_state[approvals.PENDING_MESSAGE].startswith(approvals.WAITING_PREFIX)
    assert context.tool_state["_tool_approval_turn_ttl"] > 3 * 3600


async def test_sch02_a_parked_ask_waits_no_longer_than_the_longest_firing() -> None:
    store = _Store()
    context = _context(store, client_name=None, schedule_firing_id="fir-1", ask_deadline=time.time() + 30 * 86400)
    await _mcp_write(context)
    (saved,) = store.pending()
    assert saved["expiresAt"] <= time.time() + approvals.SCHEDULED_ASK_MAX_SECONDS + 1


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"schedule_firing_id": "fir-1"},
        {"ask_deadline": time.time() + 3600},
        {"schedule_firing_id": "fir-1", "ask_deadline": time.time() - 1},
        {"schedule_firing_id": "fir-1", "ask_deadline": time.time() + 3600, "conversation_id": None},
        {"schedule_firing_id": "fir-1", "ask_deadline": time.time() + 3600, "is_service_account": True},
    ],
)
async def test_sch02_an_unwatched_turn_without_both_marks_still_blocks(overrides: dict[str, Any]) -> None:
    store = _Store()
    ctx = await _mcp_write(_context(store, client_name="api", **overrides))
    assert ctx.decision == PreDecision.DENY and "approve it in the PipesHub app" in (ctx.decision_reason or "")
    assert store.pending() == []


async def test_sch02_the_marks_grant_nothing() -> None:
    store = _Store()
    marked = _context(store, client_name=None, schedule_firing_id="fir-1", ask_deadline=time.time() + 3600)
    ctx = await _mcp_write(marked)
    # Asked, never allowed: the call runs only on the person's own answer.
    assert ctx.decision == PreDecision.ASK
    # A Block stays a Block.
    await approvals.save_own_rules(store, approvals.ToolRules(tools={"create_issue": "block"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
    blocked = await _mcp_write(_context(store, client_name=None, schedule_firing_id="fir-1", ask_deadline=time.time() + 3600))
    assert blocked.decision == PreDecision.DENY
    # Nor can a marked request answer a card: only a watched one resumes.
    answer = ToolApprovalAnswer(approval_id="a1", decision="allow")
    unwatched = _context(store, client_name=None, schedule_firing_id="fir-1", ask_deadline=time.time() + 3600, tool_approval=answer)
    assert approvals.begin_resume(unwatched, "Tool approval: allow") is False


async def test_sch02_a_watched_turn_keeps_its_short_window() -> None:
    store = _Store()
    before = time.time()
    await _mcp_write(_context(store))
    (saved,) = store.pending()
    assert saved["expiresAt"] <= before + approvals.APPROVAL_TTL_SECONDS + 5
    assert set(store.ttls.values()) == {approvals._RECORD_TTL_SECONDS}  # pyright: ignore[reportPrivateUsage]


async def test_sch02_every_card_says_whose_tool_it_is() -> None:
    ctx = await _mcp_write(_context(_Store()))
    assert ctx.pending_approval is not None
    details = ctx.pending_approval.details
    assert details["source"] == "mcp" and "toolId" not in details
