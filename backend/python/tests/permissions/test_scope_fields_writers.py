"""Writers: the scope fields ride on the membership write (`membership.py`, one place for every store), behind the flag."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Never

import pytest

from app.core import org_context as oc
from app.services.permissions.mode import (
    ENV_ACL_FILTER_MODE,
    ENV_SCOPE_FIELDS,
    scope_fields_enabled,
)
from app.services.permissions.projector import ScopeProjector
from app.services.permissions.scope_fields import (
    ENV_SCOPE_GENERATION,
    SCOPE_FIELDS_VERSION,
    SCOPE_KEYS,
    SCOPE_VERSION_FIELD,
    ScopeFields,
    Stamp,
)
from app.services.vector_db import membership
from app.services.vector_db.membership import (
    STALE_SCOPE,
    reset_membership_context,
    set_membership_context,
    sync_vector_membership,
    vector_point_payload,
    write_membership_batch_locked,
)
from app.services.vector_db.models import VectorPoint
from tests.support.in_memory_vector_db import (
    InMemoryVectorDBService as InMemoryVectorDB,
)

from .stamp_world import ORG
from .store_harness import Loaded, load, read_stamps
from .test_scope_projector import CASES

if TYPE_CHECKING:
    from collections.abc import Iterator

COLL = "scope_it"
log = logging.getLogger("t")


async def sync_all(loaded, vdb) -> None:
    for v in loaded.records:
        await sync_vector_membership(vdb, loaded.locator, loaded.graph, v, log)


@pytest.fixture
def flag_on(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    with oc.for_org(ORG, job="scope-fields-test"):
        yield


def test_scope_fields_are_off_unless_asked_for() -> None:
    assert not scope_fields_enabled({})
    assert not scope_fields_enabled({ENV_ACL_FILTER_MODE: "off"})
    assert scope_fields_enabled({ENV_SCOPE_FIELDS: "1"})
    assert scope_fields_enabled({ENV_ACL_FILTER_MODE: "shadow"})  # a filter that reads the fields needs them written
    assert not scope_fields_enabled({ENV_SCOPE_FIELDS: "maybe"})


async def test_with_the_flag_off_no_scope_key_is_written_and_the_graph_is_not_asked(monkeypatch) -> None:
    monkeypatch.delenv(ENV_SCOPE_FIELDS, raising=False)
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})

    def boom() -> Never:
        raise AssertionError("the stamp graph must not be read while scope fields are off")

    loaded.graph.permission_stamp_graph = boom  # type: ignore[method-assign]
    await sync_all(loaded, vdb)
    assert all(not set(SCOPE_KEYS) & set(p.payload) for p in vdb.collections[COLL].values())
    assert all(p.payload["connectorIds"] for p in vdb.collections[COLL].values())  # membership is written as ever


async def test_with_the_flag_on_the_membership_write_carries_the_fields(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    await sync_all(loaded, vdb)
    stamps = await read_stamps(vdb, loaded)
    assert stamps and all(f is not None and f.stamp is Stamp.CONTAINER and f.version >= 1 for f in stamps.values())


async def test_a_refused_record_is_written_legacy_with_the_current_version(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["derived:SCOPES-ADV-06"]})
    await sync_all(loaded, vdb)
    stamps = await read_stamps(vdb, loaded)
    legacy = [f for f in stamps.values() if f.stamp is Stamp.LEGACY]
    assert legacy and all(f.version == SCOPE_FIELDS_VERSION and not f.path_ids for f in legacy)


async def test_a_projection_failure_never_fails_the_membership_write_and_leaves_a_stale_marker(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    loaded.graph.stamps.failure = RuntimeError("graph down")
    await sync_all(loaded, vdb)
    stamps = await read_stamps(vdb, loaded)
    assert all(f == STALE_SCOPE for f in stamps.values())  # version 0: legacy filter, redone by the backfill


async def test_losing_records_marks_the_points_stale_so_no_old_stamp_admits_through_a_gone_record(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    await sync_all(loaded, vdb)
    v = next(iter(loaded.records))
    await write_membership_batch_locked(vdb, COLL, [v], ["gone"], None, log)
    point = (await vdb.retrieve_points(COLL, loaded.points[v]))[0]
    assert ScopeFields.from_payload(point.payload) == STALE_SCOPE


def test_new_points_carry_the_fields_of_the_bound_context() -> None:
    tokens = set_membership_context(["c"], ["g"], ["r"], ScopeFields(Stamp.UNION, ("a", "b")))
    try:
        payload = vector_point_payload({"orgId": ORG}, "text")
        assert ScopeFields.from_payload(payload) == ScopeFields(Stamp.UNION, ("a", "b"))
        assert payload["connectorIds"] == ["c"]
    finally:
        reset_membership_context(tokens)
    assert not set(SCOPE_KEYS) & set(vector_point_payload({}, ""))  # nothing leaks out of the context
    tokens = set_membership_context(["c"], ["g"])
    try:
        assert SCOPE_VERSION_FIELD not in vector_point_payload({}, "")  # a context without fields writes none
    finally:
        reset_membership_context(tokens)


def test_one_writer_path_builds_the_fields() -> None:
    """`vectorstore.py` (new points) and `membership.sync_vector_membership` (updates) both call into `membership.py`."""
    import inspect

    from app.modules.transformers import vectorstore

    assert "scope_fields_for_state" in inspect.getsource(vectorstore)
    assert "scope_fields_for_state" in inspect.getsource(membership._sync_vector_membership_locked)


async def test_with_no_org_bound_the_point_gets_the_stale_marker_and_the_graph_is_not_read(monkeypatch) -> None:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    loaded.graph.stamps.failure = AssertionError("no graph read without an org")
    await sync_all(loaded, vdb)
    assert all(f == STALE_SCOPE for f in (await read_stamps(vdb, loaded)).values())


async def test_the_write_touches_only_the_points_of_the_bound_org(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    v = next(iter(loaded.records))
    other = {**(await vdb.retrieve_points(COLL, loaded.points[v][:1]))[0].payload}
    other["metadata"] = {**other["metadata"], "orgId": "cd" * 12}
    await vdb.upsert_points(COLL, [VectorPoint(id="other-org-point", dense_vector=[0.0] * 8, payload=other)])
    await sync_all(loaded, vdb)
    assert not set(SCOPE_KEYS) & set(vdb.collections[COLL]["other-org-point"].payload)


async def test_with_the_flag_off_a_purge_and_new_points_carry_no_scope_key_and_the_graph_is_not_asked(monkeypatch) -> None:
    from app.modules.transformers.vectorstore import VectorStore

    monkeypatch.delenv(ENV_SCOPE_FIELDS, raising=False)
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    v = next(iter(loaded.records))
    await write_membership_batch_locked(vdb, COLL, [v], ["gone"], None, log)
    assert not set(SCOPE_KEYS) & set((await vdb.retrieve_points(COLL, loaded.points[v]))[0].payload)

    def boom() -> Never:
        raise AssertionError("the stamp graph must not be read while scope fields are off")

    loaded.graph.permission_stamp_graph = boom  # type: ignore[method-assign]
    store = VectorStore.__new__(VectorStore)
    store.graph_provider, store.logger = loaded.graph, log
    tokens = await store._bind_membership(v)
    try:
        assert not set(SCOPE_KEYS) & set(vector_point_payload({}, ""))
    finally:
        reset_membership_context(tokens)


async def _stamp_state(vdb: InMemoryVectorDB, loaded: Loaded, v: str) -> ScopeFields | None:
    return ScopeFields.from_payload((await vdb.retrieve_points(COLL, loaded.points[v][:1]))[0].payload)


async def test_a_stamp_from_before_the_latest_enable_is_never_trusted(monkeypatch) -> None:
    """enable, stamp, disable, the membership changes, enable again: the old stamp is stale (older generation), not trusted."""
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    v = next(iter(loaded.records))
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    monkeypatch.setenv(ENV_SCOPE_GENERATION, "1")
    with oc.for_org(ORG, job="scope-fields-test"):
        await sync_all(loaded, vdb)
    first = await _stamp_state(vdb, loaded, v)
    assert first.trusted and first.generation == 1
    monkeypatch.delenv(ENV_SCOPE_FIELDS)  # off: a membership write leaves the scope keys alone, however the graph changed
    for rid in loaded.records[v]:
        n = loaded.graph.stamps.node_map[rid]
        loaded.graph.stamps.node_map = {**loaded.graph.stamps.node_map, rid: replace(n, rule="RESTRICTED", granted=True)}
    await sync_all(loaded, vdb)
    assert await _stamp_state(vdb, loaded, v) == first
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    monkeypatch.setenv(ENV_SCOPE_GENERATION, "2")  # the operator raises the generation when switching on again
    assert not (await _stamp_state(vdb, loaded, v)).trusted
    with oc.for_org(ORG, job="scope-fields-test"):
        await sync_all(loaded, vdb)
    again = await _stamp_state(vdb, loaded, v)
    assert again.generation == 2 and again != first


def test_a_stamp_without_a_generation_or_of_another_version_is_not_trusted() -> None:
    payload = ScopeFields.container().to_payload()
    assert ScopeFields.from_payload(payload).trusted
    assert not ScopeFields.from_payload({k: x for k, x in payload.items() if k != "scopeGeneration"}).trusted
    assert not ScopeFields.from_payload({**payload, "scopeFieldsVersion": SCOPE_FIELDS_VERSION - 1}).trusted
    assert not STALE_SCOPE.trusted and not ScopeFields.legacy().trusted


async def test_one_projector_reads_an_apps_facts_once_however_many_points_it_projects() -> None:
    from .stamp_world import stamp_graph

    world = CASES["derived:SCOPES-ADV-01"]
    graph = stamp_graph(world)
    app = next(n.id for n in world.nodes if n.kind == "app")
    recs = [n.id for n in world.nodes if n.kind == "record" and n.connector == app]
    projector = ScopeProjector(graph)
    for r in recs:
        await projector.project(ORG, app, [r])
    assert len(recs) > 3 and graph.app_reads == 1


async def test_a_point_whose_records_are_all_skipped_gets_the_stale_marker_not_a_current_legacy_stamp() -> None:
    from .stamp_world import stamp_graph

    world = CASES["stamped:SLK-C-01"]
    graph = stamp_graph(world)
    node_map = {i: replace(n, deleted=True) if n.kind == "record" else n for i, n in graph.node_map.items()}
    graph.node_map = node_map
    recs = {i: n.connector_id for i, n in node_map.items() if n.kind == "record"}
    fields, _ = await ScopeProjector(graph).project_virtual_record(ORG, recs)
    assert fields == STALE_SCOPE and fields.version == 0


async def test_with_the_flag_on_a_point_without_an_org_still_gets_its_membership_refreshed(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    for p in vdb.collections[COLL].values():
        p.payload["metadata"].pop("orgId")
        p.payload["connectorIds"] = ["old"]
        p.payload["recordGroupIds"] = ["old"]
    await sync_all(loaded, vdb)
    assert all(p.payload["connectorIds"] != ["old"] and p.payload["recordGroupIds"] != ["old"] for p in vdb.collections[COLL].values())
    assert not any(set(SCOPE_KEYS) & set(p.payload) for p in vdb.collections[COLL].values())  # no org: no stamp, so the legacy filter


async def test_a_sync_that_writes_nothing_leaves_the_stale_marker_when_the_flag_is_on(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    await sync_all(loaded, vdb)
    v = next(iter(loaded.records))
    assert (await _stamp_state(vdb, loaded, v)).trusted
    loaded.graph.stamps.node_map = {i: replace(n, deleted=True) if n.kind == "record" else n for i, n in loaded.graph.stamps.node_map.items()}
    await sync_all(loaded, vdb)  # no record resolves: nothing to write, and the old stamp must not stay
    assert await _stamp_state(vdb, loaded, v) == STALE_SCOPE


async def test_the_purge_marker_matches_only_the_points_of_the_bound_org(flag_on) -> None:
    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    v = next(iter(loaded.records))
    mine = (await vdb.retrieve_points(COLL, loaded.points[v][:1]))[0].payload
    other = {**mine, "metadata": {**mine["metadata"], "orgId": "cd" * 12}}
    await vdb.upsert_points(COLL, [VectorPoint(id="other-org-point", dense_vector=[0.0] * 8, payload=other)])
    await write_membership_batch_locked(vdb, COLL, [v], ["gone"], None, log)
    assert not set(SCOPE_KEYS) & set(vdb.collections[COLL]["other-org-point"].payload)
    assert ScopeFields.from_payload(vdb.collections[COLL][loaded.points[v][0]].payload) == STALE_SCOPE


async def test_the_summary_point_gets_the_same_stamp_as_its_siblings(flag_on) -> None:
    """`index_record_summary` binds the membership of a virtual record with no record in hand: the organisation then comes from the
    ambient context, as in the other writers, not from a record that is not there."""
    from app.modules.transformers.vectorstore import VectorStore

    vdb = InMemoryVectorDB()
    loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
    v = next(iter(loaded.records))
    await sync_all(loaded, vdb)
    sibling = await _stamp_state(vdb, loaded, v)
    assert sibling.trusted
    store = VectorStore.__new__(VectorStore)
    store.graph_provider, store.logger = loaded.graph, log
    tokens = await store._bind_membership(v)  # no record: the summary point
    try:
        point = vector_point_payload({}, "summary")
    finally:
        reset_membership_context(tokens)
    assert ScopeFields.from_payload(point) == sibling


async def test_the_summary_point_takes_the_org_its_caller_names_when_none_is_bound(monkeypatch) -> None:
    from app.modules.transformers.vectorstore import VectorStore

    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    vdb = InMemoryVectorDB()
    with oc.for_org(ORG, job="scope-fields-test"):
        loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
        v = next(iter(loaded.records))
        await sync_all(loaded, vdb)
        sibling = await _stamp_state(vdb, loaded, v)
    store = VectorStore.__new__(VectorStore)
    store.graph_provider, store.logger = loaded.graph, log
    for org, expected in ((None, STALE_SCOPE), (ORG, sibling)):  # nothing is bound here
        tokens = await store._bind_membership(v, org_id=org)
        try:
            point = vector_point_payload({}, "summary")
        finally:
            reset_membership_context(tokens)
        assert ScopeFields.from_payload(point) == expected


async def test_the_stale_marker_is_written_even_when_no_org_is_bound(monkeypatch) -> None:
    """Deliberate: the marker is version 0 (never trusted), so writing it by virtual record id alone can only withdraw trust; skipping
    the write would leave an older stamp on points the failed resync could not recompute."""
    from app.services.vector_db.membership import mark_scope_stale

    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    vdb = InMemoryVectorDB()
    with oc.for_org(ORG, job="scope-fields-test"):
        loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
        await sync_all(loaded, vdb)
    v = next(iter(loaded.records))
    point = (await vdb.retrieve_points(COLL, loaded.points[v]))[0]
    assert ScopeFields.from_payload(point.payload).trusted
    await mark_scope_stale(vdb, loaded.locator, v, log)  # nothing bound here
    point = (await vdb.retrieve_points(COLL, loaded.points[v]))[0]
    assert ScopeFields.from_payload(point.payload) == STALE_SCOPE


async def _summary_store(vdb, loaded, on_write) -> object:
    from unittest.mock import AsyncMock

    from app.modules.transformers.vectorstore import VectorStore

    store = VectorStore.__new__(VectorStore)
    store.graph_provider, store.logger, store.vector_db_service, store.collection_locator = loaded.graph, log, vdb, loaded.locator
    store.dense_embeddings, store.embedding_size = object(), 8
    store._build_record_summary_document = lambda *a, **k: object()
    store._ensure_collection = AsyncMock(return_value=COLL)
    store.delete_blocks_by_ids = AsyncMock()
    store._record_is_gone = AsyncMock(return_value=False)
    store._process_document_chunks = on_write
    return store


async def test_a_membership_change_while_the_summary_is_embedded_does_not_leave_a_trusted_stale_stamp(monkeypatch) -> None:
    """The interleaving, without a sleep: the stamp is read, the graph changes (in the writer standing in for the embedding), then the
    point lands with the stamp that was read. The summary is resynced after the write, so what it carries is what the graph says now."""
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    vdb = InMemoryVectorDB()
    with oc.for_org(ORG, job="scope-fields-test"):
        loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
        await sync_all(loaded, vdb)
    v = next(iter(loaded.records))
    before = ScopeFields.from_payload((await vdb.retrieve_points(COLL, loaded.points[v][:1]))[0].payload)
    assert before.trusted

    async def embed_and_write(docs, record_id, coll) -> None:
        nodes = loaded.graph.stamps.node_map  # a restriction arrives while the embedding runs
        loaded.graph.stamps.node_map = {
            **nodes, **{i: replace(n, rule="RESTRICTED", granted=True) for i, n in nodes.items() if n.kind == "record" and n.vrid == v}}
        payload = vector_point_payload({"orgId": ORG, "virtualRecordId": v}, "summary")  # the stamp bound before the change
        await vdb.upsert_points(COLL, [VectorPoint(id="summary-point", dense_vector=[0.0] * 8, payload={**payload, "metadata": {"orgId": ORG, "virtualRecordId": v}})])

    store = await _summary_store(vdb, loaded, embed_and_write)
    await store.index_record_summary("rid", v, ORG, object())  # no org bound by the caller: it binds the one it names
    got = ScopeFields.from_payload(vdb.collections[COLL]["summary-point"].payload)
    with oc.for_org(ORG, job="scope-fields-test"):
        want = (await ScopeProjector(loaded.graph.stamps).project_virtual_record(ORG, loaded.records[v]))[0]
    assert got == want and got != before


async def test_the_summary_is_stamped_under_enforced_org_context_with_none_bound_by_the_caller(monkeypatch) -> None:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    vdb = InMemoryVectorDB()
    with oc.for_org(ORG, job="scope-fields-test"):
        loaded = await load(vdb, COLL, {"w": CASES["stamped:SLK-C-01"]})
        await sync_all(loaded, vdb)
    v = next(iter(loaded.records))
    seen: list[str | None] = []

    async def write(docs, record_id, coll) -> None:
        seen.append(oc.current_org_id())  # what the stamp graph's checks read

    store = await _summary_store(vdb, loaded, write)
    await store.index_record_summary("rid", v, ORG, object())
    assert seen == [ORG]
