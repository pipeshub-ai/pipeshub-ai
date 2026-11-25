# ruff: noqa: ANN401
"""A change that only a full pass covers (a change of the whole connector, more roots than the list holds) must also bring each point's group
membership (`recordGroupIds`) to what the graph says: a `container` stamp is read through that membership. A record moved under a group-level
group that few people read, with the pass alone, must not stay visible to the readers of the group it left.

The pass writes the membership the way the rewrite does: by id, checked against the connector's generation right before each request, fenced,
and read back from the graph, the store and a search. A membership the graph cannot resolve, or a write the store drops, ends the pass
`failed` with the old membership alone.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any

import pytest

from app.services.permissions.structure_generation import register_pending_roots
from app.services.vector_db import scope_rewrite
from app.services.vector_db.scope_backfill import DONE, FAILED

from . import gen
from .model import Gate, Grant, Membership, Mutation, Node, Parent, Principal, World
from .rewrite_kit import Env, Log, Tap
from .scenario import WorldStore
from .stamp_world import ORG
from .test_scope_projector import retyped

MEMBER_KEYS = ("connectorIds", "recordGroupIds", "rootRecordGroupIds")
MOVE = Mutation("moved", "doc", "rgl", "rgl")
HR_MOVE = Mutation("moved", "page:Handbook", "space:ALL", "space:ALL")
HOUR = 3600 * 10**6


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


def world() -> World:
    app = Node("B", "app", app_type="SHAREPOINT")
    return World(
        principals=(Principal("reader"), Principal("other"), Principal("team", "group")),
        nodes=(app,
               Node("open", "group", "B", parents=(Parent("B"),), inherits=("B",), belongs=("B",)),
               Node("rgl", "group", "B", rule="RESTRICTED", parents=(Parent("B"),), inherits=("B",), belongs=("B",), model="RECORD_GROUP_LEVEL"),
               Node("doc", "record", "B", rule="STRICT", parents=(Parent("open"),), inherits=("open",), belongs=("open",))),
        grants=(Grant("team", "rgl"),),
        memberships=(Membership("reader", "team"),),
        gates=(Gate("reader", "B"), Gate("other", "B")))


async def moved_whole() -> Env:
    env = await Env.make(world())
    await env.change((MOVE,), whole=True)      # a change of the whole connector: only a full pass covers it
    return env


async def stored_membership(env: Env) -> dict[str, list[str]]:
    return {str(p.id): sorted(p.payload.get("recordGroupIds") or []) for p in env.vdb._points(env.loaded.collection).values()}


async def test_a_record_moved_under_a_group_level_group_is_hidden_from_the_readers_of_the_group_it_left_after_a_full_pass_alone() -> None:
    env, seen = await moved_whole(), Log()
    assert (await env.backfill.run_connector(ORG, "w:B", restart=True)).state == DONE
    await env.audit(seen)
    assert not seen.leaks, seen.leaks
    assert env.pending() == {} and "w:B" in await env.current()
    assert not await env.problems()      # the membership too: `recordGroupIds` is the graph's


async def test_the_full_pass_that_covers_more_roots_than_the_list_holds_brings_the_membership_too() -> None:
    from app.services.permissions.pending import MAX_PENDING_ROOTS
    from app.services.permissions.structure_generation import register_pending_roots

    env = await Env.make(world())
    await env.change((MOVE,), whole=True)
    await register_pending_roots(env.graph, "w:B", [f"w:r{i}" for i in range(MAX_PENDING_ROOTS + 1)])
    await env.settle()
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not await env.problems()


async def test_a_record_moved_into_a_group_level_group_under_a_seed_the_walk_cannot_reach_is_not_stamped_with_the_group() -> None:
    """`A-c5` is read by `u4` by a direct grant, and the walk from it to the app goes through the group-level group `A-c1`, which it skips: a
    seed. `u4` reads `A-c6` (a group-level group below that seed) but not the records filed in it, which a declared group makes readable only
    for people who reach the group from the app or by a grant."""
    base = retyped(gen.generate("adversarial", 2091, 1)[0], "SHAREPOINT")
    env, seen = await Env.make(base, chunks=3), Log()
    await env.change((Mutation("moved", "A-c9", "A-c6", "A-c6"),))
    await env.settle()
    await env.audit(seen)
    assert not seen.leaks and not seen.inexact and not await env.problems(), (seen.leaks[:2], seen.inexact[:2])


async def test_a_virtual_record_whose_membership_cannot_be_resolved_is_left_stale_and_does_not_hold_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """One virtual record the graph cannot place must not stop the pass at its page for good (every re-run would fail in the same place): it is
    left on the stale marker, which no stamp admits, and counted; the rest of the connector is finished; and a later pass brings it right."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((Mutation("moved", "page:Handbook", "space:ALL", "space:ALL"),), whole=True)
    bad = env.vrids()["page:Onboarding"]
    real = scope_rewrite.resolve_virtual_record_state

    async def unresolved(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, v, *a, **k)
        return replace(out, roots_resolved=False) if v == bad else out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", unresolved)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == DONE and done.stale == 1, done.line()          # counted, and the pass finished
    handbook = (await env.vdb.retrieve_points(env.loaded.collection, env.loaded.points[env.vrids()["page:Handbook"]]))[0].payload
    assert handbook["recordGroupIds"] == ["w:space:ALL"], "the rest of the connector was not finished"
    onboarding = (await env.vdb.retrieve_points(env.loaded.collection, env.loaded.points[bad]))[0].payload
    assert onboarding["scopeFieldsVersion"] == 0, "the point was left with a stamp that may admit"
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks, seen.leaks
    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", real)       # the graph answers again
    again = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert again.state == DONE and again.stale == 0
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not await env.problems()


