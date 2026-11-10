"""The rewrite job's scan: it publishes before it reads, and retries a partial answer from the scroll or from the count."""
from __future__ import annotations

import pytest

from app.services.vector_db import scope_rewrite
from app.services.vector_db.errors import PartialResult
from app.services.vector_db.models import FilterExpression, VectorPoint
from app.services.vector_db.scope_rewrite import ScopeRewriteJob
from tests.support.in_memory_vector_db import InMemoryVectorDBService

ORG, COLL = "o" * 24, "c"


class _Store(InMemoryVectorDBService):
    """A store whose `count_points` fails the first `count_fails` times, and whose points in `unpublished` are seen only after `make_searchable`."""

    def __init__(self, count_fails: int = 0) -> None:
        super().__init__()
        self.count_fails, self.count_calls, self.unpublished = count_fails, 0, {}

    async def count_points(self, collection_name: str, count_filter: FilterExpression) -> int:
        self.count_calls += 1
        if self.count_calls <= self.count_fails:
            raise PartialResult("a shard failed")
        return await super().count_points(collection_name, count_filter)

    async def make_searchable(self, collection_name: str) -> None:
        self.collections[collection_name].update(self.unpublished)
        self.unpublished = {}


def _point(pid: str) -> VectorPoint:
    return VectorPoint(id=pid, dense_vector=[1.0], payload={"orgId": ORG, "metadata": {"orgId": ORG, "virtualRecordId": "v1"}})


async def _job(store: _Store, *, late: bool = False) -> ScopeRewriteJob:
    store.collections.setdefault(COLL, {})
    (store.unpublished if late else store.collections[COLL])["p1"] = _point("p1")
    return ScopeRewriteJob(None, store, None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope_rewrite, "SCAN_RETRY_SECONDS", 0)


async def test_a_point_not_yet_searchable_is_scanned() -> None:
    job = await _job(_Store(), late=True)
    assert list(await job._scan(ORG, COLL, ["v1"])) == ["p1"]


async def test_a_count_that_fails_once_is_retried() -> None:
    store = _Store(count_fails=1)
    job = await _job(store)
    assert list(await job._scan(ORG, COLL, ["v1"])) == ["p1"] and store.count_calls == 2


async def test_a_count_that_keeps_failing_fails_the_scan_after_the_attempts() -> None:
    store = _Store(count_fails=99)
    job = await _job(store)
    with pytest.raises(PartialResult):
        await job._scan(ORG, COLL, ["v1"])
    assert store.count_calls == scope_rewrite.SCAN_ATTEMPTS


async def test_a_store_that_does_not_declare_count_cannot_be_rewritten() -> None:
    from app.services.vector_db.interface.vector_db import IVectorDBService

    with pytest.raises(NotImplementedError):
        await IVectorDBService.count_points(_Store(), COLL, FilterExpression())
