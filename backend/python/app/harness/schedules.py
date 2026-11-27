"""``schedule_create``: a schedule asked for in chat (ADR-SCH-01).

The model proposes the schedule as the person will see it; the person approves that exact call on its own card,
every time (D2: no conversation grant or org rule answers it); Node makes it only on that approval, read from the
run's journal (D3). The worker holds no credential of the person, so the tool posts the approved call to Node's
harness state API with the attempt's token.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast, override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from app.harness.contracts.generated.pipeshub.api.schedule_create_arguments import (
    ScheduleCreateArguments,
)
from app.harness.journal_node.client import error_code, error_message
from harness_core.contracts import canonical_digest, validate_contract
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
# Node's bounds on a one-off (D1); checked here before the card so a time that cannot be made is never asked about.
MIN_LEAD: Final = timedelta(seconds=60)
MAX_LEAD: Final = timedelta(days=366)
MIN_CRON_GAP_MINUTES: Final = 5
SCHEDULES_PAGE: Final = "Workspace → Schedules"
_PATH: Final = "/schedules"
ASK_RULE: Final = "tool"
ASK_LAYER: Final = "deployment"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- the arguments --------------------------------------------------------------------------------------------------


def _inlined(node: object, defs: Mapping[str, Any]) -> Any:  # noqa: ANN401 - a JSON Schema fragment
    """``node`` with its ``$ref``s replaced by their definitions, without pydantic's titles, and an optional field's
    ``None`` branch dropped: the contract leaves an optional argument out, never null."""
    if isinstance(node, list):
        return [_inlined(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _inlined(defs[str(node["$ref"]).rsplit("/", 1)[1]], defs)
    branches = node.get("anyOf")
    if node.get("default", ...) is None and isinstance(branches, list):
        kept = [b for b in branches if b != {"type": "null"}]
        if len(kept) == 1:
            merged = {**kept[0], **{k: v for k, v in node.items() if k not in ("anyOf", "default")}}
            return _inlined(merged, defs)
    return {key: _inlined(value, defs) for key, value in node.items() if key not in ("title", "$defs")}


@functools.cache
def _contract_schema() -> dict[str, Any]:
    """What the model is shown: ``schedule-create-arguments.schema.json`` as its generated model gives it."""
    schema = ScheduleCreateArguments.model_json_schema(by_alias=True)
    return cast("dict[str, Any]", _inlined(schema, schema.get("$defs", {})))


def _contract_parameters() -> list[ToolParameter]:
    """The contract's top-level properties, for code that reads ``parameters`` rather than the input schema."""
    schema = _contract_schema()
    required = set(schema.get("required", ()))
    parameters: list[ToolParameter] = []
    for name, prop in schema["properties"].items():
        kind = ParameterType(prop.get("type", "object"))
        parameters.append(ToolParameter(
            name, kind, str(prop.get("description", "")), required=name in required,
            items=prop.get("items"), properties=prop.get("properties"),
        ))
    return parameters



def _zone(value: object) -> ZoneInfo | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _instant(value: object) -> datetime | None:
    """An instant the contract's pattern let through, or Node's ``nextFireAt``; ``None`` when it is not a calendar
    date (``2026-02-30T…``)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


_MONTHS: Final = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_DAYS: Final = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
# minute, hour, day of month, month, day of week: their bounds and names as Node's cron parser (cron-parser 4) reads.
_CRON_FIELDS_SPEC: Final = (
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, {name: i + 1 for i, name in enumerate(_MONTHS)}),
    ("day of week", 0, 7, {name: i for i, name in enumerate(_DAYS)}),
)
# Days a cron is matched over to find whether it ever fires and how close two of its slots come: eight years from a
# fixed day hold every leap day and weekday-of-month pattern, and no wall clock is read.
_CRON_SCAN_FROM: Final = datetime(2026, 1, 1, tzinfo=UTC).date()
_CRON_SCAN_DAYS: Final = 8 * 366


class _CronInvalid(ValueError):
    pass


@dataclass(frozen=True)
class _DaySpec:
    """What a day-of-month or day-of-week field matches: plain values, and the ``L``/``nL``/``n#k`` forms."""

    values: frozenset[int]
    wildcard: bool
    last: bool = False
    last_weekdays: frozenset[int] = frozenset()
    nth: tuple[int, int] | None = None


