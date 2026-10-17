from contextvars import ContextVar, Token
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_TIMESTAMP_LENGTH = 13

# The user's IANA time zone for the agent run in flight, so record headers
# rendered deep inside tools (search, knowledge graph, fetch) can show each
# date in the zone the answer is written in, weekday included, without the
# zone being threaded through every call site. Set once at the top of each
# run's producer task (``stream_bridge`` and ``chat_modes.bridge``); tasks
# started from it inherit the value. Unset means UTC.
_llm_time_zone: ContextVar[str | None] = ContextVar("llm_time_zone", default=None)

# English names, independent of the process locale (``%a``/``%b`` are not).
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_NOON = 12

_LLM_TIME_CONTEXT_HEADING = "## Time context"

# One subtle line under the heading (tools + relative dates).
_LLM_TIME_CONTEXT_SUBLINE = (
    "Use this when the user asks about the current date, time, day of week, "
    "or time-relative wording (today, tomorrow, this week, etc.). "
    "In replies to the user, use **Time zone** (when shown) for calendar dates and "
    "timezone-aware times (am/pm). For tools, keep each API's datetime format as required."
)


def _utc_reference_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _utc_offset_hh_mm(dt: datetime) -> str:
    """UTC offset for ``dt`` as ``±HH:MM`` (ISO-style, stable across platforms)."""
    off = dt.utcoffset()
    if off is None:
        return "+00:00"
    total_minutes = int(off.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def format_user_timezone_prompt_line(
    time_zone_name: str | None,
    *,
    moment: datetime | None = None,
) -> str:
    """Single markdown line for LLM prompts: IANA id, abbreviation, UTC offset.

    Unknown or invalid IANA names return a line with the raw value only (no offset).
    Whitespace-only names yield an empty string.
    """
    name = (time_zone_name or "").strip()
    if not name:
        return ""

    try:
        tz = ZoneInfo(name)
    except (ZoneInfoNotFoundError, OSError):
        return f"**Time zone**: {name}"

    if moment is None:
        now = datetime.now(tz)
    elif moment.tzinfo is None:
        now = moment.replace(tzinfo=tz)
    else:
        now = moment.astimezone(tz)

    abbr = now.tzname() or "local"
    offset_label = _utc_offset_hh_mm(now)
    return f"**Time zone**: {name} ({abbr}, UTC{offset_label})"


def build_llm_time_context(
    *,
    current_time: str | None = None,
    time_zone: str | None = None,
) -> str:
    """Heading plus **Current time** / **Time zone** lines for LLM prompts."""
    ct = _strip_or_none(current_time)
    tz = _strip_or_none(time_zone)
    if not ct and not tz:
        return ""

    ref = ct or _utc_reference_now_iso()
    tz_line = format_user_timezone_prompt_line(tz) if tz else ""
    current_line = f"**Current time**: {ref}" + ("" if ct else " (UTC)")

    lines = [
        _LLM_TIME_CONTEXT_HEADING,
        "",
        current_line,
    ]
    if tz_line:
        lines.append(tz_line)
    return "\n".join(lines)


def set_llm_time_zone(time_zone: str | None) -> Token[str | None]:
    """Use ``time_zone`` for dates rendered to the model in this context."""
    return _llm_time_zone.set(_strip_or_none(time_zone))


def reset_llm_time_zone(token: Token[str | None]) -> None:
    _llm_time_zone.reset(token)


def _llm_zone() -> tzinfo:
    name = _llm_time_zone.get()
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return timezone.utc


def format_epoch_ms_for_llm(epoch_ms: float) -> str:
    """A source timestamp as the model should read it, weekday worked out here.

    ``2026-07-27 03:00:00 UTC (Sun, Jul 26, 2026, 8:00 PM PDT)``: the UTC
    value as before, then the same moment in the user's time zone (UTC when
    none is set) in the form answers are asked to use. The weekday is taken
    in that zone, so a Monday-morning UTC timestamp reads as Sunday evening
    in California. Models get weekdays wrong when left to work them out
    from a bare date.
    """
    utc = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    local = utc.astimezone(_llm_zone())
    hour = local.hour % _NOON or _NOON
    meridiem = "AM" if local.hour < _NOON else "PM"
    readable = (
        f"{_WEEKDAYS[local.weekday()]}, {_MONTHS[local.month - 1]} {local.day}, {local.year}, "
        f"{hour}:{local.minute:02d} {meridiem} {local.tzname() or 'UTC'}"
    )
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC ({readable})"


def get_epoch_timestamp_in_ms() -> int:
    now = datetime.now(timezone.utc).timestamp()
    return int(now * 1000)

def parse_timestamp(timestamp_str: str) -> int:
    # Remove the 'Z' and add '+00:00' for UTC
    if timestamp_str.endswith("Z") or timestamp_str.endswith("z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"

    dt = datetime.fromisoformat(timestamp_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    timestamp = int(dt.timestamp())

    # Check if timestamp is already in milliseconds (13 digits)
    if len(str(timestamp)) >= MAX_TIMESTAMP_LENGTH:
        return timestamp

    # Convert seconds to milliseconds
    return timestamp * 1000

def epoch_ms_to_iso(epoch_ms: int) -> str:
    """Convert epoch milliseconds to an ISO 8601 UTC datetime string."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()

def prepare_iso_timestamps(start_time: str, end_time: str) -> tuple[str, str]:
    """Converts start and end time strings to ISO 8601 formatted strings."""
    start_timestamp = parse_timestamp(start_time)
    end_timestamp = parse_timestamp(end_time)

    start_dt = datetime.fromtimestamp(start_timestamp / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_timestamp / 1000, tz=timezone.utc)

    return start_dt.isoformat(), end_dt.isoformat()

def datetime_to_epoch_ms(
    dt_obj: datetime | str | None,
    strptime_format: str | None = None,
) -> int | None:
    """Convert datetime object or string to epoch timestamp in milliseconds.

    Args:
        dt_obj: datetime object, ISO string, ServiceNow-style string, or None
        strptime_format: If set and ``dt_obj`` is a str, parse with
            :func:`datetime.strptime` first (e.g. ``%Y-%m-%d %H:%M:%S`` for
            ServiceNow). Naive values are treated as UTC. If parsing fails,
            falls back to :func:`parse_timestamp` for ISO strings.

    Returns:
        Epoch timestamp in milliseconds, or None if input is None or invalid
    """
    if not dt_obj:
        return None
    try:
        if isinstance(dt_obj, str):
            if strptime_format:
                try:
                    dt = datetime.strptime(dt_obj, strptime_format)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    pass
            return parse_timestamp(dt_obj)
        dt = dt_obj
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def string_to_datetime(time_str: str) -> datetime :
    """Converts time stamp with 'Z' to datetime object"""
    if time_str.endswith("Z") or time_str.endswith("z"):
            time_str = time_str[:-1] + "+00:00"
    return datetime.fromisoformat(time_str)

