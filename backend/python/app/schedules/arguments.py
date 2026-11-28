"""The ``schedule_create`` arguments, the checks made before its card, and the words it answers with.

One module for both tools that make a schedule from chat (ADR-SCH-02 D5): the harness's (``app.harness.schedules``,
which posts the approved call to Node's harness state API) and classic chat's (``app.agents.agent_loop.schedule_tool``,
which posts it to the schedules API with the person's own request). The arguments contract is
``contracts/harness/ext/pipeshub/api/schedule-create-arguments.schema.json``; Node validates it again at the create.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from app.harness.contracts.generated.pipeshub.api.schedule_create_arguments import (
    ScheduleCreateArguments,
)
from app.harness.contracts.generated.pipeshub.api.schedule_delete_arguments import (
    ScheduleDeleteArguments,
)
from app.harness.contracts.generated.pipeshub.api.schedule_find_arguments import (
    ScheduleFindArguments,
)
from app.harness.contracts.generated.pipeshub.api.schedule_update_arguments import (
    ScheduleUpdateArguments,
)
from harness_core.contracts import validate_contract
from harness_core.tools.base import ParameterType, ToolParameter

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

SCHEDULE_CREATE_TOOL: Final = "schedule_create"
SCHEDULE_FIND_TOOL: Final = "schedule_find"
SCHEDULE_UPDATE_TOOL: Final = "schedule_update"
SCHEDULE_DELETE_TOOL: Final = "schedule_delete"
# The tools' ids on both paths: an approval card is chosen by its id, never by the tool's name (ADR-SCH-01 D4).
SCHEDULE_TOOL_ID: Final = "pipeshub@schedules/create"
SCHEDULE_FIND_TOOL_ID: Final = "pipeshub@schedules/find"
SCHEDULE_UPDATE_TOOL_ID: Final = "pipeshub@schedules/update"
SCHEDULE_DELETE_TOOL_ID: Final = "pipeshub@schedules/delete"
TOOL_IDS: Final = {
    SCHEDULE_CREATE_TOOL: SCHEDULE_TOOL_ID,
    SCHEDULE_FIND_TOOL: SCHEDULE_FIND_TOOL_ID,
    SCHEDULE_UPDATE_TOOL: SCHEDULE_UPDATE_TOOL_ID,
    SCHEDULE_DELETE_TOOL: SCHEDULE_DELETE_TOOL_ID,
}
# The schedules toolset's model names: a person's own chat only, never a delegated child's (ADR-SCH-03 D1).
SCHEDULE_TOOL_NAMES: Final = frozenset(TOOL_IDS)
# The tools that change a schedule: each asks the person every time (ADR-SCH-03 D5).
ASKED_TOOL_IDS: Final = frozenset({SCHEDULE_TOOL_ID, SCHEDULE_UPDATE_TOOL_ID, SCHEDULE_DELETE_TOOL_ID})
CARD_LABELS: Final = {
    SCHEDULE_TOOL_ID: "Set up a schedule",
    SCHEDULE_UPDATE_TOOL_ID: "Change a schedule",
    SCHEDULE_DELETE_TOOL_ID: "Delete a schedule",
}
# The most schedules a find answers (ADR-SCH-03 D2), and how much of each one's ask it shows.
FIND_LIMIT: Final = 20
_ASK_SHOWN: Final = 200
Tool = Literal["create", "update", "delete", "find"]
CARD_LABEL: Final = "Set up a schedule"
# Node's bounds on a one-off (D1); checked here before the card so a time that cannot be made is never asked about.
MIN_LEAD: Final = timedelta(seconds=60)
MAX_LEAD: Final = timedelta(days=366)
MIN_CRON_GAP_MINUTES: Final = 5
SCHEDULES_PAGE: Final = "Workspace → Schedules"


def utc_now() -> datetime:
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
        rest = {k: v for k, v in node.items() if k not in ("anyOf", "default")}
        if len(kept) == 1:
            return _inlined({**kept[0], **rest}, defs)
        return _inlined({**rest, "anyOf": kept}, defs)
    return {key: _inlined(value, defs) for key, value in node.items() if key not in ("title", "$defs")}


_MODELS: Final[dict[str, type[BaseModel]]] = {
    "create": ScheduleCreateArguments,
    "update": ScheduleUpdateArguments,
    "delete": ScheduleDeleteArguments,
    "find": ScheduleFindArguments,
}
# The class name of each `when` branch in a generated model, by `kind`: an error in the other branch is noise.
_WHEN_BRANCHES: Final[dict[str, dict[str, str]]] = {
    "create": {"once": "When", "cron": "When1"},
    "update": {"once": "When", "cron": "When3"},
}


@functools.cache
def contract_schema(tool: Tool = "create") -> dict[str, Any]:
    """What the model is shown: ``schedule-<tool>-arguments.schema.json`` as its generated model gives it."""
    schema = _MODELS[tool].model_json_schema(by_alias=True)
    return cast("dict[str, Any]", _inlined(schema, schema.get("$defs", {})))


def contract_parameters(tool: Tool = "create") -> list[ToolParameter]:
    """The contract's top-level properties, for code that reads ``parameters`` rather than the input schema."""
    schema = contract_schema(tool)
    required = set(schema.get("required", ()))
    parameters: list[ToolParameter] = []
    for name, prop in schema["properties"].items():
        kind = ParameterType(prop.get("type", "object") if "anyOf" not in prop else "object")
        parameters.append(ToolParameter(
            name, kind, str(prop.get("description", "")), required=name in required,
            items=prop.get("items"), properties=prop.get("properties"),
        ))
    return parameters