def _value(text: str, low: int, high: int, names: Mapping[str, int], field: str) -> int:
    if text.isdigit():
        value = int(text)
    elif text.upper() in names:
        value = names[text.upper()]
    else:
        raise _CronInvalid(f"`when.cron` has an unreadable {field} value {text!r}.")
    if not low <= value <= high:
        raise _CronInvalid(f"`when.cron` {field} {value} is outside {low}-{high}.")
    return value


def _item(item: str, low: int, high: int, names: Mapping[str, int], field: str, *, days: bool) -> tuple[set[int], bool]:
    """One comma item's values, and whether it is a wildcard (``*``, or ``?`` in a day field)."""
    body, slash, step_text = item.partition("/")
    if slash and not step_text.isdigit():
        raise _CronInvalid(f"`when.cron` has an unreadable step in {field}: {item!r}.")
    step = int(step_text) if slash else 1
    if step == 0:
        raise _CronInvalid(f"`when.cron` cannot step by 0 in {field}.")
    wildcard = body == "*" or (days and body == "?")
    if wildcard:
        first, last = low, high
    elif "-" in body:
        a, _, b = body.partition("-")
        first, last = _value(a, low, high, names, field), _value(b, low, high, names, field)
        if first > last:
            raise _CronInvalid(f"`when.cron` has a backwards range in {field}: {item!r}.")
    else:
        first = _value(body, low, high, names, field)
        last = high if slash else first
    return set(range(first, last + 1, step)), wildcard and not slash


def _field(text: str, index: int) -> tuple[set[int], _DaySpec | None]:
    field, low, high, names = _CRON_FIELDS_SPEC[index]
    values: set[int] = set()
    wildcard = False
    last = False
    last_weekdays: set[int] = set()
    nth: tuple[int, int] | None = None
    items = text.split(",")
    for item in items:
        if index == 2 and item.upper() == "L":
            last = True
            continue
        if index == 4 and len(item) > 1 and item.upper().endswith("L"):
            last_weekdays.add(_value(item[:-1], low, high, names, field) % 7)
            continue
        if index == 4 and "#" in item:
            if len(items) > 1:
                raise _CronInvalid("`when.cron` cannot combine `#` with a list in the day of week.")
            day, _, occurrence = item.partition("#")
            if not occurrence.isdigit() or not 1 <= int(occurrence) <= 5:
                raise _CronInvalid("`when.cron` day-of-week `#` takes an occurrence from 1 to 5.")
            nth = (_value(day, low, high, names, field) % 7, int(occurrence))
            continue
        found, wild = _item(item, low, high, names, field, days=index in (2, 4))
        values |= found
        wildcard = wildcard or wild
    if index == 4:
        values = {v % 7 for v in values}
    if index in (2, 4):
        spec = _DaySpec(frozenset(values), wildcard, last, frozenset(last_weekdays), nth)
        return values, spec
    return values, None


