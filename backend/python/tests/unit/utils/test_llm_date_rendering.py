"""Dates rendered to the model carry a weekday computed in code.

The answer prompt asks for dates like "Mon, Jul 27, 3:45 PM PDT" in the
user's time zone, but record headers used to carry only
"2026-07-27 03:00:00 UTC", so the model worked the weekday out itself and
often got it wrong. Near midnight the weekday also depends on the zone:
Monday 03:00 UTC is Sunday evening in California.
"""

import asyncio
from collections.abc import Iterator

import pytest

from app.utils.time_conversion import (
    format_epoch_ms_for_llm,
    reset_llm_time_zone,
    set_llm_time_zone,
)

MON_0300_UTC = 1785121200000  # 2026-07-27 03:00:00 UTC, a Monday
MON_2245_UTC = 1785192300000  # 2026-07-27 22:45:00 UTC
MON_0000_UTC = 1785110400000  # 2026-07-27 00:00:00 UTC
MON_1205_UTC = 1785153900000  # 2026-07-27 12:05:00 UTC
NEW_YEAR_0530_UTC = 1767245400000  # 2026-01-01 05:30:00 UTC, a Thursday


@pytest.fixture
def zone() -> Iterator[object]:
    tokens = []

    def _set(name: str | None) -> None:
        tokens.append(set_llm_time_zone(name))

    yield _set
    for token in reversed(tokens):
        reset_llm_time_zone(token)


class TestFormatEpochMsForLlm:
    def test_without_a_zone_the_weekday_is_taken_in_utc(self) -> None:
        assert format_epoch_ms_for_llm(MON_0300_UTC) == (
            "2026-07-27 03:00:00 UTC (Mon, Jul 27, 2026, 3:00 AM UTC)"
        )

    def test_late_utc_monday_is_sunday_evening_in_california(self, zone) -> None:
        zone("America/Los_Angeles")
        assert format_epoch_ms_for_llm(MON_0300_UTC) == (
            "2026-07-27 03:00:00 UTC (Sun, Jul 26, 2026, 8:00 PM PDT)"
        )

    def test_monday_evening_utc_is_tuesday_in_india(self, zone) -> None:
        zone("Asia/Kolkata")
        assert format_epoch_ms_for_llm(MON_2245_UTC).endswith("(Tue, Jul 28, 2026, 4:15 AM IST)")

    def test_year_changes_with_the_zone_too(self, zone) -> None:
        zone("America/Los_Angeles")
        assert format_epoch_ms_for_llm(NEW_YEAR_0530_UTC).endswith(
            "(Wed, Dec 31, 2025, 9:30 PM PST)"
        )

    def test_midnight_and_noon_use_twelve(self) -> None:
        assert format_epoch_ms_for_llm(MON_0000_UTC).endswith("(Mon, Jul 27, 2026, 12:00 AM UTC)")
        assert format_epoch_ms_for_llm(MON_1205_UTC).endswith("(Mon, Jul 27, 2026, 12:05 PM UTC)")

    @pytest.mark.parametrize("name", ["Not/AZone", "   ", "", None])
    def test_unknown_or_blank_zone_falls_back_to_utc(self, zone, name) -> None:
        zone(name)
        assert format_epoch_ms_for_llm(MON_0300_UTC).endswith("(Mon, Jul 27, 2026, 3:00 AM UTC)")


class TestZoneIsScopedToTheRun:
    async def test_zone_set_in_a_task_does_not_leak_out(self) -> None:
        async def _run() -> str:
            set_llm_time_zone("America/Los_Angeles")
            return format_epoch_ms_for_llm(MON_0300_UTC)

        inside = await asyncio.create_task(_run())

        assert inside.endswith("(Sun, Jul 26, 2026, 8:00 PM PDT)")
        assert format_epoch_ms_for_llm(MON_0300_UTC).endswith("(Mon, Jul 27, 2026, 3:00 AM UTC)")

    async def test_tasks_started_by_the_run_see_its_zone(self) -> None:
        async def _tool() -> str:
            return format_epoch_ms_for_llm(MON_0300_UTC)

        async def _run() -> str:
            set_llm_time_zone("America/Los_Angeles")
            return await asyncio.create_task(_tool())

        assert (await asyncio.create_task(_run())).endswith("(Sun, Jul 26, 2026, 8:00 PM PDT)")
