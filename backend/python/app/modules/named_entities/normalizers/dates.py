"""Date, range and duration normalization.

Common shapes are parsed here so the result does not depend on locale
guessing. Relative phrases ('next Friday') go through dateparser when it
is installed, anchored at the record's reference time. A date with no
year is kept as TIMEX and is not given a range.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.values import DateRangeValue, DurationValue

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_ISO_DATE = re.compile(
    r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<H>\d{2}):(?P<M>\d{2})(?::\d{2}(?:\.\d+)?)?"
    r"(?P<off>Z|[+-]\d{2}:?\d{2})?)?$"
)
_QUARTER = re.compile(r"^Q(?P<q>[1-4])\s*(?:FY\s*)?(?P<y>\d{2,4})$", re.IGNORECASE)
_MONTH_YEAR = re.compile(
    rf"^(?P<mon>{'|'.join(sorted(_MONTHS, key=len, reverse=True))})\.?\s+(?P<y>\d{{4}})$",
    re.IGNORECASE,
)
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH_DAY = re.compile(
    rf"^(?P<mon>{_MONTH_NAMES})\.?\s+(?P<d>\d{{1,2}})(?:(?:,\s*|\s+)(?P<y>\d{{4}}))?$",
    re.IGNORECASE,
)
_DAY_MONTH = re.compile(
    rf"^(?P<d>\d{{1,2}})\s+(?P<mon>{_MONTH_NAMES})\.?(?:(?:,\s*|\s+)(?P<y>\d{{4}}))?$",
    re.IGNORECASE,
)
_FISCAL_YEAR = re.compile(r"^FY\s?(?P<y>\d{4}|\d{2})$", re.IGNORECASE)
_RELATIVE_PERIOD = re.compile(r"^(?P<which>next|last|this)\s+(?P<unit>week|month|quarter|year)$", re.IGNORECASE)
_DURATION = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<u>days?|weeks?|months?|years?|hours?|hrs?|minutes?|mins?|seconds?|secs?|msecs?|ms|milliseconds?)$",
    re.IGNORECASE,
)
_RELATIVE = re.compile(
    r"^(?:next|last|this)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month|quarter|year)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizationContext:
    reference_time_ms: int | None = None
    tz: str = "UTC"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _start_of(year: int, month: int, day: int, tz: ZoneInfo) -> datetime:
    return datetime(year, month, day, tzinfo=tz)


def _add_months(moment: datetime, months: int) -> datetime:
    month = moment.month - 1 + months
    year = moment.year + month // 12
    month = month % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def normalize_temporal(
    kind: EntityKind, surface: str, ctx: NormalizationContext
) -> DateRangeValue | DurationValue | None:
    text = (surface or "").strip()
    if not text:
        return None
    if kind is EntityKind.DURATION or _DURATION.match(text):
        return _duration(text)
    quarter = _QUARTER.match(text.replace(" ", ""))
    if quarter is None:
        quarter = _QUARTER.match(re.sub(r"\s+", " ", text))
    if quarter:
        return _quarter(int(quarter.group("q")), quarter.group("y"), ctx)
    fiscal = _FISCAL_YEAR.match(text)
    if fiscal:
        year = int(fiscal.group("y"))
        return _year(year + 2000 if year < 100 else year, ctx)
    period = _RELATIVE_PERIOD.match(text)
    if period:
        return _period(period.group("which").casefold(), period.group("unit").casefold(), ctx)
    interval = _iso_interval(text, kind, ctx) if "/" in text else None
    if interval:
        return interval
    iso = _ISO_DATE.match(text)
    if iso:
        return _iso(iso, kind, ctx)
    month_year = _MONTH_YEAR.match(text)
    if month_year:
        return _month(int(month_year.group("y")), _MONTHS[month_year.group("mon").casefold()], ctx)
    month_day = _MONTH_DAY.match(text)
    if month_day:
        year = month_day.group("y")
        month = _MONTHS[month_day.group("mon").casefold()]
        day = int(month_day.group("d"))
        if not year:
            return DateRangeValue(timex=f"XXXX-{month:02d}-{day:02d}", ambiguous=True, granularity="day")
        return _day(int(year), month, day, ctx, anchored=False)
    day_month = _DAY_MONTH.match(text)
    if day_month:
        year = day_month.group("y")
        month = _MONTHS[day_month.group("mon").casefold()]
        day = int(day_month.group("d"))
        if not 1 <= day <= 31:
            return None
        if not year:
            return DateRangeValue(timex=f"XXXX-{month:02d}-{day:02d}", ambiguous=True, granularity="day")
        return _day(int(year), month, day, ctx, anchored=False)
    if _RELATIVE.match(text):
        return _relative(text, ctx)
    return _dateparser(text, kind, ctx)


def _quarter(q: int, year_raw: str, ctx: NormalizationContext) -> DateRangeValue:
    year = int(year_raw)
    if year < 100:
        year += 2000
    start_month = (q - 1) * 3 + 1
    tz = _zone(ctx.tz)
    start = _start_of(year, start_month, 1, tz)
    end = _add_months(start, 3)
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=f"{year}-Q{q}",
        granularity="quarter",
        anchored=True,
        anchor_source="explicit",
    )


def _iso_interval(text: str, kind: EntityKind, ctx: NormalizationContext) -> DateRangeValue | None:
    """ISO 8601 ``start/end``; a date-only end covers that whole day."""
    left, _, right = text.partition("/")
    first_match, last_match = _ISO_DATE.match(left.strip()), _ISO_DATE.match(right.strip())
    if first_match is None or last_match is None:
        return None
    first, last = _iso(first_match, kind, ctx), _iso(last_match, kind, ctx)
    if first is None or last is None or last.end_ms <= first.start_ms:
        return None
    minute = "minute" in (first.granularity, last.granularity)
    return DateRangeValue(
        start_ms=first.start_ms,
        end_ms=last.end_ms,
        timex=f"{first.timex}/{last.timex}",
        granularity="minute" if minute else "day",
        anchored=True,
        anchor_source="explicit",
    )


def _offset(raw: str | None) -> timezone | None:
    if not raw:
        return None
    if raw == "Z":
        return UTC
    sign = -1 if raw[0] == "-" else 1
    digits = raw[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))


def _iso(match: re.Match[str], kind: EntityKind, ctx: NormalizationContext) -> DateRangeValue | None:
    year, month, day = int(match.group("y")), int(match.group("m")), int(match.group("d"))
    hour, minute = match.group("H"), match.group("M")
    if hour is not None:
        try:
            start = datetime(
                year, month, day, int(hour), int(minute),
                tzinfo=_offset(match.group("off")) or _zone(ctx.tz),
            )
        except ValueError:
            return None
        end = start + timedelta(minutes=1)
        return DateRangeValue(
            start_ms=_ms(start),
            end_ms=_ms(end),
            timex=start.strftime("%Y-%m-%dT%H:%M"),
            granularity="minute",
            anchored=True,
            anchor_source="explicit",
        )
    return _day(year, month, day, ctx, anchored=False, kind=kind)


def _year(year: int, ctx: NormalizationContext) -> DateRangeValue:
    tz = _zone(ctx.tz)
    return DateRangeValue(
        start_ms=_ms(_start_of(year, 1, 1, tz)),
        end_ms=_ms(_start_of(year + 1, 1, 1, tz)),
        timex=f"{year}",
        granularity="year",
        anchored=True,
        anchor_source="explicit",
    )


def _base(ctx: NormalizationContext, tz: ZoneInfo) -> tuple[datetime, str]:
    if ctx.reference_time_ms:
        return datetime.fromtimestamp(ctx.reference_time_ms / 1000, tz=UTC).astimezone(tz), "record"
    return datetime.now(tz), "now"


def _period(which: str, unit: str, ctx: NormalizationContext) -> DateRangeValue:
    """'last week' is the whole previous week, not one day of it."""
    tz = _zone(ctx.tz)
    base, source = _base(ctx, tz)
    step = {"next": 1, "last": -1, "this": 0}[which]
    if unit == "week":
        monday = _start_of(base.year, base.month, base.day, tz) - timedelta(days=base.weekday())
        start = monday + timedelta(weeks=step)
        end = start + timedelta(weeks=1)
        iso_year, iso_week, _ = start.isocalendar()
        timex = f"{iso_year}-W{iso_week:02d}"
    elif unit == "year":
        start = _start_of(base.year + step, 1, 1, tz)
        end = _start_of(base.year + step + 1, 1, 1, tz)
        timex = f"{start.year}"
    else:
        months = 3 if unit == "quarter" else 1
        first = (base.month - 1) // months * months + 1
        start = _add_months(_start_of(base.year, first, 1, tz), step * months)
        end = _add_months(start, months)
        timex = f"{start.year}-Q{(start.month - 1) // 3 + 1}" if unit == "quarter" else f"{start.year}-{start.month:02d}"
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=timex,
        granularity=unit,  # type: ignore[arg-type]
        anchored=True,
        anchor_source=source,
    )


def _month(year: int, month: int, ctx: NormalizationContext) -> DateRangeValue:
    tz = _zone(ctx.tz)
    start = _start_of(year, month, 1, tz)
    end = _add_months(start, 1)
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=f"{year}-{month:02d}",
        granularity="month",
        anchored=True,
        anchor_source="explicit",
    )


def _day(
    year: int, month: int, day: int, ctx: NormalizationContext, *, anchored: bool, kind: EntityKind = EntityKind.DATE,
) -> DateRangeValue | None:
    tz = _zone(ctx.tz)
    try:
        start = _start_of(year, month, day, tz)
    except ValueError:
        return None
    end = start + timedelta(days=1)
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=f"{year:04d}-{month:02d}-{day:02d}",
        granularity="day" if kind is not EntityKind.DATE_TIME else "minute",
        anchored=anchored,
        anchor_source="explicit" if not anchored else "record",
    )


_DURATION_UNITS = {
    "ms": "millisecond", "msec": "millisecond", "msecs": "millisecond",
    "milliseconds": "millisecond", "millisecond": "millisecond",
    "sec": "second", "secs": "second", "min": "minute", "mins": "minute", "hr": "hour", "hrs": "hour",
}


def _plain(number: float) -> str:
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _duration(text: str) -> DurationValue | None:
    match = _DURATION.match(text.strip())
    if not match:
        return None
    amount = float(match.group("n"))
    unit = _DURATION_UNITS.get(match.group("u").casefold(), match.group("u").casefold().rstrip("s"))
    if unit == "millisecond":
        # ISO 8601 has no millisecond designator: fractional seconds.
        seconds = amount / 1000
        return DurationValue(iso=f"PT{_plain(seconds)}S", seconds=seconds, approx=False)
    seconds_map = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
    if unit in seconds_map:
        seconds = amount * seconds_map[unit]
        iso_unit = {"second": "S", "minute": "M", "hour": "H", "day": "D", "week": "W"}[unit]
        prefix = "PT" if unit in {"second", "minute", "hour"} else "P"
        number = int(amount) if amount == int(amount) else amount
        return DurationValue(iso=f"{prefix}{number}{iso_unit}", seconds=seconds, approx=False)
    # Months and years are calendar-relative; do not pretend they are exact seconds.
    iso = f"P{int(amount) if amount == int(amount) else amount}{'M' if unit == 'month' else 'Y'}"
    return DurationValue(iso=iso, seconds=None, approx=True)


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _relative(text: str, ctx: NormalizationContext) -> DateRangeValue | None:
    parts = text.casefold().split()
    if len(parts) == 2 and parts[0] in {"next", "last", "this"} and parts[1] in _WEEKDAYS:
        return _weekday(parts[0], _WEEKDAYS[parts[1]], ctx)
    parsed = _dateparser(text, EntityKind.DATE, ctx)
    if parsed is None or parsed.start_ms is None:
        return None
    parsed.anchored = True
    parsed.anchor_source = "record" if ctx.reference_time_ms else "now"
    return parsed


def _weekday(which: str, weekday: int, ctx: NormalizationContext) -> DateRangeValue:
    tz = _zone(ctx.tz)
    if ctx.reference_time_ms:
        base = datetime.fromtimestamp(ctx.reference_time_ms / 1000, tz=UTC).astimezone(tz)
        source = "record"
    else:
        base = datetime.now(tz)
        source = "now"
    start = _start_of(base.year, base.month, base.day, tz)
    delta = (weekday - start.weekday()) % 7
    if which == "next":
        delta = 7 if delta == 0 else delta
    elif which == "last":
        delta = -7 if delta == 0 else delta - 7
    start = start + timedelta(days=delta)
    end = start + timedelta(days=1)
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=start.strftime("%Y-%m-%d"),
        granularity="day",
        anchored=True,
        anchor_source=source,
    )


def _dateparser(text: str, kind: EntityKind, ctx: NormalizationContext) -> DateRangeValue | None:
    try:
        import dateparser
    except ImportError:
        return None
    tz = _zone(ctx.tz)
    if ctx.reference_time_ms:
        base = datetime.fromtimestamp(ctx.reference_time_ms / 1000, tz=UTC).astimezone(tz)
    else:
        base = datetime.now(tz)
    parsed = dateparser.parse(
        text,
        settings={
            "RELATIVE_BASE": base.replace(tzinfo=None),
            "RETURN_AS_TIMEZONE_AWARE": False,
            "STRICT_PARSING": True,
            "PREFER_DATES_FROM": "current_period",
        },
    )
    if parsed is None:
        return None
    start = parsed.replace(tzinfo=tz)
    if kind is EntityKind.DATE_TIME or (parsed.hour or parsed.minute):
        end = start + timedelta(minutes=1)
        granularity = "minute"
    else:
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        granularity = "day"
    return DateRangeValue(
        start_ms=_ms(start),
        end_ms=_ms(end),
        timex=start.isoformat(),
        granularity=granularity,  # type: ignore[arg-type]
        anchored=True,
        anchor_source="record" if ctx.reference_time_ms else "now",
    )