def zone_of(value: object) -> ZoneInfo | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


# Node's ``parseInstant`` (``domain/schedules.ts``): its pattern, ASCII digits only, and the same bounds on each part.
_INSTANT: Final = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,3}))?)?"
    r"(?:Z|([+-])([0-9]{2}):([0-9]{2}))$"
)


def parse_instant(value: object) -> datetime | None:
    """The instant ``value`` names, read as Node's ``parseInstant`` reads it, or ``None`` where Node's is NaN: a date
    off the calendar (``2026-02-30``), an hour past 23, a minute or second past 59, an offset past 14:59. Python's
    own parse would roll ``+00:60`` into ``+01:00`` and show a card Node then refuses; the string is never changed."""
    parts = _INSTANT.match(value) if isinstance(value, str) else None
    if parts is None:
        return None
    year, month, day, hour, minute = (int(parts[i]) for i in range(1, 6))
    second = int(parts[6] or 0)
    sign, offset_hours, offset_minutes = parts[8], int(parts[9] or 0), int(parts[10] or 0)
    if hour > 23 or minute > 59 or second > 59 or offset_hours > 14 or offset_minutes > 59:
        return None
    try:
        local = datetime(year, month, day, hour, minute, second, int((parts[7] or "0").ljust(6, "0")), tzinfo=UTC)
    except ValueError:
        return None
    offset = timedelta(hours=offset_hours, minutes=offset_minutes)
    try:
        return local - offset if sign == "+" else local + offset
    except OverflowError:
        # Its offset moves it past year 1 or 9999: no instant Python (or Node's window) can hold.
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


def _decimal(text: str) -> bool:
    """ASCII digits only: ``str.isdigit`` also takes ``²`` and other digits ``int`` cannot read."""
    return text.isascii() and text.isdigit()


@dataclass(frozen=True)
class _DaySpec:
    """What a day-of-month or day-of-week field matches: plain values, and the ``L``/``nL``/``n#k`` forms."""

    values: frozenset[int]
    wildcard: bool
    last: bool = False
    last_weekdays: frozenset[int] = frozenset()
    nth: tuple[int, int] | None = None


def _value(text: str, low: int, high: int, names: Mapping[str, int], field: str) -> int:
    if _decimal(text):
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
    if slash and not _decimal(step_text):
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
            if not _decimal(occurrence) or not 1 <= int(occurrence) <= 5:
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


def _shape_problem(arguments: Mapping[str, Any], tool: Tool = "create") -> str | None:
    """The call against ``schedule-<tool>-arguments.schema.json`` (its generated model), the strings as sent."""
    try:
        _ = validate_contract(_MODELS[tool], dict(arguments))
    except ValidationError as exc:
        errors = exc.errors()
        when = arguments.get("when")
        kind = when.get("kind") if isinstance(when, dict) else None
        branch = _WHEN_BRANCHES.get(tool, {}).get(kind) if isinstance(kind, str) else None
        # A ``when`` matches one branch of its union by ``kind``: the other branch's complaints are noise.
        relevant = [e for e in errors if len(e["loc"]) < 2 or e["loc"][0] != "when" or e["loc"][1] == branch]
        error = (relevant or errors)[0]
        where = ".".join(str(part) for part in error["loc"] if not str(part).startswith("When"))
        return f"`{where or 'arguments'}`: {error['msg']}."
    # The generated model reads an absent optional as None and has no uniqueItems; the schema says neither, so an
    # explicit null (`name: null`, `action: null`) is refused as the model-facing schema refuses it.
    nulled = sorted(key for key, value in arguments.items() if value is None)
    if nulled:
        return f"`{nulled[0]}`: leave it out instead; it is never null."
    notify = arguments.get("notify", ())
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
    return when_problem(cast("dict[str, Any]", arguments["when"]), now=now)


