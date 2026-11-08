# ruff: noqa: ANN401
"""What the rewrite promises beyond the oracle gate: a second organisation's points are never touched, one bad root does not hold the others, a
root registered again stays, a held-up writer cannot write over a newer rewrite (the generation check and the store's fence), one lock order, a
connector that cannot be released is left to the full pass, and the run is idempotent.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from app.services.permissions.pending import parse
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    OPENED_KEY,
    UNSAFE_KEY,
)
from app.services.vector_db import membership, scope_rewrite
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.scope_backfill import (
    BACKFILL_KEYS,
    ScopeFieldsOff,
    UnknownConnector,
)
from app.services.vector_db.scope_rewrite import (
    PENDING_KEY,
    RewriteIncomplete,
)

from .model import Mutation as M
from .rewrite_kit import Env, Log, Tap, held_up_writer
from .scenario import WorldStore
from .stamp_world import ORG

OTHER_ORG = "cd" * 12
W = WorldStore()


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def hr(**kw: Any) -> Env:
    return await Env.make(W.world("golden/confluence_hr"), **kw)


MOVE = M("moved", "page:Handbook", "space:ALL", "space:ALL")


async def test_a_second_organisations_point_with_the_same_virtual_record_is_untouched() -> None:
    env = await hr()
    pid = env.loaded.points["w:v-page:Handbook"][0]
    mine = (await env.vdb.retrieve_points("rewrite", [pid]))[0]
    theirs = copy.deepcopy(mine)
    theirs.id = "00000000-0000-4000-8000-0000000000aa"
    theirs.payload["metadata"]["orgId"] = OTHER_ORG
    theirs.payload.update({"scopeStamp": "union", "pathIds": ["theirs"], "aclScope": ["theirs"], "connectorIds": ["theirs"], "recordGroupIds": ["g-theirs"]})
    await env.vdb.upsert_points("rewrite", [VectorPoint(id=theirs.id, dense_vector=None, payload=theirs.payload)])
    before = copy.deepcopy((await env.vdb.retrieve_points("rewrite", [theirs.id]))[0].payload)
    await env.change((MOVE,))
    await env.settle()
    assert (await env.vdb.retrieve_points("rewrite", [theirs.id]))[0].payload == before
    assert not await env.problems()


async def test_one_bad_root_does_not_hold_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await hr()
    await env.change((MOVE,))
    await env.change((M("moved", "page:Compensation", "space:ALL", "space:ALL"),))      # (its subtree is disjoint from the Handbook's)
    assert len(env.pending()) == 2
    real = scope_rewrite.resolve_virtual_record_state

    async def flaky(graph: Any, vrid: str, *a: Any, **k: Any) -> Any:
        state = await real(graph, vrid, *a, **k)
        return replace(state, complete=False) if vrid.endswith("v-page:2027-bands") else state

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", flaky)
    with pytest.raises(RewriteIncomplete, match="1 of 2 roots"):
        await env.job.run_connector(ORG, "w:app")
    assert list(env.pending()) == ["w:page:Compensation"]               # the healthy root came off, the bad one stays
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits
    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", real)
    await env.settle()
    assert not await env.problems()


async def test_a_root_registered_again_while_it_was_rewritten_stays_on_the_list() -> None:
    env = await hr()
    await env.change((MOVE,))
    again = {"done": False}

    async def register_again() -> None:
        if not again["done"]:
            again["done"] = True
            await env.change((M("moved", "page:Handbook", "space:HR", "space:HR"),))

    env.before_write = register_again
    first = await env.job.run_connector(ORG, "w:app")
    env.before_write = None
    assert first.state == "done"
    assert list(env.pending()) == ["w:page:Handbook"] and env.pending()["w:page:Handbook"] > 1, env.pending()  # the newer registration survived
    await env.settle()                                                                                          # and the next run covers it
    assert not env.pending() and not await env.problems()


async def test_a_second_run_after_a_finished_one_does_nothing() -> None:
    env = await hr()
    await env.change((MOVE,))
    await env.settle()
    writes: list[str] = []

    async def count(name: str) -> None:
        writes.append(name)

    Tap(env, _nothing, lambda name: count(name) if name == "update_payloads_by_ids" else _none())
    again = await env.job.run_connector(ORG, "w:app")
    assert again.state == "idle" and not writes


async def test_a_run_repeated_over_a_finished_rewrite_writes_the_same_fields() -> None:
    """The fields are a pure function of the graph: running the steps again (a killed run's next run) changes nothing."""
    env = await hr()
    await env.change((MOVE,))
    await env.settle()
    stored = {p.id: copy.deepcopy(p.payload) for p in await env.vdb.retrieve_points("rewrite", [x for ps in env.loaded.points.values() for x in ps])}
    env.graph.app_docs["w:app"][PENDING_KEY] = '{"w:page:Handbook":1}'          # the root is back on the list (the kill came after the points, before the root)
    res = await env.job.run_connector(ORG, "w:app")
    assert res.state == "done" and res.points_written == 0
    again = {p.id: p.payload for p in await env.vdb.retrieve_points("rewrite", list(stored))}
    assert {k: {f: v for f, v in p.items() if f != "scopeFence"} for k, p in again.items()} == {k: {f: v for f, v in p.items() if f != "scopeFence"} for k, p in stored.items()}


async def _nothing(_: str) -> None:
    return None


async def _none() -> None:
    return None


@pytest.mark.parametrize("layer", ["generation_check", "store_fence"])
async def test_a_held_up_writer_cannot_write_over_a_newer_rewrite(monkeypatch: pytest.MonkeyPatch, layer: str) -> None:
    """Writer A reads the graph after change 1 and is held up. Change 2 commits and a second writer B (another process: its own loop) rewrites
    and finishes. A then goes on. Each layer is shown alone. `generation_check`: A is held before its request, on a store with no conditional
    write, and the check right before the request stops it. `store_fence`: A is held after the check, at the store's door, with the check
    disabled, and the store refuses the older fence."""
    env = await hr()
    env.vdb.generation_fence = layer == "store_fence"
    await held_up_writer(env, layer, monkeypatch)


async def test_the_app_document_is_written_only_outside_the_virtual_record_locks() -> None:
    env = await hr()
    await env.change((MOVE,))
    real = env.graph.update_node_fields_if_match
    held: list[bool] = []

    async def watch(*a: Any, **k: Any) -> bool:
        held.append(any(lock.locked() for lock in membership._vrid_locks.values()))
        return await real(*a, **k)

    env.graph.update_node_fields_if_match = watch
    await env.settle()
    assert held and not any(held), held


@pytest.mark.parametrize("breaks", ["not_backfilled", "unlisted_bump", "unsafe_mark", "older_pass", "unclosed_write"])
async def test_a_connector_that_cannot_be_released_is_left_to_the_full_pass(breaks: str) -> None:
    env = await hr()
    await env.change((MOVE,))
    doc = env.graph.app_docs["w:app"]
    if breaks == "not_backfilled":
        doc[BACKFILL_KEYS["state"]] = "running"
    elif breaks == "unlisted_bump":
        doc[GENERATION_KEY] += 1            # a change that listed no root (the whole connector, or more roots than the list holds), after the pass
    elif breaks == "unclosed_write":
        doc[OPENED_KEY] = 1            # a write that could not be undone started on this connector and wrote no closing mark
    elif breaks == "unsafe_mark":
        doc[UNSAFE_KEY] = 12345
    else:
        doc[BACKFILL_KEYS["version"]] = 1
    writes: list[str] = []
    Tap(env, _nothing, lambda name: writes.append(name) or _none() if name == "update_payloads_by_ids" else _none())
    res = await env.job.run_connector(ORG, "w:app")
    assert res.state == "full_pass" and not res.released and not writes and env.pending()
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits                # nothing is read from the connector's stamps meanwhile


async def test_the_job_refuses_when_the_fields_are_off_and_for_an_unknown_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    env = await hr()
    with pytest.raises(UnknownConnector):
        await env.job.run_connector(ORG, "w:no-such-app")
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    with pytest.raises(ScopeFieldsOff):
        await env.job.run_connector(ORG, "w:app")


async def test_a_pending_list_that_does_not_parse_or_a_generation_that_does_not_read_fails_closed() -> None:
    env = await hr()
    await env.change((MOVE,))
    good = env.graph.app_docs["w:app"][PENDING_KEY]
    env.graph.app_docs["w:app"][PENDING_KEY] = "{not json"
    with pytest.raises(RewriteIncomplete):
        await env.job.run_connector(ORG, "w:app")
    env.graph.app_docs["w:app"][PENDING_KEY] = good
    env.graph.app_docs["w:app"][GENERATION_KEY] = "7"
    with pytest.raises(RewriteIncomplete):
        await env.job.run_connector(ORG, "w:app")
    assert parse(good) and env.pending() == parse(good)


async def test_the_generation_is_read_before_the_graph() -> None:
    """After the last subtree read and before the first read of a virtual record, the page reads the app document (its generation)."""
    env = await hr()
    await env.change((MOVE,))
    tap = Tap(env, _nothing)
    await env.settle()
    calls = tap.calls
    first_read = calls.index("get_records_by_virtual_record_id")
    last_subtree = max(i for i in range(first_read) if calls[i] == "subtree_vrids")
    assert "get_document" in calls[last_subtree:first_read], calls[last_subtree:first_read + 1]


async def test_a_subtree_too_deep_to_walk_is_left_to_the_full_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """`subtree_too_deep` means the whole connector is reached: nothing is released or written, and the root stays hidden."""
    from app.services.permissions.errors import ViewerScopesUnavailable

    env = await hr()
    await env.change((MOVE,))

    async def too_deep(*a: Any, **k: Any) -> list[str]:
        raise ViewerScopesUnavailable("subtree_too_deep")

    monkeypatch.setattr(env.graph.stamps, "subtree_vrids", too_deep)
    writes: list[str] = []
    Tap(env, _nothing, lambda name: writes.append(name) or _none() if name == "update_payloads_by_ids" else _none())
    res = await env.job.run_connector(ORG, "w:app")
    assert res.state == "full_pass" and not res.released and not writes and env.pending()
    seen = Log()
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits


async def test_more_roots_than_the_list_holds_leave_nothing_to_rewrite_and_nothing_released() -> None:
    from app.services.permissions.pending import MAX_PENDING_ROOTS
    from app.services.permissions.structure_generation import register_pending_roots

    env = await hr()
    await register_pending_roots(env.graph, "w:app", [f"w:r{i}" for i in range(MAX_PENDING_ROOTS + 1)])     # lists none; the bump keeps the connector not current
    assert not env.pending() and not (await env.current())
    res = await env.job.run_connector(ORG, "w:app")
    assert res.state == "idle" and not res.released and not (await env.current())          # only a full pass makes it current again
    await env.change((MOVE,))                                                              # and a root listed after the unlisted bump is not a licence to release
    assert (await env.job.run_connector(ORG, "w:app")).state == "full_pass"
    await env.settle()
    assert not env.pending() and await env.current() == set(env.apps)
    assert not await env.problems()
