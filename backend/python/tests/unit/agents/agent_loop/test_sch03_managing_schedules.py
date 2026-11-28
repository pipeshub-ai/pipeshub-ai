"""ADR-SCH-03: managing schedules from chat.

``schedule_find`` reads the person's own schedules and asks nothing; ``schedule_update`` and ``schedule_delete`` take
an id and the revision read, ask every time with the schedule's current values on the card, refuse a call that
cannot succeed (or a schedule changed since it was read) without a card, and answer what is now true. Classic chat
writes through the public routes with the request's own credential; the harness through the journal-bound internal
routes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import schedule_tool
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.schedule_tool import SchedulesApi, register_schedule_tool
from app.agents.agent_loop.tool_approvals import ToolApprovalAnswer
from app.harness import schedules as harness
from app.schedules import arguments as shared
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.policy import LayeredApprovalPolicy, parse_rules
from harness_core.effects.types import ToolContext, ToolEffect, allow_all
from harness_core.journal.types import JournalUnavailable
from harness_core.logs import HarnessLog
from harness_core.tools.durable_port import _CALL  # pyright: ignore[reportPrivateUsage]
from tests.unit.agents.agent_loop.test_sch02_schedules_without_harness import (
    _AUTH,
    _chat,
)
from tests.unit.agents.agent_loop.test_tool_approvals import (
    _result_of,
    _Store,
    run_request,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

VECTORS = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/schedule-manage-arguments.json").read_text(
        encoding="utf-8"
    )
)["vectors"]
VIEW: dict[str, Any] = {
    "id": "sch-1",
    "name": "Joke in two minutes",
    "status": "active",
    "revision": 3,
    "trigger": {"kind": "once", "at": "2026-11-27T23:47:00.000Z", "timeZone": "America/Los_Angeles"},
    "nextFireAt": "2026-11-27T23:47:00.000Z",
    "notify": ["in_app"],
    "input": {"query": "Tell me a short joke."},
    "origin": {"conversationId": "conv-1"},
}
CARD = {
    "id": "sch-1", "name": "Joke in two minutes", "ask": "Tell me a short joke.",
    "when": {"kind": "once", "at": "2026-11-27T23:47:00.000Z", "timeZone": "America/Los_Angeles"},
    "notify": ["in_app"], "status": "active", "revision": 3,
}


# --- one contract, both paths -------------------------------------------------------------------------------------


_CHECKS = {"update": shared.update_problem, "delete": shared.delete_problem, "find": shared.find_problem}


@pytest.mark.parametrize("vector", VECTORS, ids=[f"{v['tool']}:{v['name']}" for v in VECTORS])
def test_sch03_every_vector_gets_its_verdict_before_any_card(vector: dict[str, Any]) -> None:
    problem = _CHECKS[vector["tool"]](vector["arguments"])
    assert (problem is None) is vector["valid"], problem


def test_sch03_both_paths_show_the_same_tools() -> None:
    tools = {t.name: t for t in harness.schedule_tools(harness.NodeSchedules(_Route()), timezone=None)}  # type: ignore[arg-type]
    classic = {
        "schedule_find": schedule_tool.ClassicScheduleFindTool(_chat(), SchedulesApi(None)),
        "schedule_update": schedule_tool.ClassicScheduleUpdateTool(_chat(), SchedulesApi(None)),
        "schedule_delete": schedule_tool.ClassicScheduleDeleteTool(_chat(), SchedulesApi(None)),
    }
    for name, tool in classic.items():
        assert tools[name].raw_input_schema == tool.raw_input_schema
        assert tools[name].description == tool.description
    update = shared.contract_schema("update")
    assert update["required"] == ["id", "revision"] and "$defs" not in update
    assert {"type": "null"} not in update["properties"]["when"]["anyOf"]


@pytest.mark.parametrize("description", [shared.UPDATE_DESCRIPTION, shared.DELETE_DESCRIPTION])
def test_sch03_a_write_is_told_to_find_first_and_never_act_on_a_name(description: str) -> None:
    assert "schedule_find" in description and "never act on a name" in description and "ask the user which" in description


def test_sch03_find_answers_what_the_model_needs_to_name_and_change_one() -> None:
    entry = shared.found_entry({**VIEW, "input": {"query": "x" * 500}}, zone="UTC", conversation_id="conv-1")
    assert (entry["id"], entry["revision"], entry["madeHere"], entry["status"]) == ("sch-1", 3, True, "active")
    assert len(entry["asks"]) == 200 and entry["asks"].endswith("…")
    assert entry["when"].startswith("once, on Friday 27 November 2026")


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"id": "sch-1", "revision": 3, "ask": "Tell me a short animal joke."}, "it now asks “Tell me a short animal joke.”"),
        ({"id": "sch-1", "revision": 3, "action": "pause"}, "Paused “Joke in two minutes”; it won't run until it is resumed."),
        ({"id": "sch-1", "revision": 3, "action": "resume"}, "Resumed “Joke in two minutes”: it runs once"),
        ({"id": "sch-1", "revision": 3, "action": "run_now"}, "is running now"),
    ],
)
def test_sch03_an_answer_says_what_is_now_true(arguments: dict[str, Any], says: str) -> None:
    view = {**VIEW, "input": {"query": arguments.get("ask", VIEW["input"]["query"])}}
    assert says in shared.changed_text(view, arguments, zone="UTC")


def test_sch03_a_skip_names_the_run_it_skips() -> None:
    text = shared.changed_text({**VIEW, "skipNextAt": "2026-11-28T09:00:00Z"}, {"action": "skip_next"}, zone="UTC")
    assert "will skip its next run on Saturday 28 November 2026 at 9:00 AM" in text


# --- classic chat ---------------------------------------------------------------------------------------------------


class _Node:
    """Node's public schedules API: the schedule ``VIEW``, and what each request was."""

    def __init__(self, *, view: dict[str, Any] | None = None, answers: dict[tuple[str, str], tuple[int, object]] | None = None) -> None:
        self.view = view if view is not None else dict(VIEW)
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            key = (request.method, request.url.path.removeprefix(schedule_tool.SCHEDULES_API_PATH))
            if key in self.answers:
                status, body = self.answers[key]
                return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
            if key == ("GET", "/sch-1"):
                return httpx.Response(200, json=self.view)
            if key == ("GET", ""):
                return httpx.Response(200, json={"schedules": [self.view]})
            if key == ("DELETE", "/sch-1"):
                return httpx.Response(204)
            if key == ("POST", "/sch-1/run"):
                return httpx.Response(202, json={"runId": "r-1"})
            return httpx.Response(200, json=self.view)

        return httpx.MockTransport(handle)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def on(_config: object) -> bool:
        return True

    async def endpoint(_config: object) -> str:
        return "http://node:3000"

    monkeypatch.setattr(schedule_tool, "is_scheduled_runs_on", on)
    monkeypatch.setattr(schedule_tool, "nodejs_endpoint", endpoint)


