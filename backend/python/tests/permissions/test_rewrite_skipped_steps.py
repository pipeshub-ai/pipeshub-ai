# ruff: noqa: ANN401
"""A rewrite never takes a pending root off after skipping part of its work, wherever the skip is. Each case breaks one step: the run raises
`RewriteIncomplete`, the root stays pending and hidden, nothing is admitted wrongly meanwhile, and the next run, with the step working, ends exact
(a hidden subtree is visible again).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services.vector_db import scope_rewrite
from app.services.vector_db.scope_rewrite import RewriteIncomplete

from .model import Mutation as M
from .rewrite_kit import Env, FencedMemoryVectorDB, Log, Tap
from .scenario import WorldStore
from .stamp_world import ORG

HR = ("golden/confluence_hr", M("moved", "page:Handbook", "space:ALL", "space:ALL"), "v-page:Handbook")
TREE = ("golden/sharepoint_tree", M("moved", "folder:Specs", "folder:Ops"), "v-deep-a")


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


class LaggingSearchVectorDB(FencedMemoryVectorDB):
    """A store that searches an older view than it stores, as OpenSearch does between refreshes: `retrieve_points` (a realtime get) sees every
    write, `scroll` shows the payloads as of the last `make_searchable` (an index refresh)."""

    def __init__(self) -> None:
        super().__init__()
        self.view: dict[str, dict[str, Any]] = {}
        self.refreshes = 0
        self.refresh_works = True

    async def scroll(self, collection_name: str, scroll_filter: Any, limit: int, offset: Any = None, with_payload: Any = None) -> Any:
        page = await super().scroll(collection_name, scroll_filter, limit, offset, with_payload)
        for point in page.points:
            point.payload = self.view.setdefault(str(point.id), dict(point.payload))
        return page

    async def make_searchable(self, collection_name: str) -> None:
        self.refreshes += 1
        if self.refresh_works:
            for point in self._points(collection_name).values():
                self.view[str(point.id)] = dict(point.payload)


class LossyScrollVectorDB(FencedMemoryVectorDB):
    """A scroll that silently lacks points, as a RediSearch query that ran out of time does; the store's own count still says how many there are."""

    drop = 0

    async def scroll(self, collection_name: str, scroll_filter: Any, limit: int, offset: Any = None, with_payload: Any = None) -> Any:
        page = await super().scroll(collection_name, scroll_filter, limit, offset, with_payload)
        page.points = page.points[self.drop:]
        return page


async def run_broken(env: Env, mutation: M) -> None:
    await env.change((mutation,))
    with pytest.raises(RewriteIncomplete):
        await env.job.run_connector(ORG, "w:app")
    assert env.pending(), "the root was removed over work that was skipped"
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits, (seen.leaks[:3], seen.pending_admits[:3])


async def recovers(env: Env) -> None:
    await env.settle()
    seen = Log()
    await env.audit(seen)
    assert not await env.problems() and not seen.leaks and not seen.inexact and not seen.pending_admits, (await env.problems(), seen.inexact[:3])


@pytest.mark.parametrize("what", ["incomplete", "roots_unresolved", "no_connector"])
async def test_a_virtual_record_whose_membership_cannot_be_read_keeps_the_root(monkeypatch: pytest.MonkeyPatch, what: str) -> None:
    ref, mutation, suffix = HR
    env = await Env.make(WorldStore().world(ref))
    real = scope_rewrite.resolve_virtual_record_state
    change = {"incomplete": {"complete": False}, "roots_unresolved": {"roots_resolved": False}, "no_connector": {"connector_ids": []}}[what]

    async def flaky(graph: Any, vrid: str, *a: Any, **k: Any) -> Any:
        state = await real(graph, vrid, *a, **k)
        return replace(state, **change) if vrid.endswith(suffix) else state

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", flaky)
    await run_broken(env, mutation)
    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", real)
    await recovers(env)


async def test_a_virtual_record_whose_collections_cannot_be_resolved_keeps_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    ref, mutation, suffix = TREE
    env = await Env.make(WorldStore().world(ref))
    real = env.loaded.locator.collections_for_records

    def broken(records: Any) -> Any:
        if any(str(r.get("_key") or r.get("id") or "").endswith(suffix.removeprefix("v-")) for r in records):
            raise RuntimeError("the collection locator could not say")
        return real(records)

    monkeypatch.setattr(env.loaded.locator, "collections_for_records", broken)
    await run_broken(env, mutation)
    monkeypatch.setattr(env.loaded.locator, "collections_for_records", real)
    await recovers(env)


async def test_a_write_the_store_acknowledged_and_lost_keeps_the_root() -> None:
    ref, mutation, _ = HR
    env = await Env.make(WorldStore().world(ref))
    lose = {"on": True}

    async def before(name: str) -> str | None:
        return "drop" if lose["on"] and name == "update_payloads_by_ids" else None

    Tap(env, before)
    await run_broken(env, mutation)
    lose["on"] = False
    await recovers(env)


