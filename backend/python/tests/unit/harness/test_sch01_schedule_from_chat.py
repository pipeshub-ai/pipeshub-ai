"""ADR-SCH-01: a schedule asked for in chat.

``schedule_create`` is offered only with the org's harness and Labs scheduled runs on; it asks the person every time
(no conversation grant or allow rule answers it); a call that cannot make a schedule is refused without asking; the
approved call reaches Node with the interaction the person approved and the arguments as approved.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler, schedule_tool
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
    schedule_problem,
    when_words,
)
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
    assert await is_scheduled_runs_on(config) is on  # type: ignore[arg-type]
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None})()
    registry = ToolRegistry()
    offered = await schedule_tool(services, run, claim, registry)  # type: ignore[arg-type]
    assert (offered is not None) is on
    assert registry.has(SCHEDULE_CREATE_TOOL) is on
    if offered is not None:
        assert offered.tool_id == SCHEDULE_TOOL_ID and offered.grant is None


async def test_sch01_an_unreadable_setting_offers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    assert await is_scheduled_runs_on(_Config(FLAGS_ON, broken=True)) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("claim_fields", [{"attempt_route": None}, {"trigger_context": {"kind": "once"}}])
async def test_sch01_no_route_or_a_fired_run_is_not_offered(monkeypatch: pytest.MonkeyPatch, claim_fields: dict[str, Any]) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    config = _Config(FLAGS_ON)
    services = type("S", (), {"config_service": lambda self: config})()
    run = type("R", (), {"sub_agent": None, "timezone": "UTC"})()
    claim = type("C", (), {"attempt_route": _Route(), "trigger_context": None, **claim_fields})()
    assert await schedule_tool(services, run, claim, ToolRegistry()) is None  # type: ignore[arg-type]


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
        ({**ONCE, "when": {**ONCE["when"], "cron": "0 9 * * *"}}, "takes only"),
        ({**CRON, "when": {**CRON["when"], "cron": "* * * * *"}}, "5 minutes"),
        ({**CRON, "when": {**CRON["when"], "cron": "*/2 * * * *"}}, "5 minutes"),
        ({**CRON, "when": {**CRON["when"], "cron": "0 9 * *"}}, "five-field"),
        ({**CRON, "notify": ["pager"]}, "`notify`"),
        ({**ONCE, "repeat": True}, "Unknown argument"),
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
