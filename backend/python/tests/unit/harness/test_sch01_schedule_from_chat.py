"""ADR-SCH-01: a schedule asked for in chat.

``schedule_create`` is offered only with the org's harness and Labs scheduled runs on; it asks the person every time
(no conversation grant or allow rule answers it); a call that cannot make a schedule is refused without asking; the
approved call reaches Node with the interaction the person approved and the arguments as approved.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler, schedule_tools
from app.harness.assembly.trigger import trigger_note
from app.harness.contracts.state import RunTrigger
from app.harness.engines.instructions import engine_time_line
from app.harness.schedules import (
    CARD_LABEL,
    SCHEDULE_CREATE_TOOL,
    SCHEDULE_TOOL_ID,
    NodeSchedules,
    ScheduleAsks,
    ScheduleCreateTool,
    schedule_destination,
)
from app.schedules.arguments import schedule_problem, when_words
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_scheduled_runs_on,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.edits import ApprovedEdit
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    ConversationGrants,
    DefaultAskPolicy,
    destination_key,
)
from harness_core.effects.log import RequestEffectLog
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    LayeredApprovalPolicy,
    parse_rules,
)
from harness_core.effects.runner import EffectRunner
from harness_core.effects.types import (
    EffectCall,
    EffectKey,
    ToolContext,
    ToolEffect,
    ToolExecution,
    allow_all,
    args_digest,
    refresh_policy,
)
from harness_core.interactions.hold import approval_interaction_id
from harness_core.journal.types import JournalUnavailable
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.tools.durable_port import _CALL  # pyright: ignore[reportPrivateUsage]
from harness_core.tools.registry import ToolRegistry
from tests.unit.harness.assembly import world as w
from tests.unit.harness_core.effects.test_ux1_flow_ask import Ledger, event

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from harness_core.effects.runner import HoldDecision

NOW = datetime(2026, 11, 26, 23, 42, tzinfo=UTC)
ONCE: dict[str, Any] = {
    "name": "Joke in five minutes",
    "ask": "Tell me a short, clean joke.",
    "when": {"kind": "once", "at": "2026-11-26T15:47:00-08:00", "timeZone": "America/Los_Angeles"},
}
CRON: dict[str, Any] = {
    "name": "Morning Jira summary",
    "ask": "Summarise my open Jira issues.",
    "when": {"kind": "cron", "cron": "0 9 * * 1-5", "timeZone": "America/Los_Angeles"},
    "notify": ["in_app"],
}
EFFECT = ToolEffect(SCHEDULE_TOOL_ID, "write", False, SinkDescriptor(egress="self", args_leave_boundary=False))
ORG_ALLOWS = LayeredApprovalPolicy((parse_rules({"writes": "allow"}),), ("org",))
ORG_DENIES = LayeredApprovalPolicy((parse_rules({"writes": "deny"}),), ("org",))
_TOP = type("Spec", (), {"run_kind": "interactive"})()
FLAGS_ON = {"featureFlags": {"ENABLE_AGENT_HARNESS": True, "ENABLE_SCHEDULED_RUNS": True}}


def _call(arguments: Mapping[str, Any]) -> ToolCall:
    return ToolCall(id="c1", name=SCHEDULE_CREATE_TOOL, arguments=dict(arguments))


def _asks(base: Any = allow_all) -> ScheduleAsks:  # noqa: ANN401
    return ScheduleAsks(base, now=lambda: NOW)


# --- the route ------------------------------------------------------------------------------------------------------


class _Route:
    """Node's harness state API as one attempt sees it: answers each post with ``answer``."""

    def __init__(self, status: int = 201, body: object = None, *, down: bool = False) -> None:
        self.status, self.body, self.down = status, body, down
        self.posts: list[tuple[str, dict[str, Any], str]] = []

    def log(self, name: str) -> HarnessLog:
        return HarnessLog.named(name)

    async def post(self, suffix: str, *, body: dict[str, Any], operation: str) -> httpx.Response:
        self.posts.append((suffix, body, operation))
        if self.down:
            raise JournalUnavailable("down")
        return httpx.Response(self.status, json=self.body, request=httpx.Request("POST", "http://node/x"))