async def test_a_scroll_that_lacks_points_keeps_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope_rewrite, "SCAN_RETRY_SECONDS", 0)
    ref, mutation, _ = HR
    vdb = LossyScrollVectorDB()
    env = await Env.make(WorldStore().world(ref), vdb)
    vdb.drop = 1
    await run_broken(env, mutation)
    vdb.drop = 0
    await recovers(env)


async def test_a_store_that_cannot_count_keeps_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    ref, mutation, _ = HR
    vdb = FencedMemoryVectorDB()
    env = await Env.make(WorldStore().world(ref), vdb)
    real = vdb.count_points

    async def cannot(*a: Any, **k: Any) -> int:
        raise NotImplementedError("this store cannot count")

    monkeypatch.setattr(vdb, "count_points", cannot)
    await run_broken(env, mutation)
    monkeypatch.setattr(vdb, "count_points", real)
    await recovers(env)


async def test_the_root_leaves_only_when_a_search_shows_what_was_written() -> None:
    """OpenSearch: a get by id sees a write that a search does not until the index refreshes. The job asks the store to publish and reads the
    points back through a search; a store that never publishes keeps the root."""
    ref, mutation, _ = HR
    vdb = LaggingSearchVectorDB()
    env = await Env.make(WorldStore().world(ref), vdb)
    vdb.view.clear()        # (the initial back-fill was read by id; the search view starts from here)
    vdb.refresh_works = False
    await run_broken(env, mutation)
    vdb.refresh_works = True
    await recovers(env)
    assert vdb.refreshes
    stale = [p for p in vdb._points("rewrite").values() if vdb.view.get(str(p.id)) != p.payload]
    assert not stale, "the search view still shows older fields than the store holds"


async def test_a_stored_fence_that_is_not_a_number_fails_the_root_and_keeps_it() -> None:
    ref, mutation, _ = HR
    env = await Env.make(WorldStore().world(ref))
    pid = env.loaded.points["w:v-page:Handbook"][0]
    env.vdb._points("rewrite")[pid].payload["scopeFence"] = "not-a-number"
    await run_broken(env, mutation)
    del env.vdb._points("rewrite")[pid].payload["scopeFence"]       # repaired
    await recovers(env)


async def test_a_virtual_record_that_cannot_be_placed_is_not_forgotten_when_another_needs_a_second_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both are in one page. The first round cannot place Onboarding and loses the write for Handbook, so a second round runs for Handbook alone;
    Onboarding must still keep the root."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    bad = env.vrids()["page:Onboarding"]
    real = scope_rewrite.resolve_virtual_record_state

    async def flaky(graph: Any, vrid: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, vrid, *a, **k)
        return replace(out, complete=False) if vrid == bad else out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", flaky)
    lost = {"n": 0}

    async def lose_the_first_write(name: str) -> str | None:
        if name == "update_payloads_by_ids" and lost["n"] == 0:
            lost["n"] += 1
            return "drop"
        return None

    Tap(env, lose_the_first_write)
    with pytest.raises(RewriteIncomplete, match="1 virtual record"):
        await env.job.run_connector(ORG, "w:app")
    assert lost["n"] == 1 and env.pending(), "the root came off over a virtual record that was never placed"
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits


def _fence_all(env: Env, value: int) -> None:
    for point in env.vdb._points(env.loaded.collection).values():
        point.payload["scopeFence"] = value       # a writer whose clock is ahead was here


async def test_the_stale_marker_lands_whatever_fence_the_points_carry(caplog: pytest.LogCaptureFixture) -> None:
    """A page that cannot be finished is left on the stale marker so that no stamp of it admits. Every store treats a write below the stored fence
    as a silent no-op, so the marker is written unfenced and read back. Here the points are fenced an hour ahead (further than the window), the
    rewrite does not follow, and the marker still lands."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    _fence_all(env, scope_rewrite._micros(scope_rewrite._clock()) + 3600 * 10**6)
    with pytest.raises(RewriteIncomplete, match="no plausible time"):
        await env.job.run_connector(ORG, "w:app")
    payloads = [p.payload for pts in env.loaded.points.values() for p in await env.vdb.retrieve_points(env.loaded.collection, pts)]
    moved = [p for p in payloads if p["metadata"]["virtualRecordId"] in (env.vrids()["page:Handbook"], env.vrids()["page:Onboarding"])]
    assert moved and all(p["scopeFieldsVersion"] == 0 for p in moved), "the marker was refused: the old stamp is still trusted"
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits


async def test_a_marker_the_store_does_not_apply_fails_the_run_instead_of_passing_for_written() -> None:
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    _fence_all(env, scope_rewrite._micros(scope_rewrite._clock()) + 3600 * 10**6)
    real_put = env.vdb.update_payloads_by_ids

    async def drop_markers(collection: str, updates: Any, **k: Any) -> None:
        keep = {pid: p for pid, p in updates.items() if p.get("scopeFieldsVersion") != 0}
        if keep:
            await real_put(collection, keep, **k)

    env.vdb.update_payloads_by_ids = drop_markers      # type: ignore[method-assign]
    try:
        with pytest.raises(RewriteIncomplete, match="stale marker did not land"):
            await env.job.run_connector(ORG, "w:app")
    finally:
        env.vdb.update_payloads_by_ids = real_put      # type: ignore[method-assign]