def when_problem(when: Mapping[str, Any], *, now: datetime | None = None) -> str | None:
    """What Node refuses of a schema-valid ``when``: the zone, a cron's meaning, ``at`` as a calendar date and, with
    ``now``, its lead time."""
    if zone_of(when["timeZone"]) is None:
        return "`when.timeZone` must be an IANA time zone such as America/Los_Angeles (the person's own)."
    if when["kind"] == "cron":
        return _cron_problem(when["cron"])
    at = parse_instant(when["at"])
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
    shown = zone_of(zone) or zone_of(when.get("timeZone")) or ZoneInfo("UTC")
    label = str(shown.key)
    if when.get("kind") == "once":
        at = parse_instant(when.get("at")) or parse_instant(next_fire_at)
        return f"once, on {_at_words(at, shown)} ({label})" if at is not None else "once"
    words = f"repeating on the cron schedule `{when.get('cron')}` in {when.get('timeZone')}"
    first = parse_instant(next_fire_at)
    return f"{words}, next on {_at_words(first, shown)} ({label})" if first is not None else words


# --- what the tool says --------------------------------------------------------------------------------------------

SHORT_DESCRIPTION: Final = (
    "Set up a reminder or a one-time or repeating schedule the user asked for (later, tomorrow at 9, every Monday); "
    "they approve it first."
)
DESCRIPTION: Final = (
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
    """Node could not be reached, or answered in a way the caller cannot read."""


def schedule_made(data: object) -> ScheduleMade:
    """Node's answer to a create: ``{schedule, created}`` from the harness route, the schedule itself from the
    public one. Raises ``ScheduleUnavailable`` for an answer that is neither."""
    try:
        answer = cast("dict[str, Any]", data)
        schedule = cast("dict[str, Any]", answer["schedule"] if "schedule" in answer else answer)
        next_fire_at = schedule.get("nextFireAt")
        return ScheduleMade(
            schedule_id=str(schedule["id"]),
            name=str(schedule["name"]),
            when=dict(schedule.get("trigger") or {}),
            next_fire_at=next_fire_at if isinstance(next_fire_at, str) else None,
            created=bool(answer.get("created", True)),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ScheduleUnavailable("invalid schedule answer from Node") from exc


def invalid_text(problem: str) -> str:
    return f"{problem} Nothing was scheduled."


def scheduled_text(made: ScheduleMade, arguments: Mapping[str, Any], *, zone: str | None) -> str:
    when = when_words(made.when or cast("dict[str, Any]", arguments["when"]), zone=zone, next_fire_at=made.next_fire_at)
    return (
        f"Scheduled “{made.name}”: {when}. Each run starts its own chat and you'll be notified; the person "
        f"can pause or delete it in {SCHEDULES_PAGE}."
    )


def refused_text(refused: ScheduleRefused) -> str:
    reason = refused.message or refused.code or "it was refused"
    return f"Not scheduled: {reason} Do not retry the same call; tell the user plainly why it was not set up."


def unavailable_text() -> str:
    return (
        "The schedule may not have been set up: PipesHub's scheduler did not answer. Do not retry now; "
        f"tell the user to check {SCHEDULES_PAGE}."
    )



# --- managing a schedule (ADR-SCH-03) ------------------------------------------------------------------------------

_FIELDS: Final = ("name", "ask", "when", "notify")


def update_problem(arguments: Mapping[str, Any], *, now: datetime | None = None) -> str | None:
    """Why a ``schedule_update`` call cannot change a schedule, worded for the model; ``None``: it may. Besides the
    contract, the one rule its model cannot say (field changes or one action, never both, never neither) and what Node
    checks of a new ``when``."""
    problem = _shape_problem(arguments, "update")
    if problem is not None:
        return (
            f"{problem} The arguments are id and revision (as schedule_find answered them), then either changes "
            "(name, ask, when, notify) or one action (pause, resume, skip_next, run_now)."
        )
    fields = [f for f in _FIELDS if f in arguments]
    if "action" in arguments and fields:
        return "`action` is not combined with changes: make the change and the action as two calls."
    if "action" not in arguments and not fields:
        return "Nothing to change: give name, ask, when or notify, or one action."
    when = arguments.get("when")
    return when_problem(cast("dict[str, Any]", when), now=now) if isinstance(when, dict) else None


def delete_problem(arguments: Mapping[str, Any]) -> str | None:
    problem = _shape_problem(arguments, "delete")
    return f"{problem} The arguments are id and revision, as schedule_find answered them." if problem else None


def find_problem(arguments: Mapping[str, Any]) -> str | None:
    problem = _shape_problem(arguments, "find")
    return f"{problem} The arguments are an optional query, status and madeHere." if problem else None


def _view_when(view: Mapping[str, Any]) -> dict[str, Any]:
    trigger = view.get("trigger")
    if not isinstance(trigger, dict):
        return {}
    keys = ("kind", "at", "timeZone") if trigger.get("kind") == "once" else ("kind", "cron", "timeZone")
    return {k: trigger[k] for k in keys if k in trigger}


def _view_ask(view: Mapping[str, Any]) -> str:
    template = view.get("input")
    query = template.get("query") if isinstance(template, dict) else None
    return query if isinstance(query, str) else ""


def card_schedule(view: Mapping[str, Any]) -> dict[str, Any]:
    """A schedule's current values as a change's card shows them: ``{id, name, ask, when, notify, status, revision}``."""
    notify = view.get("notify")
    return {
        "id": str(view.get("id", "")),
        "name": str(view.get("name", "")),
        "ask": _view_ask(view),
        "when": _view_when(view),
        "notify": [str(n) for n in notify] if isinstance(notify, list) else [],
        "status": str(view.get("status", "")),
        "revision": view.get("revision") if isinstance(view.get("revision"), int) else None,
    }


def found_entry(view: Mapping[str, Any], *, zone: str | None, conversation_id: str | None = None) -> dict[str, Any]:
    """One schedule as ``schedule_find`` answers it: what the model needs to name it to the person and to change it."""
    ask = _view_ask(view)
    origin = view.get("origin")
    made_here = view.get("madeHere")
    if not isinstance(made_here, bool):
        made_here = bool(
            conversation_id and isinstance(origin, dict) and origin.get("conversationId") == conversation_id
        )
    next_fire_at = view.get("nextFireAt") if isinstance(view.get("nextFireAt"), str) else None
    return {
        "id": str(view.get("id", "")),
        "revision": view.get("revision"),
        "name": str(view.get("name", "")),
        "asks": ask if len(ask) <= _ASK_SHOWN else ask[: _ASK_SHOWN - 1] + "…",
        "when": when_words(_view_when(view), zone=zone, next_fire_at=next_fire_at),
        "status": str(view.get("status", "")),
        "madeHere": made_here,
    }


def found_text(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No schedule of the person's matches. Say so plainly; do not guess another one."
    return (
        f"{len(entries)} of the person's schedules, this chat's first. Change or delete one only by its id and "
        "revision; when more than one could be meant, ask the person which."
    )


def changed_text(view: Mapping[str, Any], arguments: Mapping[str, Any], *, zone: str | None) -> str:
    """What is now true of a schedule after a ``schedule_update``, in plain words (ADR-SCH-03 D7)."""
    name = str(view.get("name") or arguments.get("name") or "the schedule")
    next_fire_at = view.get("nextFireAt") if isinstance(view.get("nextFireAt"), str) else None
    when = when_words(_view_when(view), zone=zone, next_fire_at=next_fire_at)
    action = arguments.get("action")
    manage = f" The person manages it in {SCHEDULES_PAGE}."
    if action == "pause":
        return f"Paused “{name}”; it won't run until it is resumed.{manage}"
    if action == "resume":
        return f"Resumed “{name}”: it runs {when}.{manage}"
    if action == "skip_next":
        skipped = parse_instant(view.get("skipNextAt"))
        shown = zone_of(zone) or zone_of(_view_when(view).get("timeZone")) or ZoneInfo("UTC")
        on = f" on {_at_words(skipped, shown)}" if skipped is not None else ""
        return f"“{name}” will skip its next run{on}, then run as usual.{manage}"
    if action == "run_now":
        return f"“{name}” is running now: its answer arrives in its own chat and the person is notified.{manage}"
    changes: list[str] = []
    if "ask" in arguments:
        changes.append(f"it now asks “{_view_ask(view) or arguments['ask']}”")
    if "notify" in arguments:
        changes.append("it now tells the person by " + ", ".join(str(n) for n in arguments["notify"]))
    if "name" in arguments:
        changes.append(f"it is now named “{name}”")
    changes.append(f"it runs {when}")
    return f"Changed “{name}”: " + "; ".join(changes) + f".{manage}"


def deleted_text(name: str) -> str:
    return f"Deleted “{name}”; it will not run again."


def change_refused_text(refused: ScheduleRefused) -> str:
    """A refused change in the person's terms, and what the model does next."""
    if refused.code == "SCHEDULE_CHANGED":
        return (
            "Not changed: the schedule changed since it was read. Read it again with schedule_find and ask the person "
            "again before changing it."
        )
    if refused.code == "SCHEDULE_NOT_FOUND":
        return "Not changed: no schedule of the person's has that id. Find it with schedule_find; never use a name."
    if refused.code == "SCHEDULE_RAN_ONCE":
        return (
            "Not changed: that one-off has already run or been skipped, so it cannot be changed. Offer to set up a new "
            "one instead."
        )
    reason = refused.message or refused.code or "it was refused"
    return f"Not changed: {reason} Do not retry the same call; tell the user plainly why."


def change_unavailable_text() -> str:
    return (
        "The schedule may or may not have been changed: PipesHub's scheduler did not answer. Do not retry now; tell "
        f"the user to check {SCHEDULES_PAGE}."
    )


FIND_SHORT_DESCRIPTION: Final = (
    "Find the user's own reminders and schedules (what each asks, when it runs next, whether this chat made it)."
)
FIND_DESCRIPTION: Final = (
    "Find the user's own schedules and reminders: \"what reminders do I have?\", \"when does the standup one run "
    "next?\", \"that one\", \"the reminder I just set\". Without a query it lists them, the ones this chat made first, "
    "then the next to run; `query` matches words of a name, `status` one state, `madeHere: true` only this chat's. "
    "Call it before schedule_update or schedule_delete: they take the id and revision it answers, never a name. When "
    "more than one schedule could be meant, ask the user which before changing anything."
)
UPDATE_SHORT_DESCRIPTION: Final = (
    "Change, pause, resume, skip or run now one of the user's reminders or schedules; they approve it first."
)
UPDATE_DESCRIPTION: Final = (
    "Change one of the user's schedules or reminders: what it asks (\"make it an animal joke\"), when (\"move it to "
    "9 am\", \"every weekday instead\"), its name or notices; or one action: `pause`, `resume`, `skip_next` (\"skip "
    "tomorrow's\"), `run_now`. Give the `id` and `revision` schedule_find answered; never act on a name, and when more "
    "than one schedule could be meant, ask the user which. Changes and an action are never in one call. A new `when` "
    "follows schedule_create's rules: work it out from the current time and time zone, never invent one. The user "
    "approves every change on its own card; do not retry a refused call."
)
DELETE_SHORT_DESCRIPTION: Final = "Delete one of the user's reminders or schedules; they approve it first."
DELETE_DESCRIPTION: Final = (
    "Delete one of the user's schedules or reminders (\"cancel that reminder\", \"stop the Monday summary\"). Give "
    "the `id` and `revision` schedule_find answered; never act on a name, and when more than one schedule could be "
    "meant, ask the user which. To stop it for a while, pause it with schedule_update instead. The user approves "
    "every delete on its own card; do not retry a refused call."
)


__all__ = [
    "ASKED_TOOL_IDS",
    "CARD_LABEL",
    "CARD_LABELS",
    "DELETE_DESCRIPTION",
    "DELETE_SHORT_DESCRIPTION",
    "FIND_DESCRIPTION",
    "FIND_LIMIT",
    "FIND_SHORT_DESCRIPTION",
    "SCHEDULE_DELETE_TOOL",
    "SCHEDULE_DELETE_TOOL_ID",
    "SCHEDULE_FIND_TOOL",
    "SCHEDULE_FIND_TOOL_ID",
    "SCHEDULE_UPDATE_TOOL",
    "SCHEDULE_UPDATE_TOOL_ID",
    "TOOL_IDS",
    "UPDATE_DESCRIPTION",
    "UPDATE_SHORT_DESCRIPTION",
    "card_schedule",
    "change_refused_text",
    "change_unavailable_text",
    "changed_text",
    "delete_problem",
    "deleted_text",
    "find_problem",
    "found_entry",
    "found_text",
    "update_problem",
    "when_problem",
    "DESCRIPTION",
    "MAX_LEAD",
    "MIN_CRON_GAP_MINUTES",
    "MIN_LEAD",
    "SCHEDULES_PAGE",
    "SCHEDULE_CREATE_TOOL",
    "SCHEDULE_TOOL_ID",
    "SHORT_DESCRIPTION",
    "ScheduleMade",
    "ScheduleRefused",
    "ScheduleUnavailable",
    "contract_parameters",
    "contract_schema",
    "invalid_text",
    "parse_instant",
    "refused_text",
    "schedule_made",
    "schedule_problem",
    "scheduled_text",
    "unavailable_text",
    "utc_now",
    "when_words",
    "zone_of",
]