def _made(created: bool = True) -> dict[str, Any]:
    return {
        "schedule": {
            "id": "sch-1", "name": ONCE["name"], "status": "active", "trigger": ONCE["when"],
            "nextFireAt": "2026-11-26T23:47:00.000Z",
        },
        "created": created,
    }


@pytest.fixture
def ctx() -> Iterator[ToolContext]:
    context = ToolContext("run-1", "org-1", "inv_1", "att-1", "k", interaction_id="ix.approve.inv_1", args_digest="sha256:" + "c" * 64)
    token = _CALL.set(context)
    try:
        yield context
    finally:
        _CALL.reset(token)


# --- offered --------------------------------------------------------------------------------------------------------


class _Config:
    def __init__(self, settings: object, *, broken: bool = False) -> None:
        self.settings, self.broken = settings, broken

    async def get_config(self, key: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        if self.broken:
            raise RuntimeError("store down")
        return self.settings if key == PLATFORM_SETTINGS_KEY else default


@pytest.mark.parametrize(
    ("worker", "flags", "on"),
    [
        ("true", {"ENABLE_AGENT_HARNESS": True, "ENABLE_SCHEDULED_RUNS": True}, True),
        ("true", {"ENABLE_AGENT_HARNESS": True}, False),
        ("true", {"ENABLE_SCHEDULED_RUNS": True}, False),
        ("false", {"ENABLE_AGENT_HARNESS": True, "ENABLE_SCHEDULED_RUNS": True}, False),
    ],
)
async def test_sch01_offered_only_with_the_org_harness_and_scheduled_runs_on(
    monkeypatch: pytest.MonkeyPatch, worker: str, flags: dict[str, bool], on: bool
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", worker)
    config = _Config({"featureFlags": flags})
    # ADR-SCH-02 D1: scheduling is the Labs flag alone; the harness tool also needs the harness, since it is one.
    assert await is_scheduled_runs_on(config) is bool(flags.get("ENABLE_SCHEDULED_RUNS"))  # type: ignore[arg-type]
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None, "spec": _TOP})()
    registry = ToolRegistry()
    offered = await schedule_tools(services, run, claim, registry)  # type: ignore[arg-type]
    assert bool(offered) is on
    assert registry.has(SCHEDULE_CREATE_TOOL) is on
    if offered:
        assert offered[0].tool_id == SCHEDULE_TOOL_ID and all(t.grant is None for t in offered)


async def test_sch01_an_unreadable_setting_offers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    assert await is_scheduled_runs_on(_Config(FLAGS_ON, broken=True)) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("claim_fields", [{"attempt_route": None}, {"trigger_context": {"kind": "once"}}])
async def test_sch01_no_route_or_a_fired_run_is_not_offered(monkeypatch: pytest.MonkeyPatch, claim_fields: dict[str, Any]) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    config = _Config(FLAGS_ON)
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None, "spec": _TOP, **claim_fields})()
    assert await schedule_tools(services, run, claim, ToolRegistry()) == []  # type: ignore[arg-type]