async def test_a_connector_that_moves_while_the_pass_marks_an_unplaced_record_fails_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The record is on no marker, and a release could make the connector current without another pass."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((HR_MOVE,), whole=True)
    bad = env.vrids()["page:Onboarding"]
    real = scope_rewrite.resolve_virtual_record_state

    async def unresolved(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, v, *a, **k)
        return replace(out, roots_resolved=False) if v == bad else out

    async def moved(*a: Any, **k: Any) -> None:
        raise scope_rewrite.GenerationMoved("w:app")

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", unresolved)
    monkeypatch.setattr(env.backfill._membership, "_mark_stale", moved)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == FAILED and done.error == "GenerationMoved", done.line()
    assert "w:app" not in await env.current()


async def test_a_membership_write_the_store_does_not_apply_leaves_the_pass_failed_not_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """The move keeps the stamp (container), so only the membership changes; a store that takes the fields and drops the membership is seen."""
    env = await moved_whole()
    real = env.vdb.update_payloads_by_ids

    async def lossy(collection: str, updates: Any, **k: Any) -> None:
        return await real(collection, {pid: {key: v for key, v in p.items() if key not in MEMBER_KEYS} for pid, p in updates.items()}, **k)

    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", lossy)
    assert (await env.backfill.run_connector(ORG, "w:B", restart=True)).state == FAILED
    assert "w:B" not in await env.current()
    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", real)
    assert (await env.backfill.run_connector(ORG, "w:B", restart=True)).state == DONE
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not await env.problems()


