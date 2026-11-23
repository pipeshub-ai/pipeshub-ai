"""The shared sweep: how long the loop waits after each kind of tick, and the orgs a tick goes through."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.core.org_context import current_org_id
from app.modules.indexing.connector_sweep import (
    ConnectorSweep,
    PageResult,
    SweepFields,
    run_connector_sweep_loop,
)


class _Lock:
    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _Sweep:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.lock = None

    async def tick(self) -> str:
        return self.outcomes.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("outcome", "interval"), [("page", 5.0), ("waiting", 5.0), ("deferred", 1800.0)])
async def test_the_wait_after_a_tick_follows_its_outcome(outcome: str, interval: float) -> None:
    sleep = AsyncMock()

    async def make_lock() -> _Lock:
        return _Lock()

    sweep = _Sweep([outcome, "idle"])

    async def make_sweep(_lock: _Lock) -> _Sweep:
        return sweep

    await run_connector_sweep_loop(
        logger=logging.getLogger("sweep-test"),
        name="sweep-test",
        make_lock=make_lock,
        make_sweep=make_sweep,
        startup_grace_seconds=0.0,
        busy_interval_seconds=5.0,
        idle_interval_seconds=600.0,
        deferred_interval_seconds=1800.0,
        error_interval_seconds=60.0,
        sleep=sleep,
    )

    assert [c.args[0] for c in sleep.await_args_list] == [0.0, interval]


ORG_A, ORG_B = "org-a", "org-b"


class _Leader(_Lock):
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True


class _Graph:
    """Apps of two orgs. ``scoped`` answers the bound org's apps only (PostgreSQL); otherwise every org's (Neo4j, ArangoDB)."""

    def __init__(self, *, scoped: bool) -> None:
        self.scoped = scoped
        self.apps = {
            "a1": {"_key": "a1", "orgId": ORG_A},
            "a2": {"_key": "a2", "orgId": ORG_A},
            "b1": {"_key": "b1", "orgId": ORG_B},
        }
        self.broken_orgs: set[str] = set()

    async def get_all_orgs(self, **_kw: object) -> list[dict]:
        return [{"_key": ORG_A}, {"_key": ORG_B}]

    def _read(self, *, raise_on_error: bool) -> list[dict]:
        org = current_org_id()
        if org in self.broken_orgs:
            # As the stores answer a failed read: PostgreSQL raises; Neo4j and ArangoDB log and answer [] unless asked to raise.
            if self.scoped or raise_on_error:
                raise RuntimeError("graph did not answer")
            return []
        return [dict(a) for a in self.apps.values() if not self.scoped or a["orgId"] == org]

    async def get_documents_paginated(self, collection: str, skip: int = 0, limit: int = 50, filters: object = None,
                                      sort_field: str | None = None, transaction: object = None, *, raise_on_error: bool = False,
                                      after_key: str | None = None, return_fields: object = None) -> list[dict]:
        assert sort_field == "_key" and not skip and not filters
        rows = sorted(self._read(raise_on_error=raise_on_error), key=lambda a: a["_key"])
        return [a for a in rows if after_key is None or a["_key"] > after_key][:limit]

    async def page_records_for_vector_membership_backfill(self, app_key: str, after_key: object, limit: int) -> list[dict]:
        assert self.apps[app_key]["orgId"] == current_org_id()
        return []

    async def update_node(self, key: str, collection: str, updates: dict) -> bool:
        assert self.apps[key]["orgId"] == current_org_id()
        self.apps[key].update(updates)
        return True


class _Probe(ConnectorSweep):
    name = "probe"
    version = "v1"
    fields = SweepFields(state="probeState", after_key="probeAfter", attempts="probeAttempts",
                         exhausted="probeExhausted", failures="probeFailures", counters=())
    max_attempts = 1
    give_up_consequence = "nothing"

    def __init__(self, graph: _Graph) -> None:
        super().__init__(logger=logging.getLogger("sweep-test"), graph_provider=graph, lock=_Leader(), page_size=10)
        self.visits: list[tuple[str, str]] = []

    async def process_page(self, app: dict, app_key: str, rows: list[dict]) -> PageResult:
        self.visits.append((app_key, current_org_id()))
        return PageResult()


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [True, False], ids=["one-org-reads", "every-org-reads"])
async def test_each_orgs_connectors_are_swept_under_that_org(scoped: bool) -> None:
    graph = _Graph(scoped=scoped)
    sweep = _Probe(graph)

    outcomes = [await sweep.tick() for _ in range(4)]

    assert outcomes == ["page", "page", "page", "idle"]
    assert sweep.visits == [("a1", ORG_A), ("a2", ORG_A), ("b1", ORG_B)]
    assert all(a["probeState"] == "v1" for a in graph.apps.values())


@pytest.mark.asyncio
async def test_a_failing_org_does_not_stop_the_others_and_never_reads_as_done() -> None:
    graph = _Graph(scoped=True)
    graph.broken_orgs.add(ORG_A)
    sweep = _Probe(graph)

    assert await sweep.tick() == "page"
    assert sweep.visits == [("b1", ORG_B)]
    with pytest.raises(RuntimeError, match="1 org"):
        await sweep.tick()

    graph.broken_orgs.clear()
    assert [await sweep.tick() for _ in range(3)] == ["page", "page", "idle"]
    assert [k for k, _ in sweep.visits] == ["b1", "a1", "a2"]


@pytest.mark.asyncio
async def test_no_connector_in_any_org_is_no_apps() -> None:
    graph = _Graph(scoped=True)
    graph.apps.clear()
    assert await _Probe(graph).tick() == "no_apps"


@pytest.mark.asyncio
async def test_a_failed_connector_read_that_answers_empty_never_reads_as_done() -> None:
    """Neo4j and ArangoDB answer a failed read with no rows: a later org's failed read must not end the sweep as idle."""
    graph = _Graph(scoped=False)
    graph.apps["a1"]["probeState"] = graph.apps["a2"]["probeState"] = "v1"
    graph.broken_orgs.add(ORG_B)
    sweep = _Probe(graph)

    with pytest.raises(RuntimeError, match="1 org"):
        await sweep.tick()

    graph.broken_orgs.clear()
    assert [await sweep.tick() for _ in range(2)] == ["page", "idle"]
    assert sweep.visits == [("b1", ORG_B)]