async def _offered(context: AgentContext, node: _Node) -> ToolRegistry:
    registry = ToolRegistry()
    assert await register_schedule_tool(registry, context, api=SchedulesApi(None, transport=node.transport()))
    return registry


async def _gate(context: AgentContext, name: str, arguments: dict[str, Any]) -> ToolCallContext:
    ctx = ToolCallContext(tool_path=f"/platform/schedules/{name.removeprefix('schedule_')}", tool_input=dict(arguments))

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


async def test_sch03_classic_find_reads_the_persons_own_with_their_credential_and_asks_nothing() -> None:
    node = _Node()
    context = _chat()
    registry = await _offered(context, node)
    assert (await _gate(context, "schedule_find", {"query": "joke"})).decision == PreDecision.ALLOW
    output = await registry.resolve_by_name("schedule_find").execute(query="joke", status="active", madeHere=True)  # type: ignore[union-attr]
    (request,) = node.requests
    assert request.headers["authorization"] == _AUTH
    assert dict(request.url.params) == {
        "limit": "20", "query": "joke", "status": "active", "madeHere": "true", "conversationId": "conv-1",
    }
    (entry,) = output.data["schedules"]
    assert (entry["id"], entry["revision"], entry["madeHere"]) == ("sch-1", 3, True)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("schedule_update", {"id": "sch-1", "revision": 3, "ask": "Tell me a short animal joke."}),
        ("schedule_update", {"id": "sch-1", "revision": 3, "action": "pause"}),
        ("schedule_delete", {"id": "sch-1", "revision": 3}),
    ],
)
async def test_sch03_classic_change_asks_with_the_current_values_and_cannot_be_remembered(
    name: str, arguments: dict[str, Any],
) -> None:
    store, node = _Store(), _Node()
    context = _chat(store)
    await _offered(context, node)
    ctx = await _gate(context, name, arguments)
    assert ctx.decision == PreDecision.ASK and ctx.pending_approval is not None
    details = ctx.pending_approval.details
    assert details["schedule"] == CARD
    assert (details["canRemember"], details["source"], details["toolId"]) == (False, "platform", shared.TOOL_IDS[name])
    assert node.writes() == []


