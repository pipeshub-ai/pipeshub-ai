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
    real, failures = vdb.set_payload, {"left": 1}

    async def once(*a, **k) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("store down")
        await real(*a, **k)

    vdb.set_payload = once  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app)
    assert p.stale > 0 and sum(f == STALE_SCOPE for f in (await read_stamps(vdb, loaded)).values()) == p.stale  # the first failure was retried

    async def never(*a, **k) -> None:
        raise RuntimeError("store down")

    vdb.set_payload = never  # type: ignore[method-assign]
    calls["n"] = 0
    with caplog.at_level(logging.ERROR):
        again = await job.run_connector(ORG, app, restart=True)
    assert again.stale > 0 and "could not be marked stale" in caplog.text


async def test_a_point_that_keeps_its_own_fields_because_the_marker_cannot_be_written_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
    vdb, loaded, job, app = await setup()
    real_write, state = vdb.set_payload, {"stamped": False}

    async def broken(name: str, payload: dict, flt) -> None:
        if payload.get("scopeStamp") == "legacy" and payload.get("scopeFieldsVersion") == 0:
            raise RuntimeError("marker store down")
        await real_write(name, payload, flt)
        state["stamped"] = True
        loaded.graph.stamps.failure = RuntimeError("graph down after the first write")  # the read-back then fails

    vdb.set_payload = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        p = await job.run_connector(ORG, app)
    assert state["stamped"] and p.stale >= 1 and "could not be marked stale" in caplog.text


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

    async def save_once_failing(a: str, p: scope_backfill.Progress) -> None:
        saves["n"] += 1
        if saves["n"] == 2:                      # the first save is the unfinished checkpoint of a fresh start; the second is the first page's
            raise RuntimeError("checkpoint store down")
        await real_save(a, p)

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
    real_update, written = loaded.graph.update_node, []

    async def spy(key: str, collection: str, updates: dict, *args: object) -> bool:
        written.append(set(updates))
        return await real_update(key, collection, updates, *args)

    loaded.graph.update_node = spy  # type: ignore[method-assign]
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


async def test_a_structure_generation_that_is_not_a_number_stops_the_run_before_it_writes_anything() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    for junk in ("x", [], {}, float("nan"), True, "0.5"):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: junk}
        before = dict(loaded.graph.app_docs[app])
        with pytest.raises(UnreadableStructureState):
            await job.run_connector(ORG, app)
        assert loaded.graph.app_docs[app].keys() == before.keys() and not any(keys_of(vdb)), junk        # no checkpoint, no point


async def test_a_graph_store_that_refuses_the_checkpoint_stops_the_run_before_anything_else_is_written() -> None:
    """An app schema older than this release does not list the new key: the first write of the run is refused, with a message that says what
    to do, and neither a point nor the unsafe mark is touched."""
    from app.services.vector_db.scope_backfill import CheckpointRefused

    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, UNSAFE_KEY: True}
    real_update = loaded.graph.update_node

    async def old_schema(key: str, collection: str, updates: dict, *args: object) -> bool:
        if "scopeBackfillStructureGeneration" in updates:
            return False
        return await real_update(key, collection, updates, *args)

    loaded.graph.update_node = old_schema  # type: ignore[method-assign]
    with pytest.raises(CheckpointRefused, match="restart a connector or indexing service"):
        await job.run_connector(ORG, app)
    assert loaded.graph.app_docs[app] == {"_key": app, UNSAFE_KEY: True} and not any(keys_of(vdb))


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
    assert loaded.graph.app_docs[app] == before and not any(keys_of(vdb))        # nothing written, no point stamped
    assert not app_is_current(loaded.graph.app_docs[app])
    p = await job.run_connector(ORG, app, close_open_mark=True)
    doc = loaded.graph.app_docs[app]
    assert doc["scopeProjectionClosed"] == 3 and isinstance(doc[UNSAFE_KEY], int) and p.state == DONE and p.unsafe == doc[UNSAFE_KEY]
    assert app_is_current(doc)
    again = await job.run_connector(ORG, app)                                      # and nothing is left to close
    assert again.state == DONE


async def test_counters_that_do_not_read_stop_the_run_before_it_writes_anything_even_with_the_flag() -> None:
    from app.services.vector_db.scope_backfill import UnreadableStructureState

    for opened, closed in (("x", 0), (1, "x"), (1, 2), (-1, 0), (True, 0)):
        vdb, loaded, job, app = await setup()
        loaded.graph.app_docs[app] = {"_key": app, "scopeProjectionOpened": opened, "scopeProjectionClosed": closed}
        with pytest.raises(UnreadableStructureState):
            await job.run_connector(ORG, app, close_open_mark=True)
        assert not any(keys_of(vdb)), (opened, closed)


async def test_the_progress_line_shows_the_mark_it_started_from() -> None:
    assert "unsafe=1234" in scope_backfill.Progress(unsafe=1234).line() and "unsafe=None" in scope_backfill.Progress().line()
