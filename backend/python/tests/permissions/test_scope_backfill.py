"""The scope-field back-fill: what it writes, that it stops and resumes, that it is bounded, org-scoped and fails closed, and that
it cannot leave a point with fields an earlier read of the graph gave when a live write or a graph change raced it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import pytest

from app.core import org_context as oc
from app.services.permissions.mode import ENV_SCOPE_FIELDS
from app.services.permissions.scope_fields import (
    ENV_SCOPE_GENERATION,
    SCOPE_FIELDS_VERSION,
    SCOPE_KEYS,
    ScopeFields,
)
from app.services.vector_db import scope_backfill
from app.services.vector_db.membership import STALE_SCOPE, sync_vector_membership
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.scope_backfill import (
    DONE,
    FAILED,
    RUNNING,
    ScopeBackfill,
    ScopeFieldsOff,
    UnknownConnector,
    status,
)
from app.services.vector_db.scope_rewrite import GenerationMoved
from tests.support.in_memory_vector_db import (
    InMemoryVectorDBService as InMemoryVectorDB,
)

from .rewrite_kit import FencedMemoryVectorDB, advancing_clock
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
    return vdb, loaded, ScopeBackfill(loaded.graph, vdb, loaded.locator, log, page_size=page_size, clock=advancing_clock()), loaded.apps[0]


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
    real = vdb.update_payloads_by_ids

    async def dying(*a, **k) -> None:  # the first write after a page was saved
        if loaded.graph.app_docs[app].get("scopeBackfillAfter"):
            raise Killed
        await real(*a, **k)

    vdb.update_payloads_by_ids = dying  # type: ignore[method-assign]
    with pytest.raises(Killed):
        await job.run_connector(ORG, app)
    saved = loaded.graph.app_docs[app]["scopeBackfillAfter"]
    assert saved and loaded.graph.app_docs[app]["scopeBackfillState"] == RUNNING
    vdb.update_payloads_by_ids = real  # type: ignore[method-assign]
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
    real, state = vdb.update_payloads_by_ids, {"failed": False}

    async def once(name: str, updates: dict, **k) -> None:
        if not state["failed"] and any(u.get("scopeStamp") not in (None, "legacy") for u in updates.values()):
            state["failed"] = True
            raise RuntimeError("write failed")
        await real(name, updates, **k)

    vdb.update_payloads_by_ids = once  # type: ignore[method-assign]
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
    real, state = vdb.update_payloads_by_ids, {"n": 0}
    vrid = loaded.graph.stamps.node_map[target].vrid
    mine = {pid for pid, pt in vdb.collections[COLL].items() if pt.payload["metadata"]["virtualRecordId"] == vrid}

    async def racing(name: str, updates: dict, **k) -> None:
        await real(name, updates, **k)
        if mine & set(updates):
            state["n"] += 1
        if state["n"] == 1 and mine & set(updates):  # a permission change commits right after the first write of this point: the record leaves its container
            n = loaded.graph.stamps.node_map[target]
            loaded.graph.stamps.node_map = {**loaded.graph.stamps.node_map, target: replace(n, rule="RESTRICTED", granted=True)}

    vdb.update_payloads_by_ids = racing  # type: ignore[method-assign]
    await job.run_connector(ORG, app)
    vdb.update_payloads_by_ids = real  # type: ignore[method-assign]
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


async def test_a_done_checkpoint_of_an_older_generation_is_not_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """on (generation 1), run to done, off, a permission change, on (generation 2): the run goes over the connector again."""
    monkeypatch.setenv(ENV_SCOPE_GENERATION, "1")
    vdb, loaded, job, app = await setup()
    assert (await job.run_connector(ORG, app)).state == DONE
    assert loaded.graph.app_docs[app]["scopeBackfillGeneration"] == 1
    monkeypatch.delenv(ENV_SCOPE_FIELDS)
    want = await expected(loaded)
    target = next(i for i, n in loaded.graph.stamps.node_map.items() if n.kind == "record" and n.vrid and want.get(n.vrid) == ScopeFields.container())
    n = loaded.graph.stamps.node_map[target]
    loaded.graph.stamps.node_map = {**loaded.graph.stamps.node_map, target: replace(n, rule="RESTRICTED", granted=True)}
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    assert (await job.run_connector(ORG, app)).state == DONE  # same generation: not read again, the old stamp stays
    assert (await read_stamps(vdb, loaded))[n.vrid] == ScopeFields.container()
    monkeypatch.setenv(ENV_SCOPE_GENERATION, "2")
    again = await job.run_connector(ORG, app)
    assert again.state == DONE and again.generation == 2 and again.records > 0
    assert (await read_stamps(vdb, loaded))[n.vrid] == (await expected(loaded))[n.vrid] != ScopeFields.container()


async def test_the_stale_marker_write_is_tried_again_and_a_marker_that_never_lands_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, loaded, job, app = await setup()
    loaded.graph.stamps.failure = None
    real_nodes = loaded.graph.stamps.nodes
    calls = {"n": 0}

    async def flaky(org: str, ids: frozenset[str]) -> dict:
        calls["n"] += 1
        if calls["n"] > 1:  # the page listing works; every projection read fails, so every point wants the marker
            raise RuntimeError("graph down")
        return await real_nodes(org, ids)

    loaded.graph.stamps.nodes = flaky  # type: ignore[method-assign]
    real, failures = vdb.update_payloads_by_ids, {"left": 1}

    async def once(*a, **k) -> None:  # (the marker is written by id)
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("store down")
        await real(*a, **k)

    vdb.update_payloads_by_ids = once  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.stale > 0 and sum(f == STALE_SCOPE for f in (await read_stamps(vdb, loaded)).values()) == p.stale  # the first failure was retried

    async def never(*a, **k) -> None:
        raise RuntimeError("store down")

    vdb.update_payloads_by_ids = never  # type: ignore[method-assign]
    calls["n"] = 0
    with caplog.at_level(logging.ERROR):
        again = await job.run_connector(ORG, app, restart=True)
    assert again.state == FAILED and "could not be marked stale" in caplog.text  # a marker that never lands fails the pass, it does not end it current


async def test_a_marker_that_never_lands_is_tried_and_reported_once_per_record(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    _, _, job, app = await setup()
    tries: dict[str, int] = {}

    async def stale(*a, **k) -> ScopeFields:
        return STALE_SCOPE

    async def never(org: str, connector: str, vrids: list[str]) -> None:
        tries[vrids[0]] = tries.get(vrids[0], 0) + 1
        raise RuntimeError("store down")

    monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)
    monkeypatch.setattr(job._membership, "mark_stale", never)
    with caplog.at_level(logging.ERROR):
        p = await job.run_connector(ORG, app)
    assert p.state == FAILED and tries and set(tries.values()) == {scope_backfill.MARKER_ATTEMPTS}
    assert caplog.text.count("could not be marked stale") == len(tries)


async def test_a_point_that_keeps_its_own_fields_because_the_marker_cannot_be_written_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, loaded, job, app = await setup()
    real_by_id, state = vdb.update_payloads_by_ids, {"stamped": False}

    async def broken(name: str, updates: dict, **k) -> None:
        if any(u.get("scopeStamp") == "legacy" and u.get("scopeFieldsVersion") == 0 for u in updates.values()):
            raise RuntimeError("marker store down")
        await real_by_id(name, updates, **k)
        if not state["stamped"]:
            state["stamped"] = True
            loaded.graph.stamps.failure = RuntimeError("graph down after the first write")  # the read-back then fails

    vdb.update_payloads_by_ids = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        p = await job.run_connector(ORG, app)
    assert state["stamped"] and p.state == FAILED and "could not be marked stale" in caplog.text


async def test_a_connector_that_moves_while_the_marker_is_written_gets_the_marker_on_the_next_try(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, _, job, app = await setup()
    calls = {"n": 0}

    async def stale(*a, **k) -> ScopeFields:
        return STALE_SCOPE

    async def moved_every_other_call(connector: str, epoch: object) -> None:
        calls["n"] += 1
        if calls["n"] % 2:
            raise GenerationMoved(connector)

    async def nothing(*a, **k) -> set[str]:
        return set()

    monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)
    monkeypatch.setattr(job._membership, "_guard", moved_every_other_call)
    monkeypatch.setattr(job._membership, "refresh_membership", nothing)
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.stale > 0 and calls["n"] >= 2 * p.stale
    assert all(point.payload.get("scopeFieldsVersion") == 0 for point in vdb.collections[COLL].values())


async def test_a_connector_that_keeps_moving_fails_the_pass_and_counts_no_marker_it_did_not_write(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, _, job, app = await setup()

    async def stale(*a, **k) -> ScopeFields:
        return STALE_SCOPE

    async def always_moved(connector: str, epoch: object) -> None:
        raise GenerationMoved(connector)

    monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)
    monkeypatch.setattr(job._membership, "_guard", always_moved)
    with caplog.at_level(logging.ERROR):
        p = await job.run_connector(ORG, app)
    assert p.state == FAILED and p.stale == 0 and "could not be marked stale" in caplog.text
    assert not any(point.payload.get("scopeFieldsVersion") == 0 for point in vdb.collections[COLL].values())


async def test_status_refuses_a_connector_of_another_organisation() -> None:
    _, loaded, _, app = await setup()
    assert await status(loaded.graph, ORG, app) is None
    with pytest.raises(UnknownConnector):
        await status(loaded.graph, "cd" * 12, app)


async def test_a_page_written_again_after_a_failure_is_not_counted_twice() -> None:
    vdb, loaded, job, app = await setup(page_size=100)
    real_nodes, n = loaded.graph.stamps.nodes, {"c": 0}

    async def flaky(org: str, ids: frozenset[str]) -> dict:
        n["c"] += 1
        if n["c"] > 1:
            raise RuntimeError("graph down")
        return await real_nodes(org, ids)

    loaded.graph.stamps.nodes = flaky  # type: ignore[method-assign]
    real_save, saves = job._checkpoints.save, {"n": 0}

    async def save_once_failing(a: str, p: scope_backfill.Progress, lease: object = None) -> None:
        saves["n"] += 1
        if saves["n"] == 2:                      # the first save is the unfinished checkpoint of a fresh start; the second is the first page's
            raise RuntimeError("checkpoint store down")
        await real_save(a, p, lease)

    job._checkpoints.save = save_once_failing  # type: ignore[method-assign]
    failed = await job.run_connector(ORG, app)
    assert failed.state == FAILED and loaded.graph.app_docs[app]["scopeBackfillStale"] == 0
    n["c"] = 0
    done = await job.run_connector(ORG, app)
    first_page_vrids = len(loaded.records)
    assert done.state == DONE and 0 < done.stale <= first_page_vrids


async def test_an_unavailable_scope_generation_writes_nothing_and_leaves_no_checkpoint() -> None:
    from app.services.permissions.scope_fields import (
        UNAVAILABLE_GENERATION,
        set_resolved_generation,
    )
    from app.services.vector_db.scope_backfill import ScopeGenerationUnavailable

    vdb, loaded, job, app = await setup()
    set_resolved_generation(UNAVAILABLE_GENERATION)
    try:
        with pytest.raises(ScopeGenerationUnavailable):
            await job.run_connector(ORG, app)
    finally:
        set_resolved_generation(None)
    assert not any(keys_of(vdb)) and not loaded.graph.app_docs  # no point written, no DONE (or any) checkpoint


async def test_a_checkpoint_that_cannot_be_read_fails_the_run_and_leaves_the_saved_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    vdb, loaded, job, app = await setup(page_size=3)
    first = await job.run_connector(ORG, app, max_pages=1)
    assert first.state != DONE and first.after
    saved = dict(loaded.graph.app_docs[app])
    real = loaded.graph.get_document

    async def flaky(key, collection, *args, raise_on_error=False, **kwargs) -> None:  # what the providers do on a failed read
        if raise_on_error:
            raise RuntimeError("graph timeout")

    monkeypatch.setattr(loaded.graph, "get_document", flaky)
    with pytest.raises(RuntimeError):
        await job.run_connector(ORG, app)
    monkeypatch.setattr(loaded.graph, "get_document", real)
    assert loaded.graph.app_docs[app] == saved  # the saved cursor was not overwritten by a fresh start


# ---- currency: the pass records the structure generation it started from, read before any record ----

from app.services.permissions.currency import StampCurrency, app_is_current  # noqa: E402
from app.services.permissions.structure_generation import GENERATION_KEY, UNSAFE_KEY  # noqa: E402


async def test_a_finished_pass_records_the_structure_generation_it_read_first_and_the_connector_is_current() -> None:
    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: 7}
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.structure_generation == 7
    assert loaded.graph.app_docs[app]["scopeBackfillStructureGeneration"] == 7
    assert app_is_current(loaded.graph.app_docs[app]) and await StampCurrency(loaded.graph).current([app, "other"]) == {app}


async def test_a_structural_change_during_the_pass_leaves_the_connector_not_current_until_the_pass_is_run_again() -> None:
    vdb, loaded, job, app = await setup(page_size=3)
    real_nodes, bumped = loaded.graph.stamps.nodes, {"done": False}

    async def bump_while_reading(org: str, ids: frozenset[str]) -> dict:
        if not bumped["done"]:
            bumped["done"] = True
            loaded.graph.app_docs.setdefault(app, {"_key": app})[GENERATION_KEY] = 1       # the change commits after the pass read the generation
        return await real_nodes(org, ids)

    loaded.graph.stamps.nodes = bump_while_reading  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app, max_pages=1000)
    assert p.state == DONE and p.structure_generation == 0
    assert not app_is_current(loaded.graph.app_docs[app])                  # done, but from before the change
    again = await job.run_connector(ORG, app, max_pages=1000)               # a finished checkpoint of an older generation is not "nothing to do"
    assert again.state == DONE and again.structure_generation == 1 and again.records > 0
    assert app_is_current(loaded.graph.app_docs[app])


async def test_a_change_after_the_pass_makes_the_connector_not_current_at_once_and_a_new_pass_makes_it_current() -> None:
    vdb, loaded, job, app = await setup()
    await job.run_connector(ORG, app)
    assert app_is_current(loaded.graph.app_docs[app])
    loaded.graph.app_docs[app][GENERATION_KEY] = 1                            # a structural write bumps it in its own transaction
    assert not app_is_current(loaded.graph.app_docs[app]) and await StampCurrency(loaded.graph).current([app]) == frozenset()
    await job.run_connector(ORG, app)
    assert app_is_current(loaded.graph.app_docs[app])


async def test_a_mark_written_before_the_pass_is_covered_by_it_and_one_written_during_it_is_not() -> None:
    vdb, loaded, job, app = await setup()
    await job.run_connector(ORG, app)
    loaded.graph.app_docs[app][UNSAFE_KEY] = 1000                              # a change that could not move the generation
    assert not app_is_current(loaded.graph.app_docs[app])
    again = await job.run_connector(ORG, app)                                   # a DONE checkpoint does not hide the mark: the pass starts over
    assert again.state == DONE and again.unsafe == 1000 and loaded.graph.app_docs[app][UNSAFE_KEY] == 1000      # and clears nothing
    assert app_is_current(loaded.graph.app_docs[app])                           # the pass read the graph after that change


async def test_a_mark_written_while_the_pass_runs_leaves_the_connector_not_current_even_when_the_old_mark_was_set() -> None:
    """Both marks are set when the pass reads them and when it ends: the second must still be told from the first (a bare True would not be)."""
    for first, second in ((1000, 2000), (True, 2000)):
        vdb, loaded, job, app = await setup(page_size=3)
        loaded.graph.app_docs[app] = {"_key": app, UNSAFE_KEY: first}
        real_nodes, marked = loaded.graph.stamps.nodes, {"done": False}

        async def mark_while_reading(org: str, ids: frozenset[str], real_nodes=real_nodes, marked=marked, loaded=loaded, app=app, second=second) -> dict:
            if not marked["done"]:
                marked["done"] = True
                loaded.graph.app_docs[app][UNSAFE_KEY] = second
            return await real_nodes(org, ids)

        loaded.graph.stamps.nodes = mark_while_reading  # type: ignore[method-assign]
        p = await job.run_connector(ORG, app, max_pages=1000)
        assert p.state == DONE and p.unsafe == first and loaded.graph.app_docs[app][UNSAFE_KEY] == second, first
        assert not app_is_current(loaded.graph.app_docs[app]), first
        again = await job.run_connector(ORG, app, max_pages=1000)               # the next pass reads the new mark and covers it
        assert again.unsafe == second and app_is_current(loaded.graph.app_docs[app]), first


async def test_the_pass_never_writes_the_mark() -> None:
    """Nothing the pass does can erase a mark written beside it: it does not write the field at all."""
    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, UNSAFE_KEY: 1000}
    real_update, real_node, written = loaded.graph.update_node_fields_if_match, loaded.graph.update_node, []

    async def spy(key: str, collection: str, updates: dict, *args: object) -> bool:  # (the checkpoint is written under the lease)
        written.append(set(updates))
        return await real_update(key, collection, updates, *args)

    async def spy_node(key: str, collection: str, updates: dict, *args: object) -> bool:      # (the other way to write a field of the app document)
        written.append(set(updates))
        return await real_node(key, collection, updates, *args)

    loaded.graph.update_node_fields_if_match = spy  # type: ignore[method-assign]
    loaded.graph.update_node = spy_node  # type: ignore[method-assign]
    await job.run_connector(ORG, app)
    assert written and not any(UNSAFE_KEY in keys for keys in written)


async def test_a_checkpoint_with_a_value_that_is_not_a_number_starts_the_pass_over_instead_of_failing_opaquely() -> None:
    vdb, loaded, job, app = await setup()
    await job.run_connector(ORG, app)
    for key, junk in (("scopeBackfillStructureGeneration", "x"), ("scopeBackfillStructureGeneration", [1]), ("scopeBackfillUnsafe", "x"),
                      ("scopeBackfillStructureGeneration", -1)):
        loaded.graph.app_docs[app]["scopeBackfillState"] = DONE
        loaded.graph.app_docs[app][key] = junk
        p = await job.run_connector(ORG, app)
        assert p.state == DONE and loaded.graph.app_docs[app][key] != junk, (key, junk)       # it started over and wrote its own


async def test_a_progress_built_the_old_positional_way_keeps_its_meaning() -> None:
    p = scope_backfill.Progress("running", "k", 5, 3, 2, 1, None)
    assert (p.state, p.after, p.version, p.generation, p.records, p.stale, p.error) == ("running", "k", 5, 3, 2, 1, None)
    assert p.structure_generation == -1 and p.unsafe is None


def unleased(doc: dict) -> dict:
    """The app document without the rewrite lease: a run takes the connector's lease before it reads anything and gives it back (null) when it stops,
    which is exclusion metadata and not something the run wrote about the connector."""
    return {k: v for k, v in doc.items() if k != "scopeRewriteLease"}


async def test_a_structure_generation_that_is_not_a_number_stops_the_run_before_it_writes_anything() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    for junk in ("x", [], {}, float("nan"), True, "0.5"):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: junk}
        before = dict(loaded.graph.app_docs[app])
        with pytest.raises(UnreadableStructureState):
            await job.run_connector(ORG, app)
        assert unleased(loaded.graph.app_docs[app]).keys() == before.keys() and not any(keys_of(vdb)), junk        # no checkpoint, no point


async def test_a_graph_store_that_refuses_the_checkpoint_stops_the_run_before_anything_else_is_written() -> None:
    """An app schema older than this release does not list the new key: the first write of the run is refused, with a message that says what
    to do, and neither a point nor the unsafe mark is touched."""
    from app.services.vector_db.scope_backfill import CheckpointRefused

    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, UNSAFE_KEY: True}
    real_update = loaded.graph.update_node_fields_if_match

    async def old_schema(key: str, collection: str, updates: dict, *args: object) -> bool:
        if "scopeBackfillStructureGeneration" in updates:
            return False
        return await real_update(key, collection, updates, *args)

    loaded.graph.update_node_fields_if_match = old_schema  # type: ignore[method-assign]
    with pytest.raises(CheckpointRefused, match="restart a connector or indexing service"):
        await job.run_connector(ORG, app)
    assert unleased(loaded.graph.app_docs[app]) == {"_key": app, UNSAFE_KEY: True} and not any(keys_of(vdb))


async def test_a_graph_store_that_refuses_the_lease_gets_the_same_guidance_and_nothing_is_written() -> None:
    """The lease is the first write of a run: an app schema without its key raises a store error, not a conflict."""
    from app.services.vector_db.scope_backfill import CheckpointRefused

    vdb, loaded, job, app = await setup()
    real_update = loaded.graph.update_node_fields_if_match

    async def old_schema(key: str, collection: str, updates: dict, *args: object) -> bool:
        if "scopeRewriteLease" in updates:
            raise RuntimeError("unknown field scopeRewriteLease")
        return await real_update(key, collection, updates, *args)

    loaded.graph.update_node_fields_if_match = old_schema  # type: ignore[method-assign]
    with pytest.raises(CheckpointRefused, match="scopeRewriteLease.*restart a connector or indexing service"):
        await job.run_connector(ORG, app)
    assert not any(keys_of(vdb))


async def test_true_and_the_token_one_are_different_marks_for_the_pass_as_for_the_currency_check() -> None:
    from app.services.permissions.structure_generation import same_mark

    assert not same_mark(True, 1) and not same_mark(1, True) and not same_mark(True, 1.0) and same_mark(True, True) and same_mark(7, 7)
    for first, second in ((1, True), (True, 1)):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, UNSAFE_KEY: first}
        await job.run_connector(ORG, app)
        loaded.graph.app_docs[app][UNSAFE_KEY] = second                         # a mark that equals the recorded one in Python, but is another mark
        assert not app_is_current(loaded.graph.app_docs[app])
        again = await job.run_connector(ORG, app)                                # so the pass starts over instead of finding "nothing to do"
        assert again.unsafe is second or (again.unsafe == second and type(again.unsafe) is type(second)), (first, second)
        assert app_is_current(loaded.graph.app_docs[app])


async def test_writes_that_never_closed_stop_the_run_until_the_operator_closes_them() -> None:
    from app.services.vector_db.scope_backfill import OpenMarkNotClosed

    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, "scopeProjectionOpened": 3, "scopeProjectionClosed": 1}
    before = dict(loaded.graph.app_docs[app])
    with pytest.raises(OpenMarkNotClosed, match="--close-open-mark"):
        await job.run_connector(ORG, app)
    assert unleased(loaded.graph.app_docs[app]) == before and not any(keys_of(vdb))        # nothing written, no point stamped
    assert not app_is_current(loaded.graph.app_docs[app])
    p = await job.run_connector(ORG, app, close_open_mark=True)
    doc = loaded.graph.app_docs[app]
    assert doc["scopeProjectionClosed"] == 3 and isinstance(doc[UNSAFE_KEY], int) and p.state == DONE and p.unsafe == doc[UNSAFE_KEY]
    assert app_is_current(doc)
    again = await job.run_connector(ORG, app)                                      # and nothing is left to close
    assert again.state == DONE


async def test_the_flag_also_closes_a_connector_whose_closed_counter_got_ahead_of_the_opened_one() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, "scopeProjectionOpened": 1, "scopeProjectionClosed": 2}   # closed by hand, then the write finished
    with pytest.raises(UnreadableStructureState):
        await job.run_connector(ORG, app)
    p = await job.run_connector(ORG, app, close_open_mark=True)
    doc = loaded.graph.app_docs[app]
    assert doc["scopeProjectionOpened"] == 1 and doc["scopeProjectionClosed"] == 1 and p.state == DONE and app_is_current(doc)


async def test_an_unreadable_generation_or_mark_stops_the_run_before_the_flag_closes_anything() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    for key in (GENERATION_KEY, UNSAFE_KEY):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, "scopeProjectionOpened": 2, "scopeProjectionClosed": 0, key: "x"}
        before = dict(loaded.graph.app_docs[app])
        with pytest.raises(UnreadableStructureState):
            await job.run_connector(ORG, app, close_open_mark=True)
        assert unleased(loaded.graph.app_docs[app]) == before and not any(keys_of(vdb)), key          # not closed, and the unreadable value is still there


async def test_counters_that_do_not_read_stop_the_run_before_it_writes_anything_even_with_the_flag() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    for opened, closed in (("x", 0), (1, "x"), (-1, 0), (True, 0)):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, "scopeProjectionOpened": opened, "scopeProjectionClosed": closed}
        with pytest.raises(UnreadableStructureState):
            await job.run_connector(ORG, app, close_open_mark=True)
        assert not any(keys_of(vdb)), (opened, closed)


async def test_the_progress_line_shows_the_mark_it_started_from() -> None:
    assert "unsafe=1234" in scope_backfill.Progress(unsafe=1234).line() and "unsafe=None" in scope_backfill.Progress().line()


async def test_a_connector_back_filled_before_a_record_without_a_group_was_left_to_the_request_is_redone() -> None:
    """Version 5 stamped such a record `container`, which hides it from a viewer past the gate who reads no group: finished, it is still not current."""
    from app.services.permissions.scope_fields import current_scope_generation
    from app.services.vector_db import scope_expr as sx
    from app.services.vector_db.filters import ScopeFilter

    from .serving_corpus import SCENARIOS

    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": next(s for s in SCENARIOS if s.id == "REGR-01").final_world()})
    job, app = ScopeBackfill(loaded.graph, vdb, loaded.locator, log, clock=advancing_clock()), loaded.apps[0]
    assert (await job.run_connector(ORG, app)).state == DONE
    pid, point = next((i, q) for i, q in vdb.collections[COLL].items() if q.payload["scopeStamp"] == "legacy" and not q.payload["recordGroupIds"])
    point.payload.update({"scopeStamp": "container", "scopeFieldsVersion": 5})              # what version 5 stamped it
    loaded.graph.app_docs[app]["scopeBackfillVersion"] = 5                  # the install finished its pass under version 5
    assert not app_is_current(loaded.graph.app_docs[app]) and await StampCurrency(loaded.graph).current([app]) == set()
    flt = ScopeFilter(current=frozenset({app}), version=5, generation=current_scope_generation(), defer=True)
    assert not sx.evaluate(sx.admits(flt), point.payload)          # served from that stamp, the record would vanish
    assert (await job.run_connector(ORG, app)).state == DONE
    payload = point.payload
    assert app_is_current(loaded.graph.app_docs[app]) and payload["scopeStamp"] == "legacy"
    served = ScopeFilter(current=frozenset({app}), version=SCOPE_FIELDS_VERSION, generation=current_scope_generation(), defer=True)
    assert sx.evaluate(sx.admits(served), payload)


# ---- a pass that lost the connector's lease writes nothing more ----------------------------------------------------------------------------

WINNER = {"scopeBackfillState": DONE, "scopeBackfillAfter": "zz", "scopeBackfillRecords": 99, "scopeBackfillStale": 0, "scopeBackfillError": None}


def take_over(loaded: Loaded, app: str) -> dict:
    """Another holder takes the lease (its deadline passed) and finishes: the winner's checkpoint is what the app document then says."""
    from app.services.permissions.rewrite_lease import REWRITE_LEASE_KEY

    loaded.graph.app_docs[app].update({REWRITE_LEASE_KEY: '{"h":"winner","u":9999999999}', **WINNER})
    return {k: loaded.graph.app_docs[app][k] for k in WINNER}


