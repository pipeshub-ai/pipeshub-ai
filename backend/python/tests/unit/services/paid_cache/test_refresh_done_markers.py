"""The completion markers of a refresh: per request id, and the time of the newest request that ran on the record.

A late event of an older request is refused once a newer one has run; a marker that cannot be read is neither a hit nor a miss.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.locks import InProcessLocks

ORG, REC = "org-1", "r1"


def _cache(locks: object | None = None) -> PaidResultCache:
    return PaidResultCache(MagicMock(), locks or InProcessLocks())  # type: ignore[arg-type]


async def test_a_marked_refresh_reads_as_done_and_an_unmarked_one_as_open() -> None:
    cache = _cache()
    assert await cache.refresh_state(ORG, REC, "A", 100) == "open"
    assert await cache.mark_refresh_done(ORG, REC, "A", 100) is True
    assert await cache.refresh_state(ORG, REC, "A", 100) == "done"
    assert await cache.refresh_state(ORG, "other-record", "A", 100) == "open"


async def test_a_late_event_of_an_older_request_is_done_once_a_newer_one_ran() -> None:
    cache = _cache()
    await cache.mark_refresh_done(ORG, REC, "B", 200)
    assert await cache.refresh_state(ORG, REC, "A", 100) == "done"             # A was requested before B: refused
    assert await cache.refresh_state(ORG, REC, "C", 300) == "open"             # a newer request still runs
    await cache.mark_refresh_done(ORG, REC, "A", 100)                          # an older completion never lowers the time
    assert await cache.refresh_state(ORG, REC, "A2", 150) == "done"


async def test_a_marker_that_cannot_be_read_is_unavailable_not_open_and_one_that_cannot_be_written_says_so() -> None:
    class Down(InProcessLocks):
        async def is_marked(self, name: str) -> bool:
            raise ConnectionError("redis down")

        async def mark(self, name: str, ttl_ms: int, value: str = "1") -> None:
            raise ConnectionError("redis down")

    cache = _cache(Down())
    assert await cache.refresh_state(ORG, REC, "A", 100) == "unavailable"
    assert await cache.mark_refresh_done(ORG, REC, "A", 100) is False


@pytest.mark.parametrize("requested_at", [None, 5])
async def test_without_a_request_time_only_the_id_marker_counts(requested_at: int | None) -> None:
    cache = _cache()
    await cache.mark_refresh_done(ORG, REC, "A", requested_at)
    assert await cache.refresh_state(ORG, REC, "A", None) == "done"
    assert await cache.refresh_state(ORG, REC, "B", None) == "open"
