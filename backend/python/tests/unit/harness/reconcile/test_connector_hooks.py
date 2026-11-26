"""CN-1..CN-8: the Jira and Slack reconcile hooks against fakes that refuse what the APIs refuse (P2.9)."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.actions.jira.jira import Jira
from app.agents.actions.slack.slack import USER_LOOKUP_CONCURRENCY, Slack
from app.harness.reconcile import (
    HOOKS,
    JiraIssueLookup,
    SlackMessageLookup,
    build_reconcile_hooks,
)
from app.harness.reconcile.hooks import JIRA_INDEX_LAG_S
from app.sources.client.slack.slack import SlackResponse
from app.utils.env_flags import HARNESS_RECONCILE
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.reconcile import (
    LookupUndecidable,
    TimedReconciler,
    effect_tag,
    reconciling,
    refuse_write_while_reconciling,
    stamped_effect,
)
from harness_core.effects.types import ToolEffect
from harness_core.failpoints import Raise
from harness_core.kernel.durable import ToolExecution
from harness_core.logs import HarnessLog
from harness_core.testing import armed
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.reconcile import (
    LEASE_S,
    ReconcileLookupContract,
    ReconcilerContract,
    unknown_effect,
)
from tests.unit.harness_core.durable.support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext

CHANNEL = "C0123456789"
_LABEL = re.compile(r"^\S{1,255}$")
_JQL = re.compile(r'^labels = "([^"\s]{1,255})"$')


def advancing(clock: ManualClock):  # noqa: ANN201 - a sleep matching ``asyncio.sleep``
    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return sleep


@dataclass
class _HttpResponse:
    status: int
    body: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.body


@dataclass
class FakeJira:
    """``JiraDataSource`` as far as create and search go: labels must be single
    words of at most 255 characters (Jira refuses spaces); search sees an issue
    only ``index_lag_s`` after its create; only the JQL the hook sends parses."""

    clock: ManualClock
    index_lag_s: float = JIRA_INDEX_LAG_S
    issues: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    searches: list[str] = field(default_factory=list[str])
    fail_searches: int = 0

    async def create_issue(self, *, fields: dict[str, Any], **_: Any) -> _HttpResponse:  # noqa: ANN401
        refuse_write_while_reconciling("jira.create_issue")
        labels = fields.get("labels") or []
        bad = [label for label in labels if not isinstance(label, str) or not _LABEL.match(label)]
        if bad:
            return _HttpResponse(400, {"errors": {"labels": "The label contains spaces which is invalid."}})
        number = len(self.issues) + 1
        issue = {"id": str(10000 + number), "key": f"PROJ-{number}", "labels": list(labels), "at": self.clock.now()}
        self.issues.append(issue)
        return _HttpResponse(201, {"id": issue["id"], "key": issue["key"], "self": "https://x/rest/api/3/issue"})

    async def search_and_reconsile_issues_using_jql_post(
        self, *, jql: str | None = None, fields: list[str] | None = None, maxResults: int | None = None, **_: Any  # noqa: ANN401
    ) -> _HttpResponse:
        self.searches.append(jql or "")
        if self.fail_searches > 0:
            self.fail_searches -= 1
            return _HttpResponse(503, {"errorMessages": ["Service unavailable"]})
        match = _JQL.match(jql or "")
        if match is None:
            return _HttpResponse(400, {"errorMessages": ["Error in the JQL Query"]})
        if maxResults is not None and not 0 <= maxResults <= 5000:
            return _HttpResponse(400, {"errorMessages": ["maxResults out of range"]})
        visible = [
            {"id": i["id"], "key": i["key"]}
            for i in self.issues
            if match.group(1) in i["labels"] and (self.clock.now() - i["at"]).total_seconds() >= self.index_lag_s
        ]
        return _HttpResponse(200, {"issues": visible[: maxResults or 50]})


@dataclass
class FakeSlack:
    """``SlackDataSource`` for posting and channel history: metadata must carry an
    ``event_type`` and an object ``event_payload`` (``invalid_metadata_format``);
    history returns metadata only with ``include_all_metadata``; ``limit`` ≤ 1000;
    unknown channels are ``channel_not_found``."""

    clock: ManualClock
    channels: tuple[str, ...] = (CHANNEL,)
    messages: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    posts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    fail_history: int = 0

    async def chat_post_message(self, *, channel: str, text: str | None = None, **kwargs: Any) -> SlackResponse:  # noqa: ANN401
        refuse_write_while_reconciling("slack.chat_postMessage")
        self.posts.append({"channel": channel, "text": text, **kwargs})
        if channel not in self.channels:
            return SlackResponse(success=False, error="channel_not_found")
        metadata = kwargs.get("metadata")
        if metadata is not None and (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("event_type"), str)
            or not isinstance(metadata.get("event_payload"), dict)
        ):
            return SlackResponse(success=False, error="invalid_metadata_format")
        ts = f"{self.clock.now().timestamp():.6f}"
        message: dict[str, Any] = {"type": "message", "text": text, "ts": ts}
        if metadata is not None:
            message["metadata"] = metadata
        self.messages.append({"channel": channel, **message})
        return SlackResponse(success=True, data={"ok": True, "channel": channel, "ts": ts})

    async def conversations_history(
        self, *, channel: str, oldest: str | None = None, limit: int | None = None, **kwargs: Any  # noqa: ANN401
    ) -> SlackResponse:
        if self.fail_history > 0:
            self.fail_history -= 1
            return SlackResponse(success=False, error="ratelimited")
        if channel not in self.channels:
            return SlackResponse(success=False, error="channel_not_found")
        if limit is not None and not 1 <= limit <= 1000:
            return SlackResponse(success=False, error="invalid_limit")
        floor = float(oldest) if oldest is not None else 0.0
        with_metadata = bool(kwargs.get("include_all_metadata"))
        found = []
        for m in reversed(self.messages):
            if m["channel"] != channel or float(m["ts"]) < floor:
                continue
            shown = {k: v for k, v in m.items() if k != "channel" and (with_metadata or k != "metadata")}
            found.append(shown)
        return SlackResponse(success=True, data={"ok": True, "messages": found[: limit or 100]})


def _jira(fake: FakeJira) -> Jira:
    jira = Jira.__new__(Jira)
    jira.client = fake  # type: ignore[assignment]
    jira._site_url = "https://example.atlassian.net"
    jira._field_schema_cache = None
    jira._create_fields_cache = {}
    return jira


def _slack(fake: FakeSlack) -> Slack:
    slack = Slack.__new__(Slack)
    slack.client = fake  # type: ignore[assignment]
    slack._user_cache = {}
    slack._channel_cache = {}
    slack._lookup_sem = asyncio.Semaphore(USER_LOOKUP_CONCURRENCY)
    return slack


@dataclass
class JiraHarness:
    clock: ManualClock = field(default_factory=lambda: ManualClock(datetime(2026, 10, 5, tzinfo=UTC)))
    tool_id: str = "jira@inst/create_issue"
    tool_name: str = "jira__create_issue"
    fake: FakeJira = field(init=False)
    lookup: JiraIssueLookup = field(init=False)
    reconciler: TimedReconciler = field(init=False)
    _times: list[datetime] = field(default_factory=list[datetime])

    def __post_init__(self) -> None:
        self.fake = FakeJira(self.clock)
        self.lookup = JiraIssueLookup(_jira(self.fake))
        self.reconciler = TimedReconciler({self.tool_id: self.lookup}, self.clock, advancing(self.clock), retry_s=2.0)

    @property
    def timing(self):  # noqa: ANN201
        return self.lookup.timing

    def arguments(self) -> Mapping[str, JsonValue]:
        return {"project_key": "PROJ", "summary": "bug", "issue_type_name": "Task"}

    async def apply(self, effect_key: str) -> None:
        with stamped_effect(effect_key):
            ok, _ = await _jira(self.fake).create_issue(project_key="PROJ", summary="bug", issue_type_name="Task")
        assert ok

    def fail_lookups(self, count: int) -> None:
        self.fake.fail_searches = count

    def lookup_times(self) -> list[datetime]:
        return self._times

    def writes(self) -> int:
        return len(self.fake.issues)


@dataclass
class SlackHarness:
    clock: ManualClock = field(default_factory=lambda: ManualClock(datetime(2026, 10, 5, tzinfo=UTC)))
    tool_id: str = "slack@inst/send_message"
    tool_name: str = "slack__send_message"
    fake: FakeSlack = field(init=False)
    lookup: SlackMessageLookup = field(init=False)
    reconciler: TimedReconciler = field(init=False)
    _times: list[datetime] = field(default_factory=list[datetime])

    def __post_init__(self) -> None:
        self.fake = FakeSlack(self.clock)
        self.lookup = SlackMessageLookup(_slack(self.fake))
        self.reconciler = TimedReconciler({self.tool_id: self.lookup}, self.clock, advancing(self.clock), retry_s=2.0)

    @property
    def timing(self):  # noqa: ANN201
        return self.lookup.timing

    def arguments(self) -> Mapping[str, JsonValue]:
        return {"channel": CHANNEL, "message": "deploy done"}

    async def apply(self, effect_key: str) -> None:
        with stamped_effect(effect_key):
            ok, _ = await _slack(self.fake).send_message(channel=CHANNEL, message="deploy done")
        assert ok

    def fail_lookups(self, count: int) -> None:
        self.fake.fail_history = count

    def lookup_times(self) -> list[datetime]:
        return self._times

    def writes(self) -> int:
        return len(self.fake.posts)


def _timed(harness: JiraHarness | SlackHarness) -> JiraHarness | SlackHarness:
    """Record when each lookup reads the provider (RC-4)."""
    lookup = harness.lookup
    original = lookup.lookup

    async def recorded(effect):  # noqa: ANN202
        harness._times.append(harness.clock.now())
        return await original(effect)

    object.__setattr__(lookup, "lookup", recorded)
    return harness


@pytest.fixture(params=["jira", "slack"])
def provider(request: pytest.FixtureRequest) -> JiraHarness | SlackHarness:
    return _timed(JiraHarness() if request.param == "jira" else SlackHarness())


class TestConnectorReconcilers(ReconcilerContract):
    @pytest.fixture
    def reconcile_harness(self, provider: JiraHarness | SlackHarness) -> JiraHarness | SlackHarness:
        return provider


class TestConnectorLookups(ReconcileLookupContract):
    @pytest.fixture
    def lookup_harness(self, provider: JiraHarness | SlackHarness) -> JiraHarness | SlackHarness:
        return provider


async def test_cn1_jira_create_carries_the_tag_only_when_stamped() -> None:
    fake = FakeJira(ManualClock())
    ok, _ = await _jira(fake).create_issue(project_key="P", summary="s", issue_type_name="Task", labels=["ops"])
    assert ok and fake.issues[0]["labels"] == ["ops"]
    with stamped_effect("org:run:t1.0"):
        ok, _ = await _jira(fake).create_issue(project_key="P", summary="s", issue_type_name="Task", labels=["ops"])
    assert ok and fake.issues[1]["labels"] == ["ops", effect_tag("org:run:t1.0")]


async def test_cn1_the_fake_refuses_labels_jira_refuses() -> None:
    fake = FakeJira(ManualClock())
    ok, body = await _jira(fake).create_issue(project_key="P", summary="s", issue_type_name="Task", labels=["two words"])
    assert not ok and fake.issues == []
    assert "label" in body.lower()


async def test_cn2_jira_lookup_sends_the_label_jql_and_needs_two_negatives() -> None:
    h = JiraHarness()
    verdict = await h.reconciler.reconcile(unknown_effect(h))
    assert verdict.answer == "not_applied" and verdict.lookups == 2
    assert h.fake.searches == [f'labels = "{effect_tag("org-1:run-1:t1.0")}"'] * 2


async def test_cn2_jira_search_lag_hides_a_fresh_issue_from_the_first_read() -> None:
    h = JiraHarness()
    effect = unknown_effect(h)
    h.clock.advance(h.timing.tool_timeout_s + LEASE_S - 5)
    await h.apply(effect.effect_key)
    verdict = await h.reconciler.reconcile(effect)
    assert verdict.answer == "applied" and verdict.lookups == 2
    assert verdict.evidence is not None and verdict.evidence.receipt["issueKey"] == "PROJ-1"


async def test_cn3_slack_post_carries_metadata_only_when_stamped() -> None:
    fake = FakeSlack(ManualClock())
    ok, _ = await _slack(fake).send_message(channel=CHANNEL, message="hi")
    assert ok and "metadata" not in fake.posts[0]
    with stamped_effect("org:run:t1.0"):
        ok, _ = await _slack(fake).send_message(channel=CHANNEL, message="hi")
    assert ok
    assert fake.posts[1]["metadata"] == {
        "event_type": "pipeshub_effect",
        "event_payload": {"effect": effect_tag("org:run:t1.0")},
    }


async def test_cn3_the_fake_refuses_malformed_metadata() -> None:
    fake = FakeSlack(ManualClock())
    refused = await fake.chat_post_message(channel=CHANNEL, text="x", metadata={"event_payload": "x"})
    assert refused.success is False and refused.error == "invalid_metadata_format"


async def test_cn4_slack_lookup_reads_history_with_metadata_from_before_the_post() -> None:
    h = SlackHarness()
    effect = unknown_effect(h)
    await h.apply(effect.effect_key)
    verdict = await h.reconciler.reconcile(effect)
    assert verdict.answer == "applied" and verdict.lookups == 1
    assert verdict.evidence is not None and verdict.evidence.receipt["channel"] == CHANNEL


async def test_cn4_an_unknown_channel_is_a_failed_read_not_a_negative() -> None:
    h = SlackHarness()
    effect = unknown_effect(h)
    effect = type(effect)(**{**effect.__dict__, "arguments": {"channel": "C9999999999", "message": "x"}})
    verdict = await h.reconciler.reconcile(effect)
    assert (verdict.answer, verdict.reason) == ("still_unknown", "lookup_failed")


async def test_cn5_a_slack_post_with_attachments_is_undecidable() -> None:
    h = SlackHarness()
    effect = unknown_effect(h)
    effect = type(effect)(**{**effect.__dict__, "arguments": {**effect.arguments, "attachment_record_ids": ["r1"]}})
    with reconciling():
        assert isinstance(await h.lookup.lookup(effect), LookupUndecidable)
    verdict = await h.reconciler.reconcile(effect)
    assert (verdict.answer, verdict.reason) == ("still_unknown", "undecidable")


async def test_cn7_a_jira_create_lost_to_a_crash_is_reconciled_applied_once() -> None:
    world = World()
    world.admit()
    fake = FakeJira(world.clock, index_lag_s=0.0)

    async def create(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        ok, body = await _jira(fake).create_issue(**dict(args))  # type: ignore[arg-type]
        return ToolExecution(status="ok" if ok else "error", content=json.loads(body))

    world.tools.add("jira__create_issue", create, tags=[("category", "write")])
    tool_id = world.tools.describe("jira__create_issue").tool_id  # type: ignore[union-attr]

    async def stepping(seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            world.clock.advance(min(1.0, remaining))
            remaining -= 1.0
            for _ in range(4):
                await asyncio.sleep(0)

    reconciler = TimedReconciler({tool_id: JiraIssueLookup(_jira(fake))}, world.clock, stepping)
    _, port = scripted(
        reply("", ("jira__create_issue", {"project_key": "PROJ", "summary": "bug", "issue_type_name": "Task"})),
        reply("filed"),
    )

    def kernel():  # noqa: ANN202
        return world.kernel(port, reconciler=reconciler, renew_interval_s=5.0, sleep=world.clock.sleep)

    with armed({"worker.tool.after_effect": Raise(Crash)}):
        await world.crash(kernel())
    done = await world.run(kernel())
    assert done.status == "completed"
    assert len(fake.issues) == 1
    assert world.events("tool.reconciled")[0].data["verdict"] == "applied"


def _effect(name: str, tool_id: str, *, idempotent: bool = False) -> ToolEffect:
    return ToolEffect(tool_id, "write", idempotent, SinkDescriptor(egress="external", args_leave_boundary=True))


class _Registry:
    def __init__(self, tools: dict[str, object]) -> None:
        self._tools = tools

    def has(self, name: str) -> bool:
        return name in self._tools

    def resolve_by_name(self, name: str) -> object:
        return self._tools[name]


@dataclass
class _Bound:
    bound_instance: object


def test_cn8_flag_on_hooks_exactly_the_granted_hooked_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_RECONCILE, "true")
    registry = _Registry(
        {
            "jira__create_issue": _Bound(_jira(FakeJira(ManualClock()))),
            "slack__send_message": _Bound(_slack(FakeSlack(ManualClock()))),
            "slack__delete_message": _Bound(object()),
        }
    )
    effects = {
        "jira__create_issue": _effect("jira__create_issue", "jira@i/create_issue"),
        "slack__send_message": _effect("slack__send_message", "slack@i/send_message"),
        "slack__delete_message": _effect("slack__delete_message", "slack@i/delete_message"),
    }
    hooks = build_reconcile_hooks(registry, effects, HarnessLog.named("test"))  # type: ignore[arg-type]
    assert hooks is not None and set(hooks) == {"jira@i/create_issue", "slack@i/send_message"}
    assert set(HOOKS) == {"jira__create_issue", "slack__send_message"}
    only_jira = build_reconcile_hooks(registry, {"jira__create_issue": effects["jira__create_issue"]}, HarnessLog.named("t"))  # type: ignore[arg-type]
    assert only_jira is not None and set(only_jira) == {"jira@i/create_issue"}


def test_cn8_flag_off_builds_no_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HARNESS_RECONCILE, raising=False)
    registry = _Registry({"jira__create_issue": _Bound(object())})
    effects = {"jira__create_issue": _effect("jira__create_issue", "jira@i/create_issue")}
    assert build_reconcile_hooks(registry, effects, HarnessLog.named("t")) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("toolset", "tool", "read"),
    [(Jira, "create_issue", "find_effect_issue"), (Slack, "send_message", "find_effect_message")],
)
def test_cn9_the_hook_read_is_not_a_tool_and_the_write_still_is(toolset: type, tool: str, read: str) -> None:
    meta = getattr(getattr(toolset, tool), "_agent_tool_meta", None)
    assert meta is not None and meta.path.endswith(f"/{tool}")
    assert not hasattr(getattr(toolset, read), "_agent_tool_meta")