# --- the arguments --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({**ONCE, "name": ""}, "`name`"),
        ({**ONCE, "name": "x" * 121}, "`name`"),
        ({**ONCE, "ask": " "}, "`ask`"),
        ({**ONCE, "ask": "x" * 10_001}, "`ask`"),
        ({**ONCE, "when": {"kind": "weekly", "timeZone": "UTC"}}, "`when.kind`"),
        ({**ONCE, "when": {**ONCE["when"], "at": "2026-11-26T15:47:00"}}, "`when.at`"),
        ({**ONCE, "when": {**ONCE["when"], "at": "tomorrow at 9"}}, "`when.at`"),
        ({**ONCE, "when": {**ONCE["when"], "timeZone": "Pacific Time"}}, "`when.timeZone`"),
        ({**ONCE, "when": {**ONCE["when"], "at": "2026-11-26T23:42:30Z"}}, "at least a minute"),
        ({**ONCE, "when": {**ONCE["when"], "at": "2028-01-01T00:00:00Z"}}, "within a year"),
        ({**ONCE, "when": {**ONCE["when"], "cron": "0 9 * * *"}}, "Extra inputs"),
        ({**CRON, "when": {**CRON["when"], "cron": "* * * * *"}}, "5 minutes"),
        ({**CRON, "when": {**CRON["when"], "cron": "*/2 * * * *"}}, "5 minutes"),
        ({**CRON, "when": {**CRON["when"], "cron": "0 9 * *"}}, "at least 9"),
        ({**CRON, "notify": ["pager"]}, "`notify"),
        ({**ONCE, "repeat": True}, "`repeat`"),
        # Review round 3: lengths count the string as sent, null and repeated channels are refused, at most 3 fractional
        # digits.
        ({**ONCE, "name": "x" + " " * 120}, "`name`"),
        ({**ONCE, "notify": None}, "never null"),
        ({**ONCE, "notify": ["in_app", "in_app"]}, "once"),
        ({**ONCE, "when": {**ONCE["when"], "at": "2026-11-26T23:47:00.1234Z"}}, "`when.at`"),
    ],
)
def test_sch01_a_call_that_cannot_make_a_schedule_runs_unasked_and_is_refused(arguments: dict[str, Any], says: str) -> None:
    problem = schedule_problem(arguments, now=NOW)
    assert problem is not None and says in problem
    # Nobody is asked about it, whatever the layers under the ask say; a deny still refuses.
    assert _asks(LayeredApprovalPolicy((parse_rules({"writes": "ask"}),), ("org",)))(_call(arguments), EFFECT) == "allow"
    assert _asks(ORG_DENIES)(_call(arguments), EFFECT) == "deny"


@pytest.mark.parametrize("arguments", [ONCE, CRON, {**CRON, "when": {**CRON["when"], "cron": "*/5 * * * *"}}])
def test_sch01_a_schedule_that_can_be_made_is_valid(arguments: dict[str, Any]) -> None:
    assert schedule_problem(arguments, now=NOW) is None


async def test_sch01_the_tool_refuses_a_bad_call_without_reaching_node(ctx: ToolContext) -> None:
    route = _Route()
    out = await ScheduleCreateTool(NodeSchedules(route)).execute(**{**ONCE, "when": {"kind": "once"}})  # type: ignore[arg-type]
    assert out.success is False and out.error_code == "invalid_call" and "Nothing was scheduled" in str(out.error)
    assert route.posts == []


# --- the ask --------------------------------------------------------------------------------------------------------


def _granted_for(call: ToolCall) -> ConversationGrants:
    """A person's "allow for this conversation" for this very schedule (its tool and destination)."""
    where = {"label": CARD_LABEL, "key": schedule_destination(call).key}
    grants = ConversationGrants()
    grants.apply([
        event(
            "interaction.explained", "worker", interactionId="ix.approve.k1", rule=FLOW_RULE, toolId=SCHEDULE_TOOL_ID,
            sensitivity="private", sources=[], moreSources=0, destination=where, conversationGrant=True,
        ),
        event("interaction.resolved", "node", interactionId="ix.approve.k1", decision="approved", by="p_u", channel="web"),
        event("interaction.granted", "node", interactionId="ix.approve.k1", scope="conversation"),
    ])
    return grants


@pytest.mark.parametrize("arguments", [ONCE, CRON])
def test_sch01_the_loop_chain_asks_even_when_an_org_rule_allows_writes(arguments: dict[str, Any]) -> None:
    call = _call(arguments)
    policy = _asks(ORG_ALLOWS)
    assert ORG_ALLOWS(call, EFFECT) == "allow"
    assert policy(call, EFFECT) == "ask"
    assert policy.grant_answers(call, EFFECT) is False
    reason = policy.ask_reason(call, EFFECT)
    assert reason is not None
    assert reason["conversationGrant"] is False
    assert reason["toolId"] == SCHEDULE_TOOL_ID
    assert reason["destination"] == {"label": CARD_LABEL, "key": schedule_destination(call).key}


