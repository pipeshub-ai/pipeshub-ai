"""``schedule_create``: a schedule asked for in chat (ADR-SCH-01).

The model proposes the schedule as the person will see it; the person approves that exact call on its own card,
every time (D2: no conversation grant or org rule answers it); Node makes it only on that approval, read from the
run's journal (D3). The worker holds no credential of the person, so the tool posts the approved call to Node's
harness state API with the attempt's token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast, override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.harness.journal_node.client import error_code, error_message
from harness_core.contracts import canonical_digest
from harness_core.effects.flow import (
    AskDestination,
    destination_key,
    destination_wire,
    grant_answerable,
)
from harness_core.effects.policy import stricter
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    EffectKey,
    ExplainsAsk,
    ExplainsDenial,
    args_digest,
    consult_policy,
    refresh_policy,
)
from harness_core.interactions.hold import approval_interaction_id
from harness_core.journal.types import JournalUnavailable
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.durable_port import current_tool_context
from harness_core.tools.tags import EFFECT_TAG_KEY, TAG_SINK_SELF

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx
    from pydantic import JsonValue

    from app.harness.journal_node.client import AttemptRoute
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolEffect
    from harness_core.kernel.core.messages import ToolCall

SCHEDULE_CREATE_TOOL: Final = "schedule_create"
SCHEDULE_TOOL_ID: Final = "pipeshub@schedules/create"
CARD_LABEL: Final = "Set up a schedule"
MAX_NAME_CHARS: Final = 120
MAX_ASK_CHARS: Final = 10_000
NOTIFY_CHANNELS: Final = ("in_app", "email", "slack")
# Node's bounds on a one-off (D1); checked here before the card so a time that cannot be made is never asked about.
MIN_LEAD: Final = timedelta(seconds=60)
MAX_LEAD: Final = timedelta(days=366)
MIN_CRON_GAP_MINUTES: Final = 5
SCHEDULES_PAGE: Final = "Workspace → Schedules"
_PATH: Final = "/schedules"
_FIELDS: Final = frozenset({"name", "ask", "when", "notify"})
_ONCE_FIELDS: Final = frozenset({"kind", "at", "timeZone"})
_CRON_FIELDS: Final = frozenset({"kind", "cron", "timeZone"})
# ISO 8601 with an explicit offset: a bare local time would be read in the worker's zone, not the person's.
_INSTANT: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$")
ASK_RULE: Final = "tool"
ASK_LAYER: Final = "deployment"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- the arguments --------------------------------------------------------------------------------------------------


def _zone(value: object) -> ZoneInfo | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not _INSTANT.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes(field: str) -> set[int] | None:
    """The minutes a cron minute field fires at; ``None`` when it is not one this check reads."""
    minutes: set[int] = set()
    for part in field.split(","):
        body, slash, step_text = part.partition("/")
        if slash and not (step_text.isdigit() and int(step_text) > 0):
            return None
        step = int(step_text) if slash else 1
        if body == "*":
            low, high = 0, 59
        elif "-" in body:
            a, _, b = body.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            low, high = int(a), int(b)
        elif body.isdigit():
            low = int(body)
            high = 59 if slash else low
        else:
            return None
        if not 0 <= low <= high <= 59:
            return None
        minutes.update(range(low, high + 1, step))
    return minutes


def _cron_problem(cron: object) -> str | None:
    if not isinstance(cron, str) or len(cron.split()) != 5:
        return "`when.cron` must be a five-field cron expression (minute hour day-of-month month day-of-week)."
    minutes = _minutes(cron.split()[0])
    if minutes is None:
        return None
    ordered = sorted(minutes)
    gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)] + [60 - ordered[-1] + ordered[0]]
    if len(ordered) > 1 and min(gaps) < MIN_CRON_GAP_MINUTES:
        return f"`when.cron` fires more often than once every {MIN_CRON_GAP_MINUTES} minutes; schedules may not."
    return None


def schedule_problem(arguments: Mapping[str, Any], *, now: datetime | None = None) -> str | None:
    """Why the call cannot make a schedule, worded for the model; ``None``: it may. With ``now``, a one-off's time is
    held to Node's bounds as well."""
    unknown = sorted(set(arguments) - _FIELDS)
    if unknown:
        return f"Unknown argument(s) {unknown}; give only name, ask, when and notify."
    name, ask, when = arguments.get("name"), arguments.get("ask"), arguments.get("when")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= MAX_NAME_CHARS:
        return f"`name` must be a short title of 1 to {MAX_NAME_CHARS} characters."
    if not isinstance(ask, str) or not 0 < len(ask.strip()) <= MAX_ASK_CHARS:
        return f"`ask` must say what each run is asked, in 1 to {MAX_ASK_CHARS} characters."
    if not isinstance(when, dict):
        return "`when` must be {kind: once, at, timeZone} or {kind: cron, cron, timeZone}."
    when = cast("dict[str, Any]", when)
    kind = when.get("kind")
    if kind not in ("once", "cron"):
        return "`when.kind` must be `once` (one time) or `cron` (repeating)."
    if set(when) - (_ONCE_FIELDS if kind == "once" else _CRON_FIELDS):
        fields = "kind, at and timeZone" if kind == "once" else "kind, cron and timeZone"
        return f"`when` for `{kind}` takes only {fields}."
    if _zone(when.get("timeZone")) is None:
        return "`when.timeZone` must be an IANA time zone such as America/Los_Angeles (the person's own)."
    if kind == "cron":
        problem = _cron_problem(when.get("cron"))
        if problem is not None:
            return problem
    else:
        at = _instant(when.get("at"))
        if at is None:
            return "`when.at` must be an ISO 8601 time with its offset, such as 2026-11-26T15:47:00-08:00."
        if now is not None and at < now + MIN_LEAD:
            return "`when.at` must be at least a minute from now; work it out again from the current time."
        if now is not None and at > now + MAX_LEAD:
            return "`when.at` must be within a year from now."
    notify = arguments.get("notify")
    if notify is not None and (
        not isinstance(notify, list) or not notify or any(channel not in NOTIFY_CHANNELS for channel in notify)
    ):
        return f"`notify`, when given, lists channels from {list(NOTIFY_CHANNELS)}; leave it out for the default."
    return None


