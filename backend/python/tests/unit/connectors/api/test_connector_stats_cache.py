import asyncio

import pytest

from app.connectors.api import connector_stats_cache as cache


class Graph:
    """Counts like the provider: one answer per call, numbered so a reused answer shows."""

    def __init__(self, gate: asyncio.Event | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.gate, self.fail = gate, fail

    async def get_connector_stats(self, org_id: str, connector_id: str) -> dict:
        self.calls.append((org_id, connector_id))
        if self.gate:
            await self.gate.wait()
        if self.fail:
            return {"success": False, "message": "down", "data": None}
        return {"success": True, "data": {"orgId": org_id, "connectorId": connector_id, "stats": {"total": len(self.calls)}}}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    return now


async def test_concurrent_calls_share_one_count_and_get_their_own_copy(clock: list[float]) -> None:
    gate = asyncio.Event()
    g = Graph(gate)
    calls = [asyncio.ensure_future(cache.connector_stats(g, "o1", "c1")) for _ in range(20)]
    await asyncio.sleep(0)
    gate.set()
    out = await asyncio.gather(*calls)
    assert g.calls == [("o1", "c1")] and all(o == out[0] for o in out)
    out[0]["data"]["stats"]["total"] = 99
    assert out[1]["data"]["stats"]["total"] == 1


async def test_an_answer_is_reused_for_ttl_seconds_then_counted_again(clock: list[float]) -> None:
    g = Graph()
    assert (await cache.connector_stats(g, "o1", "c1"))["data"]["stats"]["total"] == 1
    clock[0] += cache.TTL_S - 0.01
    assert (await cache.connector_stats(g, "o1", "c1"))["data"]["stats"]["total"] == 1       # within the window: the same answer
    clock[0] += 0.01
    assert (await cache.connector_stats(g, "o1", "c1"))["data"]["stats"]["total"] == 2       # past it: a new count
    assert len(g.calls) == 2


async def test_orgs_and_connectors_never_share_an_answer(clock: list[float]) -> None:
    g = Graph()
    a = await cache.connector_stats(g, "o1", "c1")
    b = await cache.connector_stats(g, "o2", "c1")
    c = await cache.connector_stats(g, "o1", "c2")
    assert g.calls == [("o1", "c1"), ("o2", "c1"), ("o1", "c2")]
    assert (a["data"]["orgId"], b["data"]["orgId"], c["data"]["connectorId"]) == ("o1", "o2", "c2")
    assert (await cache.connector_stats(Graph(), "o1", "c1"))["data"]["stats"]["total"] == 1     # another provider counts for itself


async def test_a_failed_count_is_not_kept(clock: list[float]) -> None:
    g = Graph(fail=True)
    assert (await cache.connector_stats(g, "o1", "c1"))["success"] is False
    g.fail = False
    assert (await cache.connector_stats(g, "o1", "c1"))["success"] is True and len(g.calls) == 2


async def test_a_count_that_raises_is_not_kept(clock: list[float]) -> None:
    class Broken(Graph):
        async def get_connector_stats(self, org_id: str, connector_id: str) -> dict:
            self.calls.append((org_id, connector_id))
            raise RuntimeError("store down")
    g = Broken()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cache.connector_stats(g, "o1", "c1")
    assert len(g.calls) == 2


async def test_the_cache_is_bounded_oldest_first(clock: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_KEYS", 3)
    g = Graph()
    for c in ("c1", "c2", "c3", "c4"):
        await cache.connector_stats(g, "o1", c)
    assert list(cache._entries[g]) == [("o1", "c2"), ("o1", "c3"), ("o1", "c4")]