def points_before(vdb: InMemoryVectorDB) -> dict:
    return {pid: dict(p.payload) for pid, p in vdb.collections[COLL].items()}


async def test_a_pass_that_lost_the_lease_before_a_page_is_stamped_writes_no_point_and_no_checkpoint() -> None:
    vdb, loaded, job, app = await setup()
    before, real, hit = points_before(vdb), loaded.graph.stamps.nodes, {}

    async def taken(org: str, ids: frozenset[str]) -> dict:      # right after the page's renew
        hit["winner"] = take_over(loaded, app)
        return await real(org, ids)

    loaded.graph.stamps.nodes = taken  # type: ignore[method-assign]
    with pytest.raises(scope_backfill.ConnectorBusy):
        await job.run_connector(ORG, app)
    assert points_before(vdb) == before, "a point was written after the lease was lost"
    assert {k: loaded.graph.app_docs[app][k] for k in WINNER} == hit["winner"], "the lost pass wrote the winner's checkpoint"


async def test_a_pass_that_loses_the_lease_while_a_page_is_stamped_saves_no_checkpoint_for_it() -> None:
    vdb, loaded, job, app = await setup()
    real, hit = job._membership.refresh_membership, {}

    async def taken(*a: object, **k: object) -> set[str]:      # after the page's writes, before its checkpoint
        hit["winner"] = take_over(loaded, app)
        return await real(*a, **k)

    job._membership.refresh_membership = taken  # type: ignore[method-assign]
    with pytest.raises(scope_backfill.ConnectorBusy):
        await job.run_connector(ORG, app)
    assert {k: loaded.graph.app_docs[app][k] for k in WINNER} == hit["winner"]


