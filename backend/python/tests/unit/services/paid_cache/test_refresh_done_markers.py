"""The completion marker of a refresh, by request id: the second copy of a fact the record carries (``refreshDoneId``).

A marker that cannot be read is neither a hit nor a miss.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.locks import InProcessLocks

ORG, REC = "org-1", "r1"


def _cache(locks: object | None = None) -> PaidResultCache:
    return PaidResultCache(MagicMock(), locks or InProcessLocks())  # type: ignore[arg-type]


async def test_a_marked_refresh_reads_as_done_and_an_unmarked_one_as_open() -> None:
    cache = _cache()
    assert await cache.refresh_state(ORG, REC, "A") == "open"
    assert await cache.mark_refresh_done(ORG, REC, "A") is True
    assert await cache.refresh_state(ORG, REC, "A") == "done"
    assert await cache.refresh_state(ORG, REC, "B") == "open"                  # by identity: another request is not covered
    assert await cache.refresh_state(ORG, "other-record", "A") == "open"


async def test_a_marker_that_cannot_be_read_is_unavailable_not_open_and_one_that_cannot_be_written_says_so() -> None:
    class Down(InProcessLocks):
        async def is_marked(self, name: str) -> bool:
            raise ConnectionError("redis down")

        async def mark(self, name: str, ttl_ms: int, value: str = "1") -> None:
            raise ConnectionError("redis down")

    cache = _cache(Down())
    assert await cache.refresh_state(ORG, REC, "A") == "unavailable"
    assert await cache.mark_refresh_done(ORG, REC, "A") is False