@pytest.mark.parametrize(
    ("node", "says"),
    [
        (_Node(view={**VIEW, "revision": 4}), "changed since it was read"),
        (_Node(answers={("GET", "/sch-1"): (404, {"error": {"code": "SCHEDULE_NOT_FOUND", "message": "Schedule not found"}})}), "no schedule of the person's has that id"),
    ],
)
async def test_sch03_classic_a_changed_or_missing_schedule_is_refused_without_a_card(node: _Node, says: str) -> None:
    store = _Store()
    context = _chat(store)
    await _offered(context, node)
    ctx = await _gate(context, "schedule_update", {"id": "sch-1", "revision": 3, "action": "pause"})
    assert ctx.decision == PreDecision.DENY and says in (ctx.decision_reason or "")
    assert store.pending() == [] and node.writes() == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"id": "sch-1", "revision": 3, "action": "pause", "name": "x"},
        {"id": "sch-1", "revision": 3},
        {"id": "sch-1", "revision": 3, "when": {"kind": "cron", "cron": "* * * * *", "timeZone": "UTC"}},
        {"id": "Joke in two minutes", "revision": 3, "action": "pause"},
    ],
)
async def test_sch03_classic_an_invalid_change_is_refused_without_reading_or_a_card(arguments: dict[str, Any]) -> None:
    store, node = _Store(), _Node()
    context = _chat(store)
    await _offered(context, node)
    ctx = await _gate(context, "schedule_update", arguments)
    assert ctx.decision == PreDecision.DENY and "Nothing was changed." in (ctx.decision_reason or "")
    assert store.pending() == [] and node.requests == []


def _stopped(call: ToolCall) -> list[dict[str, Any]]:
    waiting = f"Waiting for your approval to run {call.name} on PipesHub."
    return [
        {"role": "user_query", "content": "make it an animal joke"},
        {"role": "bot_response", "content": waiting, "tool_results": [{
            "tool_id": call.id, "tool_name": call.name, "args": call.arguments, "result": waiting, "status": "error",
        }]},
    ]


async def _approve(node: _Node, name: str, arguments: dict[str, Any], *, between: dict[str, Any] | None = None) -> str:
    """Asks for the call, then answers its card with Allow in the person's next request; the model's result text."""
    store = _Store()
    call = ToolCall(id="c1", name=name, arguments=arguments)
    done = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})
    asking = _chat(store)
    registry = await _offered(asking, node)
    asked = await run_request(asking, [registry.resolve_by_name(name)], call, done)
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL and node.writes() == []
    if between is not None:
        node.view = {**node.view, **between}
    answer = ToolApprovalAnswer(approval_id=blocked["approval"]["approvalId"], decision="allow")
    answering = _chat(store, tool_approval=answer)
    registry = await _offered(answering, node)
    answered = await run_request(
        answering, [registry.resolve_by_name(name)], done, history=_stopped(call), query="Tool approval: allow",
    )
    return str(_result_of(answered.sent[0]).content)


async def test_sch03_classic_an_approved_change_patches_with_the_revision_and_this_chat() -> None:
    node = _Node()
    node.answers[("PATCH", "/sch-1")] = (200, {**VIEW, "revision": 4, "input": {"query": "Tell me a short animal joke."}})
    # A day on from now: a time already past is refused before any card, so a fixed date would expire.
    at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    when = {"kind": "once", "at": at, "timeZone": "UTC"}
    text = await _approve(node, "schedule_update", {"id": "sch-1", "revision": 3, "ask": "Tell me a short animal joke.", "when": when})
    (patch,) = node.writes()
    assert patch.method == "PATCH" and patch.url.path.endswith("/schedules/sch-1")
    assert patch.headers["authorization"] == _AUTH
    assert json.loads(patch.content) == {
        "revision": 3, "input": {"query": "Tell me a short animal joke."}, "trigger": when,
        "fromConversation": {"conversationId": "conv-1"},
    }
    assert "Changed “Joke in two minutes”: it now asks “Tell me a short animal joke.”" in text