async def test_a_pass_that_loses_the_lease_before_it_ends_does_not_save_done_over_the_winner() -> None:
    vdb, loaded, job, app = await setup()
    hit = {}

    async def end_of_list(org: str, a: str, after: str | None, limit: int) -> list[str]:
        hit["winner"] = take_over(loaded, app)
        return []

    loaded.graph.stamps.record_page = end_of_list  # type: ignore[method-assign]
    with pytest.raises(scope_backfill.ConnectorBusy):
        await job.run_connector(ORG, app)
    assert {k: loaded.graph.app_docs[app][k] for k in WINNER} == hit["winner"] and loaded.graph.app_docs[app]["scopeBackfillRecords"] == 99


async def test_a_marker_is_not_tried_again_nor_counted_as_a_store_fault_when_the_lease_was_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, loaded, job, app = await setup()
    before, real, hit = points_before(vdb), job._membership.mark_stale, {"tries": 0}

    async def stale(*a: object, **k: object) -> ScopeFields:
        return STALE_SCOPE

    async def counted(*a: object, **k: object) -> None:
        hit["tries"] += 1
        take_over(loaded, app)
        await real(*a, **k)

    monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)
    monkeypatch.setattr(job._membership, "mark_stale", counted)
    with pytest.raises(scope_backfill.ConnectorBusy):
        await job.run_connector(ORG, app)
    assert hit["tries"] == 1 and points_before(vdb) == before


