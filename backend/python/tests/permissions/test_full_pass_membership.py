# ruff: noqa: ANN401, ANN201
"""A change that only a full pass covers (a change of the whole connector, more roots than the list holds) must also bring each point's group
membership (`recordGroupIds`) to what the graph says: a `container` stamp is read through that membership. A record moved under a group-level
group that few people read, with the pass alone, must not stay visible to the readers of the group it left.

The pass writes the membership the way the rewrite does: by id, checked against the connector's generation right before each request, fenced,
and read back from the graph, the store and a search. A membership the graph cannot resolve, or a write the store drops, ends the pass
`failed` with the old membership alone.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services.vector_db import scope_rewrite
from app.services.vector_db.scope_backfill import DONE, FAILED

from . import gen
from .model import Gate, Grant, Membership, Mutation, Node, Parent, Principal, World
from .rewrite_kit import Env, Log
from .stamp_world import ORG
from .test_scope_projector import retyped

MEMBER_KEYS = ("connectorIds", "recordGroupIds", "rootRecordGroupIds")
MOVE = Mutation("moved", "doc", "rgl", "rgl")


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


async def test_a_membership_the_graph_cannot_resolve_leaves_the_full_pass_failed_and_the_old_membership_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await moved_whole()
    before = await stored_membership(env)
    real = scope_rewrite.resolve_virtual_record_state

    async def unresolved(graph: Any, v: str, *a: Any, **k: Any) -> Any:
        return replace(await real(graph, v, *a, **k), roots_resolved=False)

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", unresolved)
    assert (await env.backfill.run_connector(ORG, "w:B", restart=True)).state == FAILED
    assert await stored_membership(env) == before and "w:B" not in await env.current()
    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", real)       # the graph answers again
    assert (await env.backfill.run_connector(ORG, "w:B", restart=True)).state == DONE
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not await env.problems()


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