def _day_matches(day: date, dom: _DaySpec, dow: _DaySpec, months: set[int]) -> bool:
    if day.month not in months:
        return False
    last_day = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    weekday = (day.weekday() + 1) % 7

    def by_dom() -> bool:
        return day.day in dom.values or (dom.last and day == last_day)

    def by_dow() -> bool:
        return (
            weekday in dow.values
            or (weekday in dow.last_weekdays and day + timedelta(days=7) > last_day)
            or (dow.nth is not None and weekday == dow.nth[0] and (day.day - 1) // 7 + 1 == dow.nth[1])
        )

    # Standard cron, as Node's parser: with both day fields restricted, a day matching either fires.
    if dom.wildcard and dow.wildcard:
        return True
    if dom.wildcard:
        return by_dow()
    if dow.wildcard:
        return by_dom()
    return by_dom() or by_dow()


def _cron_problem(cron: object) -> str | None:
    """What Node's ``assertCron`` refuses, said for the model: five fields its parser reads, a slot that ever comes,
    and no two slots closer than five minutes."""
    if not isinstance(cron, str) or len(cron.split()) != 5:
        return "`when.cron` must be a five-field cron expression (minute hour day-of-month month day-of-week)."
    try:
        parsed = [_field(text, index) for index, text in enumerate(cron.split())]
    except _CronInvalid as exc:
        return str(exc)
    (minutes, _), (hours, _), (_, dom), (months, _), (_, dow) = parsed
    assert dom is not None and dow is not None
    times = sorted(h * 60 + m for h in hours for m in minutes)
    days = [
        day
        for offset in range(_CRON_SCAN_DAYS)
        if _day_matches(day := _CRON_SCAN_FROM + timedelta(days=offset), dom, dow, months)
    ]
    if not days:
        return "`when.cron` never fires (no day matches it)."
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    if any((b - a).days == 1 for a, b in zip(days, days[1:], strict=False)):
        gaps.append(24 * 60 - times[-1] + times[0])
    if gaps and min(gaps) < MIN_CRON_GAP_MINUTES:
        return f"`when.cron` fires more often than once every {MIN_CRON_GAP_MINUTES} minutes; schedules may not."
    return None


def _shape_problem(arguments: Mapping[str, Any]) -> str | None:
    """The call against ``schedule-create-arguments.schema.json`` (its generated model), the strings as sent."""
    try:
        _ = validate_contract(ScheduleCreateArguments, dict(arguments))
    except ValidationError as exc:
        errors = exc.errors()
        when = arguments.get("when")
        branch = {"once": "When", "cron": "When1"}.get(when.get("kind") if isinstance(when, dict) else None)
        # A ``when`` matches one branch of its union by ``kind``: the other branch's complaints are noise.
        relevant = [e for e in errors if len(e["loc"]) < 2 or e["loc"][0] != "when" or e["loc"][1] == branch]
        error = (relevant or errors)[0]
        where = ".".join(str(part) for part in error["loc"] if not str(part).startswith("When"))
        return f"`{where or 'arguments'}`: {error['msg']}."
    notify = arguments.get("notify", ())
    # The generated model reads an absent optional as None and has no uniqueItems; the schema says neither.
    if notify is None:
        return "`notify`: leave it out for the default; it is never null."
    if len(set(notify)) != len(notify):
        return "`notify`: name each channel once."
    return None


def schedule_problem(arguments: Mapping[str, Any], *, now: datetime | None = None) -> str | None:
    """Why the call cannot make a schedule, worded for the model; ``None``: it may. The shape is the contract's; what
    it cannot say is checked here as Node checks it: the zone, a cron's meaning, ``at`` as a calendar date and, with
    ``now``, its lead time."""
    problem = _shape_problem(arguments)
    if problem is not None:
        return f"{problem} The arguments are name, ask, when ({{kind: once, at, timeZone}} or {{kind: cron, cron, timeZone}}) and an optional notify."
    when = cast("dict[str, Any]", arguments["when"])
    if _zone(when["timeZone"]) is None:
        return "`when.timeZone` must be an IANA time zone such as America/Los_Angeles (the person's own)."
    if when["kind"] == "cron":
        return _cron_problem(when["cron"])
    at = _instant(when["at"])
    if at is None:
        return "`when.at` is not a date on the calendar; work it out again from the current time."
    if now is not None and at < now + MIN_LEAD:
        return "`when.at` must be at least a minute from now; work it out again from the current time."
    if now is not None and at > now + MAX_LEAD:
        return "`when.at` must be within a year from now."
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
        return _contract_parameters()

    @property
    @override
    def raw_input_schema(self) -> dict[str, Any] | None:
        return _contract_schema()

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