def test_sch01_the_engine_chain_asks_even_with_a_conversation_grant_for_this_schedule() -> None:
    call = _call(ONCE)
    where = schedule_destination(call)
    grants = _granted_for(call)
    engine = DefaultAskPolicy(
        allow_all, ENGINE_APPROVALS, ENGINE_WRITES_RULE, Ledger(frozenset()), "r",  # type: ignore[arg-type]
        destination=lambda c, e: where, grants=grants,
    )
    # The grant answers the engine's own write default; this layer still asks, and offers no grant.
    assert grants.allows(SCHEDULE_TOOL_ID, where.key)
    assert engine(call, EFFECT) == "allow"
    policy = _asks(engine)
    assert policy(call, EFFECT) == "ask"
    reason = policy.ask_reason(call, EFFECT)
    assert reason is not None and reason["conversationGrant"] is False
    assert reason["destination"] == {"label": CARD_LABEL, "key": where.key}


def test_sch01_other_tools_pass_through_unchanged() -> None:
    other = ToolEffect("jira@inst/create_issue", "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
    call = ToolCall(id="c2", name="jira__create_issue", arguments={"title": "T"})
    assert _asks(ORG_ALLOWS)(call, other) == "allow"
    assert _asks(ORG_DENIES)(call, other) == "deny"


def test_sch01_the_destination_names_the_schedule_not_a_place() -> None:
    a, b = schedule_destination(_call(ONCE)), schedule_destination(_call(CRON))
    assert a.label == b.label == CARD_LABEL
    assert a.key != b.key
    assert a.key != destination_key(SCHEDULE_TOOL_ID, CARD_LABEL)


async def test_sch01_a_loop_run_offers_the_tool_and_asks_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    with w.deployment():
        services = w.Services()
        services.config.put(PLATFORM_SETTINGS_KEY, FLAGS_ON)
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
        assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
        claim = dataclasses.replace(w.claim(w.pinned_spec(tool_ids=[])), attempt_route=_Route())
        runtime = await w.first_use(await assembler.build(claim))
        try:
            await refresh_policy(runtime.approval)
            effect = runtime.tools.describe(SCHEDULE_CREATE_TOOL)
            assert effect is not None and effect.tool_id == SCHEDULE_TOOL_ID
            assert (effect.effect, effect.idempotent) == ("write", False)
            assert runtime.approval(_call(CRON), effect) == "ask"
            # A call the tool refuses is never asked about.
            bad = {**CRON, "when": {**CRON["when"], "timeZone": "Pacific Time"}}
            assert runtime.approval(_call(bad), effect) == "allow"
        finally:
            if runtime.close is not None:
                await runtime.close()


async def test_sch01_a_loop_run_with_scheduled_runs_off_has_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    with w.deployment():
        services = w.Services()
        services.config.put(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_AGENT_HARNESS": True}})
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
        assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
        claim = dataclasses.replace(w.claim(w.pinned_spec(tool_ids=[])), attempt_route=_Route())
        runtime = await w.first_use(await assembler.build(claim))
        try:
            assert runtime.tools.describe(SCHEDULE_CREATE_TOOL) is None
        finally:
            if runtime.close is not None:
                await runtime.close()


# --- the approved call ----------------------------------------------------------------------------------------------


async def test_sch01_an_approved_call_posts_its_interaction_and_arguments_as_called(ctx: ToolContext) -> None:
    route = _Route(201, _made())
    out = await ScheduleCreateTool(NodeSchedules(route), timezone="America/Los_Angeles").execute(**ONCE)
    assert out.success is True
    [(suffix, body, operation)] = route.posts
    assert (suffix, operation) == ("/schedules", "schedule.create")
    assert body == {
        "interactionId": "ix.approve.inv_1", "toolId": SCHEDULE_TOOL_ID, "callArgsDigest": "sha256:" + "c" * 64,
        "arguments": ONCE,
    }
    result = out.data["result"]
    assert result.startswith("Scheduled “Joke in five minutes”: once, on Thursday 26 November 2026 at 3:47 PM (America/Los_Angeles).")
    assert "Workspace → Schedules" in result


async def test_sch01_a_repeat_of_the_same_interaction_answers_the_same_schedule(ctx: ToolContext) -> None:
    out = await ScheduleCreateTool(NodeSchedules(_Route(200, _made(created=False)))).execute(**ONCE)
    assert out.success is True and out.data["scheduleId"] == "sch-1"
    assert out.data["result"].startswith("Scheduled “Joke in five minutes”")


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (403, "SCHEDULE_NOT_APPROVED", "The person did not approve this schedule as asked."),
        (409, "SCHEDULE_LIMIT", "You have reached the most schedules you can have."),
    ],
)
async def test_sch01_a_refusal_is_told_plainly_and_not_retried(ctx: ToolContext, status: int, code: str, message: str) -> None:
    route = _Route(status, {"error": {"code": f"AGENT_HARNESS_{code}", "message": message}})
    out = await ScheduleCreateTool(NodeSchedules(route)).execute(**ONCE)
    assert out.success is False
    assert message in str(out.error) and "Do not retry the same call" in str(out.error)