async def test_a_held_up_write_does_not_land_over_a_point_a_newer_writer_fenced() -> None:
    """The store refuses the request (the check and the write are one operation there), and the caller is told to follow the newer fence."""
    from app.services.vector_db import scope_rewrite

    vdb = FencedMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES[WORLD]})
    app, job = loaded.apps[0], ScopeBackfill(loaded.graph, vdb, loaded.locator, log, clock=advancing_clock())
    point = next(iter(vdb.collections[COLL].values()))
    vrid = point.payload["metadata"]["virtualRecordId"]
    epoch, at = await job._membership.begin(app)
    newer = scope_rewrite._micros(at) + 30 * 10**6      # a plausible time: another host's clock a little ahead
    point.payload.update({"scopeFence": newer, "scopeStamp": "newer-writers-field"})
    taken, floor = await job._membership.write_payload(ORG, app, [COLL], vrid, ScopeFields.legacy().to_payload(), epoch, at)
    assert not taken and floor > newer and point.payload["scopeStamp"] == "newer-writers-field" and point.payload["scopeFence"] == newer


async def test_a_point_with_an_implausible_fence_is_refused_once_named_and_ends_on_the_stale_marker(caplog: pytest.LogCaptureFixture) -> None:
    from app.services.vector_db import scope_rewrite

    vdb = FencedMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES[WORLD]})
    app, job = loaded.apps[0], ScopeBackfill(loaded.graph, vdb, loaded.locator, log, clock=advancing_clock())
    far = scope_rewrite._micros(scope_rewrite._clock()) + 3600 * 10**6      # an hour ahead: beyond the skew that is followed
    for point in vdb.collections[COLL].values():
        point.payload.update({"scopeFence": far, "scopeStamp": "held"})
    fenced, real = [], vdb.update_payloads_by_ids

    async def spy(name: str, updates: dict, **kw: object) -> None:
        if kw.get("fence") is not None:
            fenced.append(set(updates))
        await real(name, updates, **kw)

    vdb.update_payloads_by_ids = spy  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        p = await job.run_connector(ORG, app)
    vrids = {pt.payload["metadata"]["virtualRecordId"] for pt in vdb.collections[COLL].values()}
    assert p.state == DONE and p.stale == len(vrids)
    assert caplog.text.count("is not a plausible time") == len(vrids)                      # not three per record
    assert all(f"virtual record {v} carries a fence" in caplog.text for v in vrids)       # the operator can tell which to index again
    assert len(fenced) == len(vrids)      # one refused write per record (the stale marker is unfenced), not one per round
    assert all(pt.payload.get("scopeFieldsVersion") == 0 and pt.payload["scopeStamp"] != "held" for pt in vdb.collections[COLL].values())