def _at_words(at: datetime, zone: ZoneInfo) -> str:
    local = at.astimezone(zone)
    hour = local.hour % 12 or 12
    return f"{local:%A} {local.day} {local:%B %Y} at {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def when_words(when: Mapping[str, Any], *, zone: str | None = None, next_fire_at: str | None = None) -> str:
    """When the schedule runs, in plain words in ``zone`` (the run's), else the schedule's own zone."""
    shown = _zone(zone) or _zone(when.get("timeZone")) or ZoneInfo("UTC")
    label = str(shown.key)
    if when.get("kind") == "once":
        at = _instant(when.get("at")) or _instant(next_fire_at)
        return f"once, on {_at_words(at, shown)} ({label})" if at is not None else "once"
    words = f"repeating on the cron schedule `{when.get('cron')}` in {when.get('timeZone')}"
    first = _instant(next_fire_at)
    return f"{words}, next on {_at_words(first, shown)} ({label})" if first is not None else words


# --- Node -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleMade:
    schedule_id: str
    name: str
    when: dict[str, Any]
    next_fire_at: str | None
    created: bool


class ScheduleRefused(Exception):
    """Node refused the create (not approved, a limit, a validation): the call cannot succeed as it is."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class ScheduleUnavailable(Exception):
    """Node could not be reached, or answered in a way the worker cannot read."""


class NodeSchedules:
    """One attempt's schedule creates, through Node's harness state API (``POST /internal/runs/{runId}/schedules``)."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log("app.harness.schedules")

    async def create(
        self, interaction_id: str, call_args_digest: str, arguments: Mapping[str, JsonValue]
    ) -> ScheduleMade:
        """``call_args_digest``: the model's call's, which the interaction is keyed on; ``arguments`` may be the
        person's edit of it (AP-06). The route adds the attempt's id and fence."""
        body: dict[str, JsonValue] = {
            "interactionId": interaction_id,
            "toolId": SCHEDULE_TOOL_ID,
            "callArgsDigest": call_args_digest,
            "arguments": dict(arguments),
        }
        try:
            response = await self._route.post(_PATH, body=body, operation="schedule.create")
        except JournalUnavailable as exc:
            raise ScheduleUnavailable("Node unreachable") from exc
        if response.status_code in (200, 201):
            return self._made(response)
        code = error_code(response)
        if 400 <= response.status_code < 500:
            self._log.warning("schedule.refused", status=response.status_code, detail=code or None)
            raise ScheduleRefused(code, error_message(response))
        self._log.warning("schedule.unavailable", status=response.status_code, detail=code or None)
        raise ScheduleUnavailable(f"schedule route answered {response.status_code}")

    def _made(self, response: httpx.Response) -> ScheduleMade:
        try:
            data = response.json()
            schedule = data["schedule"]
            made = ScheduleMade(
                schedule_id=str(schedule["id"]),
                name=str(schedule["name"]),
                when=dict(schedule.get("trigger") or {}),
                next_fire_at=schedule.get("nextFireAt") if isinstance(schedule.get("nextFireAt"), str) else None,
                created=bool(data.get("created", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleUnavailable("invalid schedule answer from Node") from exc
        self._log.info("schedule.created", scheduleId=made.schedule_id, created=made.created)
        return made


# --- the tool -------------------------------------------------------------------------------------------------------

_DESCRIPTION: Final = (
    "Set up a schedule that runs a request later, once or repeatedly, as the user. Use it only when the user asked "
    "for something later or repeated (\"in 5 minutes\", \"tomorrow at 9\", \"every weekday morning\"); never invent "
    "a time or a repetition they did not ask for, and do the work now when they did not ask for later. Work out the "
    "time from the current time and time zone you were given: \"in 5 minutes\" or \"tomorrow at 9am\" is "
    "`when: {kind: once, at: <ISO 8601 time with its offset>, timeZone: <their IANA zone>}`; a repetition is "
    "`when: {kind: cron, cron: <five-field cron>, timeZone: <their IANA zone>}`, at most once every 5 minutes. "
    "Write `ask` so it stands alone: each run starts a new chat that sees only `ask`, not this conversation. "
    "Before calling, say back in plain words what will run and when. The user approves every schedule on its own "
    "card; call it once per schedule, and do not call it again after a refusal."
)


class ScheduleCreateTool(Tool):
    def __init__(self, schedules: NodeSchedules, *, timezone: str | None = None) -> None:
        self._schedules = schedules
        self._timezone = timezone

    @property
    @override
    def name(self) -> str:
        return SCHEDULE_CREATE_TOOL

    @property
    @override
    def short_description(self) -> str:
        return "Set up a one-time or repeating schedule the user asked for; they approve it first."

    @property
    @override
    def description(self) -> str:
        return _DESCRIPTION

    @property
    @override
    def path(self) -> str:
        return "/toolsets/schedules/create"

    @property
    @override
    def tags(self) -> list[Tag]:
        # The schedule is the person's own, read by no one else: its arguments stay with them.
        return [Tag(key=EFFECT_TAG_KEY, value="write"), TAG_SINK_SELF]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("name", ParameterType.STRING, f"A short title for the schedule (at most {MAX_NAME_CHARS} characters)."),
            ToolParameter(
                "ask", ParameterType.STRING,
                "What each run is asked, written to stand alone (each run starts a new chat that sees only this).",
            ),
            ToolParameter(
                "when", ParameterType.OBJECT, "When it runs: once at a time, or repeating on a cron schedule.",
                properties={
                    "kind": {"type": "string", "enum": ["once", "cron"], "description": "once or cron"},
                    "at": {"type": "string", "description": "once: ISO 8601 time with its offset, at least a minute ahead"},
                    "cron": {"type": "string", "description": "cron: five-field cron, at most once every 5 minutes"},
                    "timeZone": {"type": "string", "description": "The user's IANA time zone, e.g. America/Los_Angeles"},
                },
                required_properties=["kind", "timeZone"],
            ),
            ToolParameter(
                "notify", ParameterType.ARRAY, "Optional: where the user is told of each run; leave out for the default.",
                required=False, items={"type": "string", "enum": list(NOTIFY_CHANNELS)},
            ),
        ]

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        # Node binds the approval to the digest of the arguments as called, so they reach it untouched: no default
        # filled in, no enum corrected. ``execute`` checks them instead.
        del kwargs

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        problem = schedule_problem(kwargs)
        if problem is not None:
            return ToolOutput(success=False, error=f"{problem} Nothing was scheduled.", error_code="invalid_call")
        ctx = current_tool_context()
        if ctx is None:
            return ToolOutput(success=False, error="Schedules can only be made in a PipesHub run.", error_code="unavailable")
        called = ctx.args_digest or args_digest(kwargs)
        interaction = ctx.interaction_id or approval_interaction_id(EffectKey(ctx.run_id, SCHEDULE_TOOL_ID, called))
        try:
            made = await self._schedules.create(interaction, called, kwargs)
        except ScheduleRefused as refused:
            reason = refused.message or refused.code or "it was refused"
            return ToolOutput(
                success=False,
                error=f"Not scheduled: {reason} Do not retry the same call; tell the user plainly why it was not set up.",
                error_code="failed",
            )
        except ScheduleUnavailable:
            return ToolOutput(
                success=False,
                error=(
                    "The schedule may not have been set up: PipesHub's scheduler did not answer. Do not retry now; "
                    f"tell the user to check {SCHEDULES_PAGE}."
                ),
                error_code="unavailable",
            )
        when = when_words(made.when or cast("dict[str, Any]", kwargs["when"]), zone=self._timezone, next_fire_at=made.next_fire_at)
        return ToolOutput(
            success=True,
            data={
                "scheduleId": made.schedule_id,
                "result": (
                    f"Scheduled “{made.name}”: {when}. Each run starts its own chat and you'll be notified; the person "
                    f"can pause or delete it in {SCHEDULES_PAGE}."
                ),
            },
        )


# --- the ask --------------------------------------------------------------------------------------------------------


def schedule_destination(call: ToolCall) -> AskDestination:
    """The card's destination: the schedule, keyed by what it is (its arguments), never by a place a grant names."""
    return AskDestination(CARD_LABEL, destination_key(SCHEDULE_TOOL_ID, canonical_digest(dict(call.arguments))))


@dataclass
class ScheduleAsks:
    """``base`` with a person asked before every ``schedule_create`` (D2). Unlike the repository asks, no conversation
    grant and no rule under it can let one through: the ask offers no grant and this layer never allows a valid call.
    A call that cannot make a schedule runs and is refused by the tool, so nobody is asked about it."""

    base: ApprovalPolicy
    now: Callable[[], datetime] = _utc_now

    def _ours(self, effect: ToolEffect) -> bool:
        return effect.tool_id == SCHEDULE_TOOL_ID

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        decision = self.base(call, effect)
        if not self._ours(effect) or decision == "deny":
            return decision
        if schedule_problem(call.arguments, now=self.now()) is not None:
            # The tool refuses it without reaching Node: nothing is made, so no layer has anything to ask about.
            return "allow" if decision == "ask" else decision
        return stricter(decision, "ask")

    async def refresh(self) -> None:
        await refresh_policy(self.base)

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None:
        await consult_policy(self.base, call, effect)

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        base = self.base(call, effect)
        reason = self.base.ask_reason(call, effect) if base == "ask" and isinstance(self.base, ExplainsAsk) else None
        if not self._ours(effect) or self(call, effect) != "ask":
            return reason
        where = destination_wire(schedule_destination(call))
        if reason:
            return {**reason, "conversationGrant": False, "destination": where}
        return {
            "rule": ASK_RULE, "layer": ASK_LAYER, "toolId": effect.tool_id, "conversationGrant": False,
            "destination": where,
        }

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        if self._ours(effect):
            return False
        return self.base(call, effect) != "deny" and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL


def schedule_asks(policy: ApprovalPolicy, *, offered: bool) -> ApprovalPolicy:
    """The run's policy with ``schedule_create`` asked every time, when the run is offered it."""
    return ScheduleAsks(policy) if offered else policy


__all__ = [
    "CARD_LABEL",
    "SCHEDULE_CREATE_TOOL",
    "SCHEDULE_TOOL_ID",
    "NodeSchedules",
    "ScheduleAsks",
    "ScheduleCreateTool",
    "ScheduleMade",
    "ScheduleRefused",
    "ScheduleUnavailable",
    "schedule_asks",
    "schedule_destination",
    "schedule_problem",
    "when_words",
]