async def test_sch01_node_down_says_it_may_not_be_set_up(ctx: ToolContext) -> None:
    out = await ScheduleCreateTool(NodeSchedules(_Route(down=True))).execute(**ONCE)
    assert out.success is False and out.error_code == "unavailable" and "Workspace → Schedules" in str(out.error)


async def test_sch01_without_a_named_approval_the_engine_interaction_is_recomputed() -> None:
    route = _Route(201, _made())
    token = _CALL.set(ToolContext("run-9", "org-1", "inv_1", "att-1", "k"))
    try:
        _ = await ScheduleCreateTool(NodeSchedules(route)).execute(**ONCE)
    finally:
        _CALL.reset(token)
    expected = approval_interaction_id(EffectKey("run-9", SCHEDULE_TOOL_ID, args_digest(ONCE)))
    assert route.posts[0][1]["interactionId"] == expected
    assert route.posts[0][1]["callArgsDigest"] == args_digest(ONCE)


@dataclasses.dataclass
class _ApprovingHold:
    """A person who approves the call, with ``edit`` in place of its arguments when set."""

    edit: dict[str, Any] | None = None

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        return "approved"

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None:
        return ApprovedEdit(self.edit, "sha256:edit") if self.edit is not None else None

    def interaction_for(self, call: EffectCall) -> str:
        return approval_interaction_id(call.key)


@pytest.mark.parametrize("edit", [None, {**ONCE, "name": "A better joke"}])
async def test_sch01_the_runner_names_the_approval_a_call_runs_on(edit: dict[str, Any] | None) -> None:
    """The approval is the model's call's (its args digest); an edited call runs with the person's arguments, so the
    tool cannot recompute it from what it was given and is told it."""
    seen: list[tuple[str | None, str | None, dict[str, Any]]] = []

    async def execute(call: ToolCall, context: ToolContext) -> ToolExecution:
        seen.append((context.interaction_id, context.args_digest, dict(call.arguments)))
        return ToolExecution(status="ok", content="done")

    tool_call = _call(ONCE)
    effect_call = EffectCall("run-1", "inv_1", tool_call, EFFECT, args_digest(tool_call.arguments))
    runner = EffectRunner(RequestEffectLog(), _asks(), hold=_ApprovingHold(edit))
    _ = await runner.run(effect_call, ToolContext("run-1", "org-1", "inv_1", "att-1", "k"), execute)
    expected = approval_interaction_id(EffectKey("run-1", SCHEDULE_TOOL_ID, args_digest(ONCE)))
    assert seen == [(expected, args_digest(ONCE), edit or ONCE)]


# --- the one-off trigger, the words, the engine's time ---------------------------------------------------------------


def test_sch01_a_one_off_trigger_reads_and_is_told_as_one() -> None:
    once = {"kind": "once", "scheduleId": "sch-1", "firingId": "fir-1", "slotAt": "2026-11-26T23:47:00.000Z"}
    assert RunTrigger.model_validate(once).kind == "once"
    note = trigger_note(once)
    assert note.startswith("This run was started by its one-time schedule for the slot 2026-11-26T23:47:00.000Z.")


def test_sch01_when_words_for_a_repeat_names_the_next_run() -> None:
    words = when_words(CRON["when"], zone="America/Los_Angeles", next_fire_at="2026-11-27T17:00:00.000Z")
    assert words == (
        "repeating on the cron schedule `0 9 * * 1-5` in America/Los_Angeles, next on Friday 27 November 2026 at "
        "9:00 AM (America/Los_Angeles)"
    )