async def test_a_connector_that_moves_under_a_record_costs_no_round(monkeypatch: pytest.MonkeyPatch) -> None:
    vdb, _, job, app = await setup()
    moves = {"n": 0}
    real = job._membership.write_payload

    async def moved_twice_then_right(*a, **k) -> tuple[bool, int]:
        moves["n"] += 1
        if moves["n"] <= scope_backfill.MAX_ROUNDS + 2:      # more moves than rounds
            raise GenerationMoved("moved")
        return await real(*a, **k)

    monkeypatch.setattr(job._membership, "write_payload", moved_twice_then_right)
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.stale == 0 and moves["n"] > scope_backfill.MAX_ROUNDS + 2


async def test_a_page_is_scanned_once_not_once_per_virtual_record(monkeypatch: pytest.MonkeyPatch) -> None:
    vdb, loaded, job, app = await setup()
    scans, real = [], job._membership._scan

    async def spy(org_id: str, collection: str, vrids: list, keys: tuple = ()) -> dict:
        scans.append(len(vrids))
        return await real(org_id, collection, vrids, keys)

    monkeypatch.setattr(job._membership, "_scan", spy)
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.stale == 0 and len(scans) >= 1 and 1 not in scans      # (every scan covers the page's virtual records)
    want, got = await expected(loaded), await read_stamps(vdb, loaded)
    assert got == want