@pytest.mark.parametrize(
    ("action", "path", "body"),
    [
        ("pause", "/sch-1/pause", {"revision": 3, "fromConversation": {"conversationId": "conv-1"}}),
        ("resume", "/sch-1/resume", {"revision": 3, "fromConversation": {"conversationId": "conv-1"}}),
        ("skip_next", "/sch-1/skip", {"revision": 3, "fromConversation": {"conversationId": "conv-1"}}),
        ("run_now", "/sch-1/run", None),
    ],
)
async def test_sch03_classic_an_approved_action_takes_its_own_route(action: str, path: str, body: dict[str, Any] | None) -> None:
    node = _Node()
    await _approve(node, "schedule_update", {"id": "sch-1", "revision": 3, "action": action})
    (write,) = node.writes()
    assert (write.method, write.url.path.removeprefix(schedule_tool.SCHEDULES_API_PATH)) == ("POST", path)
    assert (json.loads(write.content) if write.content else None) == body
    if action == "run_now":
        assert write.headers["idempotency-key"].startswith("chat-")


async def test_sch03_classic_an_approved_delete_names_its_revision() -> None:
    node = _Node()
    text = await _approve(node, "schedule_delete", {"id": "sch-1", "revision": 3})
    (delete,) = node.writes()
    assert delete.method == "DELETE" and dict(delete.url.params) == {"revision": "3"}
    assert "Deleted “Joke in two minutes”; it will not run again." in text


async def test_sch03_classic_a_schedule_changed_after_the_card_is_not_changed() -> None:
    node = _Node()
    text = await _approve(node, "schedule_delete", {"id": "sch-1", "revision": 3}, between={"revision": 5})
    assert node.writes() == [] and "changed since it was read" in text


async def test_sch03_classic_nodes_schedule_changed_is_told_plainly() -> None:
    node = _Node(answers={("POST", "/sch-1/pause"): (409, {"error": {"code": "SCHEDULE_CHANGED", "message": "x"}})})
    text = await _approve(node, "schedule_update", {"id": "sch-1", "revision": 3, "action": "pause"})
    assert "Read it again with schedule_find" in text


# --- the harness ----------------------------------------------------------------------------------------------------