def test_sch01_an_engine_is_told_the_runs_time_and_zone() -> None:
    assert engine_time_line("2026-11-26T23:42:00Z", "America/Los_Angeles") == (
        "The current time is 2026-11-26T23:42:00Z. The user's time zone is America/Los_Angeles."
    )
    assert engine_time_line(None, "UTC") == ""


def test_sch01_its_result_carries_no_record_content() -> None:
    from app.harness.labels import tool_kind

    assert tool_kind(SCHEDULE_CREATE_TOOL) == "none"


# What Node's ``assertCron`` (cron-parser 4.9.0, five fields, 50 slots at least five minutes apart) answered for each
# expression: ``True`` it makes the schedule, ``False`` it refuses it. Recorded from Node; the parity test below asks
# it again when the Node workspace is installed.
NODE_CRON_VERDICTS: dict[str, bool] = {
    "0 9 * * 1-5": True, "0 9 * * MON-FRI": True, "0 9 * JAN,jul *": True, "*/5 * * * *": True, "0 9 ? * *": True,
    "0 9 L * *": True, "0 9 * * 5L": True, "0 9 * * 1#2": True, "0 9 * * 7": True, "0/15 * * * *": True,
    "10/70 * * * *": True, "0 0,23 * * *": True, "59 23 * * *": True, "0 9 1-31/2 * *": True, "0 9 * * sun": True,
    "0 9 * * 1,3-5/2": True, "0 9 * * */2": True, "0 9 31 1,4 *": True, "0 9 29 2 *": True, "0 9 1-31 2 *": True,
    "0 9 */31 2 *": True, "0 9 * * MON-FRI/2": True, "0 9 * * 0-7": True, "*/60 * * * *": True, "0 9 L 2 *": True,
    "0 9 * jan-mar/2 *": True, "0 9 1,L * *": True, "0 9 ?/2 * *": True, "0 9 * * 1,?": True, "0 9 ? * ?": True,
    "00 09 * * *": True, "0 9 * * 5L,1": True, "0 9 15 * 5L": True, "0 9 1 1 *": True,
    "0 99 * * *": False, "*/4 * * * *": False, "0 9 * * 8": False, "5-1 * * * *": False, "0 9 31 2 *": False,
    "0 9 30 2 *": False, "*/0 * * * *": False, "0,3 * * * *": False, "0 9 * 13 *": False, "0 9 0 * *": False,
    "0 24 * * *": False, "60 * * * *": False, "a b c d e": False, "0 9 W * *": False, "0 9 15W * *": False,
    "1-5/0 * * * *": False, "0-4 * * * *": False, "* 9 * * *": False, "H 9 * * *": False, "0 9 31 4 *": False,
    "0 9 31 2,4 *": False, "0 9 30,31 2 *": False, "0 9 30-31 2 *": False, "0 9 31/1 2 *": False,
    "0 9 L-2 * *": False, "0 9 LW * *": False, "0 9 * * L": False, "0 9 * * 1#6": False, "0 9 * * 1#0": False,
    "0 9 * * 8#1": False, "0 9 * FEB-JAN *": False, "+5 9 * * *": False, "0 9 * * SUN,MON#1": False,
    "0,1-3/2 9 * * *": False, "1-2,4 9 * * *": False, "0 9 * * 7-0": False, "0 9 * * mon-sun": False,
    "0 9 * * fri-mon": False, "0 9 * *": False, "@daily": False,
}


def _cron_call(cron: str) -> dict[str, Any]:
    return {**CRON, "when": {**CRON["when"], "cron": cron, "timeZone": "UTC"}}


@pytest.mark.parametrize(("cron", "node_accepts"), sorted(NODE_CRON_VERDICTS.items()))
def test_sch01_a_cron_is_refused_here_exactly_when_node_refuses_it(cron: str, node_accepts: bool) -> None:
    problem = schedule_problem(_cron_call(cron), now=NOW)
    assert (problem is None) is node_accepts, problem
    # A cron Node refuses never reaches the card; one it accepts is asked about.
    assert _asks()(_call(_cron_call(cron)), EFFECT) == ("ask" if node_accepts else "allow")


