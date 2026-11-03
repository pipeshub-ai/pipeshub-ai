"""The scope-field back-fill: what it writes, that it stops and resumes, that it is bounded, org-scoped and fails closed, and that
it cannot leave a point with fields an earlier read of the graph gave when a live write or a graph change raced it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import pytest

from app.core import org_context as oc
from app.services.permissions.mode import ENV_SCOPE_FIELDS
from app.services.permissions.scope_fields import SCOPE_KEYS, ScopeFields
from app.services.vector_db.membership import STALE_SCOPE, sync_vector_membership
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.scope_backfill import (
    DONE,
    FAILED,
    RUNNING,
    ScopeBackfill,
    ScopeFieldsOff,
    UnknownConnector,
)
from tests.support.in_memory_vector_db import (
    InMemoryVectorDBService as InMemoryVectorDB,
)

from .stamp_world import ORG
from .store_harness import Loaded, expected, load, read_stamps
from .test_scope_projector import CASES

COLL = "scope_bf"
WORLD = "derived:SCOPES-ADV-01"
log = logging.getLogger("t")


class Killed(BaseException):
    """A process killed mid-run: not an Exception, so nothing in the back-fill may swallow it."""


@pytest.fixture(autouse=True)
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")


async def setup(page_size: int = 100) -> tuple[InMemoryVectorDB, Loaded, ScopeBackfill, str]:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES[WORLD]})
    return vdb, loaded, ScopeBackfill(loaded.graph, vdb, loaded.locator, log, page_size=page_size), loaded.apps[0]


def keys_of(vdb) -> list[set[str]]:
    return [set(SCOPE_KEYS) & set(p.payload) for p in vdb.collections[COLL].values()]


async def test_the_flag_off_refuses_and_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SCOPE_FIELDS, raising=False)
    vdb, loaded, job, app = await setup()
    loaded.graph.stamps.failure = AssertionError("no graph read while the fields are off")
    with pytest.raises(ScopeFieldsOff):
        await job.run_connector(ORG, app)
    assert not any(keys_of(vdb)) and not loaded.graph.app_docs


async def test_every_point_of_the_connector_gets_the_projector_fields_and_membership_is_untouched() -> None:
    vdb, loaded, job, app = await setup()
    before = {i: list(p.payload["connectorIds"]) for i, p in vdb.collections[COLL].items()}
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.stale == 0
    want = await expected(loaded)
    got = await read_stamps(vdb, loaded)
    assert {v: f for v, f in got.items() if f is not None} == {v: f for v, f in want.items() if f is not None and got[v] is not None}
    assert sum(f is not None for f in got.values()) >= 5
    assert {i: list(q.payload["connectorIds"]) for i, q in vdb.collections[COLL].items()} == before


async def test_a_finished_connector_is_not_read_again_but_a_restart_is() -> None:
    _, loaded, job, app = await setup()
    await job.run_connector(ORG, app)
    real = loaded.graph.stamps.record_page

    async def never(*a: object) -> list[str]:
        raise AssertionError("records listed after done")

    loaded.graph.stamps.record_page = never  # type: ignore[method-assign]
    assert (await job.run_connector(ORG, app)).state == DONE
    loaded.graph.stamps.record_page = real  # type: ignore[method-assign]
    assert (await job.run_connector(ORG, app, restart=True)).state == DONE


async def test_a_run_is_bounded_and_the_next_run_goes_on_where_the_last_stopped() -> None:
    vdb, loaded, job, app = await setup(page_size=2)
    first = await job.run_connector(ORG, app, max_pages=1)
    assert first.state == RUNNING and first.records == 2 and first.after
    seen: list[str | None] = []
    real = loaded.graph.stamps.record_page

    async def spy(org: str, a: str, after: str | None, limit: int) -> list[str]:
        seen.append(after)
        return await real(org, a, after, limit)

    loaded.graph.stamps.record_page = spy  # type: ignore[method-assign]
    last = await job.run_connector(ORG, app)
    assert seen[0] == first.after and last.state == DONE and last.records > first.records


async def test_a_killed_run_resumes_at_the_last_saved_page_and_ends_with_the_same_fields() -> None:
    vdb, loaded, job, app = await setup(page_size=2)
    real, calls = vdb.set_payload, 0

    async def dying(*a, **k) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise Killed
        await real(*a, **k)

    vdb.set_payload = dying  # type: ignore[method-assign]
    with pytest.raises(Killed):
        await job.run_connector(ORG, app)
    saved = loaded.graph.app_docs[app]["scopeBackfillAfter"]
    assert saved and loaded.graph.app_docs[app]["scopeBackfillState"] == RUNNING
    vdb.set_payload = real  # type: ignore[method-assign]
    assert (await job.run_connector(ORG, app)).state == DONE
    want = await expected(loaded)
    got = await read_stamps(vdb, loaded)
    assert all(got[v] == f for v, f in want.items() if f is not None and got[v] is not None)


async def test_a_graph_that_cannot_be_read_marks_the_points_stale_and_never_guesses() -> None:
    vdb, loaded, job, app = await setup()
    loaded.graph.stamps.failure = None
    real = loaded.graph.stamps.nodes
    state = {"n": 0}

    async def flaky(org: str, ids: frozenset[str]) -> dict:
        state["n"] += 1
        if state["n"] > 1:  # the page listing works; every projection read fails
            raise RuntimeError("graph down")
        return await real(org, ids)

    loaded.graph.stamps.nodes = flaky  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app)
    assert p.stale > 0
    assert all(f in (None, STALE_SCOPE) for f in (await read_stamps(vdb, loaded)).values())


async def test_a_page_that_cannot_be_listed_fails_the_run_keeps_the_cursor_and_the_next_run_goes_on() -> None:
    vdb, loaded, job, app = await setup(page_size=2)
    await job.run_connector(ORG, app, max_pages=1)
    cursor = loaded.graph.app_docs[app]["scopeBackfillAfter"]
    real = loaded.graph.stamps.record_page

    async def down(*a: object) -> list[str]:
        raise RuntimeError("down")

    loaded.graph.stamps.record_page = down  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app)
    assert p.state == FAILED and p.after == cursor and loaded.graph.app_docs[app]["scopeBackfillError"] == "RuntimeError"
    loaded.graph.stamps.record_page = real  # type: ignore[method-assign]
    assert (await job.run_connector(ORG, app)).state == DONE


async def test_a_failed_write_marks_the_point_stale() -> None:
    vdb, loaded, job, app = await setup()
    real, state = vdb.set_payload, {"failed": False}

    async def once(name: str, payload: dict, flt) -> None:
        if not state["failed"] and payload.get("scopeStamp") != "legacy":
            state["failed"] = True
            raise RuntimeError("write failed")
        await real(name, payload, flt)

    vdb.set_payload = once  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app)
    assert p.stale >= 1 and STALE_SCOPE in (await read_stamps(vdb, loaded)).values()


async def test_only_the_points_and_apps_of_the_organisation_are_touched() -> None:
    vdb, loaded, job, app = await setup()
    mine = next(iter(vdb.collections[COLL].values()))
    other = {**mine.payload, "metadata": {**mine.payload["metadata"], "orgId": "cd" * 12}}
    await vdb.upsert_points(COLL, [VectorPoint(id="other-org", dense_vector=[0.0] * 8, payload=other)])
    with pytest.raises(UnknownConnector):
        await job.run_connector("cd" * 12, app)
    await job.run_connector(ORG, app)
    assert not set(SCOPE_KEYS) & set(vdb.collections[COLL]["other-org"].payload)


async def test_a_graph_change_between_the_read_and_the_write_is_corrected_by_the_read_back() -> None:
    vdb, loaded, job, app = await setup()
    want = await expected(loaded)
    target = next(i for i, n in loaded.graph.stamps.node_map.items() if n.kind == "record" and n.vrid and want.get(n.vrid) == ScopeFields.container())
    real, state = vdb.set_payload, {"n": 0}
    vrid = loaded.graph.stamps.node_map[target].vrid

    async def racing(*a, **k) -> None:
        await real(*a, **k)
        if vrid in repr(a[2]):
            state["n"] += 1
        if state["n"] == 1 and vrid in repr(a[2]):  # a permission change commits right after the first write of this point: the record leaves its container
            n = loaded.graph.stamps.node_map[target]
            loaded.graph.stamps.node_map = {**loaded.graph.stamps.node_map, target: replace(n, rule="RESTRICTED", granted=True)}

    vdb.set_payload = racing  # type: ignore[method-assign]
    await job.run_connector(ORG, app)
    vdb.set_payload = real  # type: ignore[method-assign]
    now = (await expected(loaded))[vrid]
    got = (await read_stamps(vdb, loaded))[vrid]
    assert got == now and got != ScopeFields.container()


async def test_a_live_membership_write_running_beside_the_back_fill_leaves_the_fields_the_graph_gives() -> None:
    vdb, loaded, job, app = await setup()
    with oc.for_org(ORG, job="live"):
        await asyncio.gather(job.run_connector(ORG, app), *(sync_vector_membership(vdb, loaded.locator, loaded.graph, v, log) for v in loaded.records))
    want = await expected(loaded)
    got = await read_stamps(vdb, loaded)
    assert all(got[v] == f for v, f in want.items() if f is not None and got[v] is not None)
    assert sum(g is not None for g in got.values()) >= 5