async def test_a_move_registered_after_the_first_graph_read_is_written_by_the_retry_with_the_new_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety property of the write: it is checked against the generation right before the request, so the membership read before the move
    is never sent."""
    env = await Env.make(world())
    real, hit = scope_rewrite.resolve_virtual_record_state, {"n": 0}

    async def resolve(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, v, *a, **k)
        if not hit["n"]:
            hit["n"] += 1
            await env.change((MOVE,), whole=True)      # registered right after the first read: the generation moves
        return out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", resolve)
    old_writes: list[dict] = []
    real_put = env.vdb.update_payloads_by_ids

    async def spy(collection: str, updates: Any, **k: Any) -> None:
        old_writes.extend(p for p in updates.values() if hit["n"] and p.get("recordGroupIds") == ["w:open"] and "scopeStamp" not in p)
        return await real_put(collection, updates, **k)

    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", spy)
    await env.backfill.run_connector(ORG, "w:B", restart=True)
    assert hit["n"] and not old_writes, "the retry wrote the membership read before the move"


async def test_a_virtual_record_with_no_live_record_and_an_unreadable_membership_does_not_hold_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await Env.make(world())
    gone = env.vrids()["doc"]
    await env.change((Mutation("deleted", "doc"),), whole=True)      # the record is deleted, the point and the virtual record stay
    real = scope_rewrite.resolve_virtual_record_state

    async def resolve(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, v, *a, **k)
        return replace(out, roots_resolved=False) if v == gone else out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", resolve)
    done = await env.backfill.run_connector(ORG, "w:B", restart=True)
    assert done.state == DONE and done.stale == 0


async def test_a_move_after_the_write_is_found_by_the_recheck_of_the_membership_and_written_in_the_next_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fields do not change (the stamp stays container), so only the recheck of the membership against the graph can send the page round again."""
    env = await Env.make(world())
    member = env.backfill._membership
    real, hit = member._differs, {"n": 0}

    async def differs(*a: Any, **k: Any) -> Any:
        if not hit["n"]:
            hit["n"] += 1
            await env.change((MOVE,), whole=True)      # after the write, before the read-back
        return await real(*a, **k)

    monkeypatch.setattr(member, "_differs", differs)
    await env.backfill.run_connector(ORG, "w:B", restart=True)
    vrid = env.vrids()["doc"]
    assert hit["n"]
    assert (await stored_membership(env))[env.loaded.points[vrid][0]] == ["w:rgl"], await stored_membership(env)


async def test_a_full_pass_follows_a_fence_above_this_hosts_time_too() -> None:
    env = await Env.make(WorldStore().world("golden/confluence_hr"), settle_seconds=0.2)
    for p in env.vdb._points(env.loaded.collection).values():
        p.payload["scopeFence"] = scope_rewrite._micros(scope_rewrite._clock()) + 5 * 60 * 10**6
    await env.change((HR_MOVE,), whole=True)
    assert (await env.backfill.run_connector(ORG, "w:app", restart=True)).state == DONE
    assert not await env.problems()      # the membership too: the same fence blocked it before


async def test_the_back_fill_does_not_wedge_on_a_connector_that_never_stops_changing() -> None:
    env = await Env.make(WorldStore().world("golden/confluence_hr"), settle_seconds=0.2)
    await env.change((HR_MOVE,), whole=True)
    live = {"on": True}

    async def never_still(name: str) -> None:
        if live["on"] and name == "retrieve_points":
            await env.graph.increment_node_field("w:app", "apps", "scopeProjectionGeneration")

    Tap(env, never_still)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == FAILED          # a page left unwritten must not count as covered; the next pass does the work again
    assert "w:app" not in await env.current()
    live["on"] = False
    again = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert again.state == DONE and not await env.problems() and "w:app" in await env.current()


async def test_a_page_the_connector_kept_moving_under_cannot_be_released_by_a_later_rewrite() -> None:
    """Every move lists a root of its own, which is what lets a rewrite release a finished pass without running a new one. The records of the
    page were never written, so the pass must not count as finished."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"), settle_seconds=0.2)
    await env.change((HR_MOVE,), whole=True)
    live, roots = {"on": True}, itertools.count()

    async def moves_with_a_root(name: str) -> None:
        if live["on"] and name == "retrieve_points":
            await register_pending_roots(env.graph, "w:app", [f"w:elsewhere-{next(roots)}"])

    Tap(env, moves_with_a_root)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == FAILED, done.line()
    live["on"] = False
    result = await env.run_job("app")
    assert not result.released, result
    assert "w:app" not in await env.current()
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks, seen.leaks[:2]


async def test_the_stale_marker_of_a_record_whose_membership_cannot_be_resolved_lands_over_a_fence_ahead(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case where a refused marker was an over-admit: the whole connector changed, a record's membership cannot be resolved, and its points
    carry a fence an hour ahead. The marker is not refused by the fence: the points are on the legacy filter and nothing admits through the old
    membership under the stamp they held."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((HR_MOVE,), whole=True)
    bad = env.vrids()["page:Handbook"]
    for point in env.vdb._points(env.loaded.collection).values():
        point.payload["scopeFence"] = scope_rewrite._micros(scope_rewrite._clock()) + HOUR
    real = scope_rewrite.resolve_virtual_record_state

    async def unresolved(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real(graph, v, *a, **k)
        return replace(out, roots_resolved=False) if v == bad else out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", unresolved)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    # Two records end on the marker: the unresolvable one, and one below it whose fields changed and cannot be written over a fence that far
    # ahead (the back-fill's write is fenced like a rewrite's, so it no longer lands over it).
    assert done.state == DONE and done.stale == 2, done.line()
    for name in ("page:Handbook", "page:Onboarding"):
        held = [p.payload for p in await env.vdb.retrieve_points(env.loaded.collection, env.loaded.points[env.vrids()[name]])]
        assert held and all(p["scopeFieldsVersion"] == 0 for p in held), f"{name}: the marker was refused: the old stamp and membership are trusted"
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks, seen.leaks[:4]


async def test_a_virtual_record_left_stale_by_both_steps_of_the_pass_is_counted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its scope fields cannot be told (the back-fill's own read is incomplete) and neither can its membership (the engine's): one record, one count."""
    from app.services.vector_db import scope_backfill

    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    bad = env.vrids()["page:Handbook"]

    def incomplete(real: Any) -> Any:
        async def read(graph: Any, v: str, *a: Any, **k: Any) -> Any:
            out = await real(graph, v, *a, **k)
            return replace(out, complete=False) if v == bad else out

        return read

    monkeypatch.setattr(scope_backfill, "resolve_virtual_record_state", incomplete(scope_backfill.resolve_virtual_record_state))
    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", incomplete(scope_rewrite.resolve_virtual_record_state))
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == DONE and done.stale == 1, done.line()