def test_sch01_the_recorded_verdicts_are_nodes() -> None:
    import json
    import shutil
    import subprocess
    from pathlib import Path

    apps = Path(__file__).resolve().parents[4] / "nodejs/apps"
    if shutil.which("node") is None or not (apps / "node_modules/cron-parser").is_dir():
        pytest.skip("the Node workspace is not installed (npm ci)")
    script = (
        "const {parseExpression}=require(process.argv[1]+'/node_modules/cron-parser');"
        "const out={};for(const c of JSON.parse(process.argv[2])){let ok=c.trim().split(/\\s+/).length===5,s=[];"
        "if(ok){try{const it=parseExpression(c,{currentDate:new Date(Date.UTC(2026,10,27,12)),tz:'UTC'});"
        "for(let i=0;i<50;i++){try{s.push(it.next().getTime())}catch{break}}}catch{ok=false}}"
        "ok=ok&&s.length>0&&s.every((t,i)=>i===0||t-s[i-1]>=300000);out[c]=ok}console.log(JSON.stringify(out))"
    )
    answered = subprocess.run(
        ["node", "-e", script, str(apps), json.dumps(list(NODE_CRON_VERDICTS))],
        capture_output=True, text=True, check=True, timeout=20,
    )
    assert json.loads(answered.stdout) == NODE_CRON_VERDICTS


async def test_sch01_a_code_task_engine_child_is_not_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    config = _Config(FLAGS_ON)
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    spec = type("Spec", (), {"run_kind": "child"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None, "spec": spec})()
    registry = ToolRegistry()
    assert await schedule_tools(services, run, claim, registry) == []  # type: ignore[arg-type]
    assert not registry.has(SCHEDULE_CREATE_TOOL)


VECTORS = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/schedule-create-arguments.json").read_text(
        encoding="utf-8"
    )
)["vectors"]
# Schema-valid vectors the worker still refuses before the card, by name, with the words of the code-only rule that
# refuses each (calendar date, lead time, zone, cron meaning). The contract's vectors are all real zones, crons and
# dates a few minutes after NOW, so none is: every vector the schema accepts reaches the card.
CODE_ONLY_REFUSALS: dict[str, str] = {}


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_sch01_every_contract_vector_gets_the_contracts_verdict_before_the_card(vector: dict[str, Any]) -> None:
    arguments = vector["arguments"]
    sent = json.loads(json.dumps(arguments))
    problem = schedule_problem(arguments)
    expected = vector["valid"] and vector["name"] not in CODE_ONLY_REFUSALS
    assert (problem is None) is expected, problem
    if vector["name"] in CODE_ONLY_REFUSALS:
        assert problem is not None and CODE_ONLY_REFUSALS[vector["name"]] in problem
    # Checking never alters what the person approves.
    assert arguments == sent
    # Against the time the vectors were written for (NOW): a schema-valid one is asked about, any other never.
    assert (schedule_problem(arguments, now=NOW) is None) is expected
    assert _asks()(_call(arguments), EFFECT) == ("ask" if expected else "allow")


@pytest.mark.parametrize(
    ("when", "says"),
    [
        ({"kind": "once", "at": "2026-02-30T09:00:00Z", "timeZone": "UTC"}, "calendar"),
        ({"kind": "once", "at": "2026-11-26T23:42:30Z", "timeZone": "UTC"}, "at least a minute"),
        ({"kind": "once", "at": "2028-01-01T00:00:00Z", "timeZone": "UTC"}, "within a year"),
        ({"kind": "once", "at": "2026-11-27T09:00:00Z", "timeZone": "Pacific Time"}, "IANA"),
        ({"kind": "cron", "cron": "0 99 * * *", "timeZone": "UTC"}, "outside"),
    ],
)
def test_sch01_a_schema_valid_call_is_refused_only_for_the_code_only_rules(when: dict[str, Any], says: str) -> None:
    arguments = {"name": "Joke", "ask": "Tell me a joke.", "when": when}
    problem = schedule_problem(arguments, now=NOW)
    assert problem is not None and says in problem
    assert _asks()(_call(arguments), EFFECT) == "allow"


def test_sch01_the_model_sees_the_contracts_schema() -> None:
    schema = ScheduleCreateTool(NodeSchedules(_Route())).to_schema().input_schema
    assert schema["additionalProperties"] is False and schema["required"] == ["name", "ask", "when"]
    assert schema["properties"]["name"] == {
        "description": "The schedule's name; not only spaces.", "maxLength": 120, "minLength": 1, "pattern": "\\S",
        "type": "string",
    }
    notify = schema["properties"]["notify"]
    assert notify["type"] == "array" and "anyOf" not in notify and "$defs" not in schema