@pytest.mark.parametrize(("world", "exact"), [("stamped:KB-C-01", True), ("stamped:GM-C-03", False), ("derived:SCOPES-ADV-04", False)])
async def test_a_connector_is_exact_only_when_the_pass_left_no_legacy_or_stale_point(world: str, exact: bool) -> None:
    """GM-C-03 has records the stamper refuses (their points are legacy), ADV-04 has one on the stale marker."""
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES[world]})
    app = loaded.apps[0]
    p = await ScopeBackfill(loaded.graph, vdb, loaded.locator, log, clock=advancing_clock()).run_connector(ORG, app)
    assert p.state == DONE and p.exact is exact and (p.legacy == 0 and p.stale == 0) is exact
    assert loaded.graph.app_docs[app]["scopeBackfillExact"] is exact
    assert (await status(loaded.graph, ORG, app)).exact is exact       # and it reads back


def test_a_pass_that_is_not_done_is_not_exact() -> None:
    assert scope_backfill.Progress(state=DONE).exact and not scope_backfill.Progress(state=RUNNING).exact and not scope_backfill.Progress(state=FAILED).exact
    assert not scope_backfill.Progress(state=DONE, stale=1).exact and not scope_backfill.Progress(state=DONE, legacy=1).exact


@pytest.mark.parametrize("saved", [None, "x", -3, True, 1.5])
async def test_a_checkpoint_without_a_readable_legacy_count_is_not_exact_and_the_next_pass_starts_over(saved: object) -> None:
    """A missing or unreadable count is not "none left": the connector is not exact, and a finished pass of that checkpoint is made again."""
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:KB-C-01"]})
    app = loaded.apps[0]
    job = ScopeBackfill(loaded.graph, vdb, loaded.locator, log, clock=advancing_clock())
    assert (await job.run_connector(ORG, app)).exact
    doc = loaded.graph.app_docs[app]
    doc["scopeBackfillExact"] = True
    if saved is None:
        del doc["scopeBackfillLegacy"]
    else:
        doc["scopeBackfillLegacy"] = saved
    assert not (await status(loaded.graph, ORG, app)).exact
    again = await job.run_connector(ORG, app)
    assert again.exact and again.legacy == 0 and loaded.graph.app_docs[app]["scopeBackfillExact"] is True