def stamps_all_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """The graph read for the stamp says "stale" for every virtual record: the pass's own marker is written for all of them."""
    from app.services.vector_db import scope_backfill
    from app.services.vector_db.membership import STALE_SCOPE

    async def stale(*a: Any, **k: Any) -> Any:
        return STALE_SCOPE

    monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)


def is_marker(update: dict[str, Any]) -> bool:
    return update.get("scopeStamp") == "legacy" and update.get("scopeFieldsVersion") == 0


@pytest.mark.parametrize("how", ["raises", "drops"])
async def test_a_stamp_marker_the_store_refuses_or_drops_fails_the_pass_and_a_later_pass_leaves_no_point_admitting(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, how: str) -> None:
    """The pass's own stale marker (a record whose stamp cannot be computed) is written by id and read back, like the rewrite's: one that did not
    land fails the pass, never ends it current with a stamp that may admit; once the store takes it, no point admits."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((HR_MOVE,), whole=True)
    stamps_all_stale(monkeypatch)
    real = env.vdb.update_payloads_by_ids

    async def refusing(collection: str, updates: Any, **k: Any) -> None:
        if any(is_marker(u) for u in updates.values()):
            if how == "raises":
                raise RuntimeError("store down")
            return None      # taken and dropped
        return await real(collection, updates, **k)

    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", refusing)
    with caplog.at_level("ERROR"):
        failed = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert failed.state == FAILED, failed.line()
    assert "w:app" not in await env.current() and (how == "drops" or "could not be marked stale" in caplog.text)
    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", real)
    done = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert done.state == DONE and done.stale > 0, done.line()
    assert all(p.payload["scopeFieldsVersion"] == 0 for p in env.vdb._points(env.loaded.collection).values())
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits


async def test_the_marker_of_a_record_whose_membership_cannot_be_resolved_is_read_back_on_the_full_pass_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store that takes the marker of the membership step without applying it must fail the pass: the read-back is what sees it."""
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((HR_MOVE,), whole=True)
    bad = env.vrids()["page:Handbook"]
    real_state = scope_rewrite.resolve_virtual_record_state

    async def unresolved(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        out = await real_state(graph, v, *a, **k)
        return replace(out, roots_resolved=False) if v == bad else out

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", unresolved)
    real = env.vdb.update_payloads_by_ids

    async def dropping(collection: str, updates: Any, **k: Any) -> None:
        return await real(collection, {pid: u for pid, u in updates.items() if not is_marker(u)}, **k)

    monkeypatch.setattr(env.vdb, "update_payloads_by_ids", dropping)
    failed = await env.backfill.run_connector(ORG, "w:app", restart=True)
    assert failed.state == FAILED and "w:app" not in await env.current(), failed.line()