# What Node's ``parseInstant`` (``domain/schedules.ts``) gives each ``at``: the epoch ms, or None for its NaN. The
# parity test below runs Node's own function on them when Node is installed.
NODE_INSTANTS: dict[str, int | None] = {
    "2026-11-27T01:47:00+00:60": None,
    "2026-11-27T01:47:00+00:59": 1_795_740_480_000,
    "2026-11-27T01:47:00+14:59": 1_795_690_080_000,
    "2026-11-27T01:47:00+15:00": None,
    "2026-11-27T01:47:00-14:00": 1_795_794_420_000,
    "2026-02-30T09:00:00Z": None,
    "2026-04-31T09:00:00Z": None,
    "2026-11-27T24:00:00Z": None,
    "2026-11-27T23:60:00Z": None,
    "2026-11-27T23:59:60Z": None,
    "2027-02-29T09:00:00Z": None,
    "2028-02-29T09:00:00Z": 1_835_427_600_000,
    "2026-11-27T01:47Z": 1_795_744_020_000,
    "2026-11-27T01:47:00.123Z": 1_795_744_020_123,
    "2026-11-27T01:47:00.5-08:00": 1_795_772_820_500,
    "2026-11-27T01:47:00": None,
    "2026-11-27T01:47:00+0100": None,
    "２026-11-27T01:47:00Z": None,
}


@pytest.mark.parametrize(("at", "epoch_ms"), sorted(NODE_INSTANTS.items()))
def test_sch01_an_instant_reads_as_nodes_parse_instant(at: str, epoch_ms: int | None) -> None:
    from app.schedules.arguments import parse_instant

    read = parse_instant(at)
    assert (round(read.timestamp() * 1000) if read is not None else None) == epoch_ms
    arguments = {"name": "Joke", "ask": "Tell me a joke.", "when": {"kind": "once", "at": at, "timeZone": "UTC"}}
    sent = json.loads(json.dumps(arguments))
    if epoch_ms is None:
        # Refused before the card, whatever the schema's pattern let through.
        assert schedule_problem(arguments) is not None
        assert _asks()(_call(arguments), EFFECT) == "allow"
    assert arguments == sent


def test_sch01_the_recorded_instants_are_nodes() -> None:
    import re
    import shutil
    import subprocess

    source = (Path(__file__).resolve().parents[4] / "nodejs/apps/src/modules/agent-harness/domain/schedules.ts")
    if shutil.which("node") is None or not source.is_file():
        pytest.skip("Node is not on PATH")
    text = source.read_text(encoding="utf-8")
    pattern = re.search(r"const INSTANT =\s*(/.*?/);", text, re.S)
    function = re.search(r"function parseInstant\(.*?\n}\n", text, re.S)
    assert pattern is not None and function is not None, "parseInstant moved: update this test"
    js = re.sub(r"\)\s*:\s*number\s*=>", ") =>", function.group(0))
    js = js.replace("(at: string): number", "(at)").replace("(group: number)", "(group)")
    script = (
        f"const INSTANT = {pattern.group(1)};\n{js}\n"
        "const out = {}; for (const at of JSON.parse(process.argv[1])) { const v = parseInstant(at);"
        " out[at] = Number.isFinite(v) ? v : null; } console.log(JSON.stringify(out));"
    )
    answered = subprocess.run(
        ["node", "-e", script, json.dumps(list(NODE_INSTANTS))], capture_output=True, text=True, check=True, timeout=20
    )
    assert json.loads(answered.stdout) == NODE_INSTANTS


@pytest.mark.parametrize("kind", [[], {}, 5, None])
def test_sch01_a_kind_that_is_not_a_string_is_refused_without_a_card(kind: object) -> None:
    arguments = {**ONCE, "when": {**ONCE["when"], "kind": kind}}
    problem = schedule_problem(arguments, now=NOW)
    assert problem is not None and "`when" in problem
    assert _asks()(_call(arguments), EFFECT) == "allow"
