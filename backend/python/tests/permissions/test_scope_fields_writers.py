"""Writers: the scope fields ride on the membership write (`membership.py`, one place for every store), behind the flag."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Never

import pytest

from app.core import org_context as oc
from app.services.permissions.mode import (
    ENV_ACL_FILTER_MODE,
    ENV_SCOPE_FIELDS,
    scope_fields_enabled,
)
from app.services.permissions.scope_fields import (
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
from tests.support.in_memory_vector_db import (
    InMemoryVectorDBService as InMemoryVectorDB,
)

from .stamp_world import ORG
from .store_harness import load, read_stamps
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
    from app.services.vector_db.models import VectorPoint
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
