"""The dateparser fallback gives what a direct ``dateparser.parse`` call gives,
without registering new dateparser settings for every record's reference time."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.modules.named_entities.normalizers import dates

dateparser = pytest.importorskip("dateparser")

TEXTS = [
    "tomorrow",
    "yesterday",
    "today",
    "tomorrow 14:00 UTC",
    "3 days ago",
    "in 2 weeks",
    "Fri, 5 Jun 2026 09:12:00 -0700",
    "Thu, Sep 3, 2026 at 9:24 AM",
    "Tue 2026-06-23 10:00am PT",
    "09/18-09",
    "3150004380",
    "15 de marzo de 2026",
    "Friday",
    "June",
    "14:00 UTC",
    "EOD Wed",
    "Q4",
    "end of week",
]
# dateparser takes a naive base: the record's time in the deployment zone.
BASES = [
    moment.replace(tzinfo=None)
    for moment in (datetime(2026, 1, 1, tzinfo=UTC), datetime(2024, 2, 29, 23, 59, tzinfo=UTC), datetime(2031, 7, 15, 6, 30, tzinfo=UTC))
]


def _direct(text: str, base: datetime) -> datetime | None:
    return dateparser.parse(
        text,
        settings={
            "RELATIVE_BASE": base,
            "RETURN_AS_TIMEZONE_AWARE": False,
            "STRICT_PARSING": True,
            "PREFER_DATES_FROM": "current_period",
        },
    )


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("text", TEXTS)
def test_same_result_as_a_direct_call(text: str, base: datetime) -> None:
    dates._parse_date.cache_clear()
    assert dates._parse_date(text, base) == _direct(text, base)


def _registered_settings() -> int:
    registry = getattr(dateparser.conf.Settings, "__registry_dict", None)
    assert registry is not None, "dateparser's settings registry moved; this test no longer checks it"
    return len(registry)


def test_new_reference_times_register_no_new_settings() -> None:
    ctx = dates.NormalizationContext(reference_time_ms=1767225600000, tz="UTC")
    dates._dateparser("tomorrow", dates.EntityKind.DATE, ctx)
    before = _registered_settings()

    for day in range(1, 40):
        ctx = dates.NormalizationContext(reference_time_ms=1767225600000 + day * 86_400_000, tz="UTC")
        dates._dateparser("Friday", dates.EntityKind.DATE, ctx)

    assert _registered_settings() == before


def test_concurrent_reference_times_do_not_leak_into_each_other() -> None:
    dates._parse_date.cache_clear()
    cases = [(text, base) for text in ("tomorrow", "3 days ago", "yesterday") for base in BASES] * 20

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda case: dates._parse_date_uncached(*case), cases))

    assert results == [_direct(text, base) for text, base in cases]