class _Route:
    """Node's harness state API for one attempt: answers by (method, suffix)."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, object]] | None = None, *, down: bool = False) -> None:
        self.answers = answers or {}
        self.down = down
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def log(self, name: str) -> HarnessLog:
        return HarnessLog.named(name)

    def _answer(self, method: str, suffix: str) -> httpx.Response:
        if self.down:
            raise JournalUnavailable("down")
        status, body = self.answers.get((method, suffix), (200, VIEW))
        return httpx.Response(status, json=body, request=httpx.Request(method, "http://node/x"))

    async def get(self, suffix: str, *, params: dict[str, Any] | None = None, operation: str) -> httpx.Response:
        self.calls.append(("GET", suffix, dict(params or {})))
        return self._answer("GET", suffix)

    async def post(self, suffix: str, *, body: dict[str, Any], operation: str) -> httpx.Response:
        self.calls.append(("POST", suffix, body))
        return self._answer("POST", suffix)


@pytest.fixture
def ctx() -> Any:  # noqa: ANN401
    context = ToolContext("run-1", "org-1", "inv_1", "att-1", "k", interaction_id="ix.approve.inv_1", args_digest="sha256:" + "d" * 64)
    token = _CALL.set(context)
    try:
        yield context
    finally:
        _CALL.reset(token)


def _harness_tool(name: str, route: _Route) -> Any:  # noqa: ANN401
    tools = {t.name: t for t in harness.schedule_tools(harness.NodeSchedules(route), timezone="UTC")}  # type: ignore[arg-type]
    return tools[name]


async def test_sch03_harness_find_reads_as_the_runs_person() -> None:
    route = _Route({("GET", "/schedules"): (200, {"schedules": [VIEW]})})
    output = await _harness_tool("schedule_find", route).execute(query="joke", madeHere=False)
    assert route.calls == [("GET", "/schedules", {"query": "joke", "madeHere": "false"})]
    assert output.data["schedules"][0]["id"] == "sch-1"


async def test_sch03_harness_an_approved_change_posts_the_approval_and_the_arguments(ctx: ToolContext) -> None:
    route = _Route({("POST", "/schedules/sch-1/update"): (200, {"schedule": {**VIEW, "revision": 4}, "changed": True})})
    arguments = {"id": "sch-1", "revision": 3, "action": "pause"}
    output = await _harness_tool("schedule_update", route).execute(**arguments)
    assert [c[:2] for c in route.calls] == [("GET", "/schedules/sch-1"), ("POST", "/schedules/sch-1/update")]
    assert route.calls[1][2] == {
        "interactionId": "ix.approve.inv_1", "toolId": shared.SCHEDULE_UPDATE_TOOL_ID,
        "callArgsDigest": "sha256:" + "d" * 64, "arguments": arguments,
    }
    assert output.success and output.data["result"].startswith("Paused “Joke in two minutes”")


async def test_sch03_harness_a_delete_says_what_it_removed(ctx: ToolContext) -> None:
    route = _Route({("POST", "/schedules/sch-1/delete"): (200, {"schedule": None, "changed": True})})
    output = await _harness_tool("schedule_delete", route).execute(id="sch-1", revision=3)
    assert route.calls[1][2]["toolId"] == shared.SCHEDULE_DELETE_TOOL_ID
    assert output.data["result"] == "Deleted “Joke in two minutes”; it will not run again."


@pytest.mark.parametrize(
    ("route", "says"),
    [
        (_Route({("GET", "/schedules/sch-1"): (200, {**VIEW, "revision": 9})}), "changed since it was read"),
        (_Route({("POST", "/schedules/sch-1/update"): (409, {"error": {"code": "SCHEDULE_CHANGED"}})}), "changed since it was read"),
        (_Route({("POST", "/schedules/sch-1/update"): (403, {"error": {"code": "SCHEDULE_NOT_APPROVED", "message": "Not approved."}})}), "Not changed: Not approved."),
        (_Route(down=True), "may or may not have been changed"),
    ],
)
async def test_sch03_harness_a_refused_change_is_told_and_not_retried(ctx: ToolContext, route: _Route, says: str) -> None:
    output = await _harness_tool("schedule_update", route).execute(id="sch-1", revision=3, action="pause")
    assert not output.success and says in (output.error or "")
    if says == "changed since it was read" and route.answers.get(("GET", "/schedules/sch-1")):
        assert [c[0] for c in route.calls] == ["GET"]


_UPDATE = ToolEffect(shared.SCHEDULE_UPDATE_TOOL_ID, "write", False, SinkDescriptor(egress="self", args_leave_boundary=False))
_DELETE = ToolEffect(shared.SCHEDULE_DELETE_TOOL_ID, "destructive", False, SinkDescriptor(egress="self", args_leave_boundary=False))
_FIND = ToolEffect(shared.SCHEDULE_FIND_TOOL_ID, "read", False, SinkDescriptor(egress="self", args_leave_boundary=False))
ORG_ALLOWS = LayeredApprovalPolicy((parse_rules({"writes": "allow"}),), ("org",))


def _call(name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
    from harness_core.kernel.core.messages import ToolCall as KernelCall

    return KernelCall(id="c1", name=name, arguments=arguments)


@pytest.mark.parametrize("effect", [_UPDATE, _DELETE])
async def test_sch03_harness_a_change_asks_with_the_current_values_whatever_the_org_allows(effect: ToolEffect) -> None:
    asks = harness.ScheduleAsks(ORG_ALLOWS, read=harness.NodeSchedules(_Route()).read)  # type: ignore[arg-type]
    call = _call("schedule_update", {"id": "sch-1", "revision": 3, "action": "pause"} if effect is _UPDATE else {"id": "sch-1", "revision": 3})
    await asks.consult(call, effect)
    assert asks(call, effect) == "ask"
    reason = asks.ask_reason(call, effect)
    assert reason is not None and reason["conversationGrant"] is False
    # Read before asking, but not journaled with the reason: `interaction.explained` has no field for it.
    assert asks.current(call) == CARD and "schedule" not in reason
    assert asks.grant_answers(call, effect) is False
    assert reason["destination"]["label"] == shared.CARD_LABELS[effect.tool_id]  # type: ignore[index]


async def test_sch03_harness_a_stale_change_is_not_asked_about() -> None:
    asks = harness.ScheduleAsks(allow_all, read=harness.NodeSchedules(_Route({("GET", "/schedules/sch-1"): (200, {**VIEW, "revision": 4})})).read)  # type: ignore[arg-type]
    call = _call("schedule_update", {"id": "sch-1", "revision": 3, "action": "pause"})
    await asks.consult(call, _UPDATE)
    assert asks(call, _UPDATE) == "allow"


async def test_sch03_harness_find_is_not_asked_about() -> None:
    asks = harness.ScheduleAsks(allow_all)
    assert asks(_call("schedule_find", {}), _FIND) == "allow"


async def test_sch03_harness_offers_the_four_tools_with_their_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly.assembler import schedule_tools
    from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")

    class _Config:
        async def get_config(self, key: str, default: object = None, use_cache: bool = True, **_: object) -> object:
            flags = {"ENABLE_AGENT_HARNESS": True, "ENABLE_SCHEDULED_RUNS": True}
            return {"featureFlags": flags} if key == PLATFORM_SETTINGS_KEY else default

    config = _Config()
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None, "spec": type("Spec", (), {"run_kind": "interactive"})()})()
    registry = ToolRegistry()
    offered = await schedule_tools(services, run, claim, registry)  # type: ignore[arg-type]
    assert {t.name: t.tool_id for t in offered} == shared.TOOL_IDS
    assert harness.schedule_reader(registry) is not None


async def test_sch03_classic_a_delete_node_finds_stale_is_told_as_a_change() -> None:
    node = _Node(answers={("DELETE", "/sch-1"): (409, {"error": {"code": "SCHEDULE_CHANGED", "message": "x"}})})
    text = await _approve(node, "schedule_delete", {"id": "sch-1", "revision": 3})
    assert "Read it again with schedule_find" in text


def test_sch03_a_delegated_child_never_holds_the_schedule_tools() -> None:
    """ADR-SCH-03 D1: the schedules toolset is a person's own chat's; deep mode's spawn pool and the list a spawning call
    names both drop it, while the child keeps its other tools."""
    from app.agents.agent_loop.loops.orchestrator import domain_spec_factory
    from app.schedules.arguments import SCHEDULE_TOOL_NAMES

    pool = ["jira_search", *sorted(SCHEDULE_TOOL_NAMES)]
    factory = domain_spec_factory(provider="scripted", model_name="scripted-model", default_tool_names=pool)
    assert factory("researcher").tool_names == ["jira_search"]
    named = factory("researcher", tool_names=["schedule_create", "jira_search", "schedule_delete"])
    assert named.tool_names == ["jira_search"]
    granted = domain_spec_factory(
        provider="scripted", model_name="scripted-model", default_tool_names=pool,
        grant=lambda names: [*names, "schedule_update"],
    )("researcher")
    assert granted.tool_names == ["jira_search"]


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_sch03_an_instant_its_offset_moves_off_the_calendar_is_no_instant(value: str) -> None:
    assert shared.parse_instant(value) is None
    assert shared.parse_instant("2026-12-01T09:00:00+01:00") == datetime(2026, 12, 1, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", ["² 0 * * *", "0 ٣ * * *", "*/² * * * *", "0 9 * * 1#²"])
def test_sch03_a_cron_with_digits_int_cannot_read_is_a_problem_not_an_error(cron: str) -> None:
    problem = shared.update_problem({"id": "sch-1", "revision": 3, "when": {"kind": "cron", "cron": cron, "timeZone": "UTC"}})
    assert problem is not None and "`when.cron`" in problem


@pytest.mark.parametrize("field", ["name", "ask", "when", "action", "notify"])
def test_sch03_an_explicit_null_for_any_optional_argument_is_refused(field: str) -> None:
    problem = shared.update_problem({"id": "sch-1", "revision": 3, field: None})
    assert problem is not None and f"`{field}`" in problem and "never null" in problem
    # Left out, the same field is fine where the call still changes something.
    assert shared.update_problem({"id": "sch-1", "revision": 3, "name": "Standup"}) is None
