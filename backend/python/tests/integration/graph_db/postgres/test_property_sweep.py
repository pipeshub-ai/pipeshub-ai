"""The property projection sweep's reads, index and state document on PostgreSQL, against a real database.

The same scenarios as ``tests/integration/graph_permissions/test_property_projection_sweep.py`` runs on Neo4j and ArangoDB: both selections
agree with the Python reference of the staleness test, the change seek pages by ``(updatedAtTimestamp, id)`` without splitting a tie, a record
stamped far in the future does not hide later changes, a trashed record is never selected, the index is the revision's (valid, found by the
sweep), and the epoch on the connector's ``apps`` document is a compare-and-set that exactly one of several claimants wins.

Needs a migrated database (graph_0021): PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors
from app.modules.indexing.property_projection_sweep import (
    GraphStateStore,
    Scope,
    SweepState,
)
from tests.unit.modules.indexing.sweep_world import stale

from .test_identity import w as _identity_world  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from app.core.db import KernelDB

    from .test_access import World

pytestmark = pytest.mark.integration

HASH, GEN = "0123456789abcdef", 2
CONNECTOR = "kb-sweep"


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


def _rec(w: World, key: str, *, updated: int = 5_000, connector: str = CONNECTOR, **fields: object) -> dict:
    return {"_key": key, "recordName": key, "connectorId": connector, "orgId": w.org, "updatedAtTimestamp": updated, **fields}


_WORLDS = {
    "never": {},
    "current": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_000},
    "other_set": {"propsSet": "ffffffffffffffff", "propsGen": GEN, "propsSrc": 5_000},
    "older_gen": {"propsSet": HASH, "propsGen": GEN - 1, "propsSrc": 5_000},
    "newer_gen": {"propsSet": HASH, "propsGen": GEN + 1, "propsSrc": 5_000},
    "older_stamp": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 4_999},
    "newer_stamp": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_001},
    "source_stamp_newer": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_000, "sourceLastModifiedTimestamp": 6_000},
    "source_stamp_current": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 6_000, "sourceLastModifiedTimestamp": 6_000},
    "stamp_zero": {"updatedAtTimestamp": 0},
    "string_gen": {"propsSet": HASH, "propsGen": "2", "propsSrc": 5_000},
    "float_stamp": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_000, "updatedAtTimestamp": 5_000.5},
    "deleted": {"isDeleted": True},
    "not_deleted_flag": {"isDeleted": False},
}


async def _seek(w: World, connector: str = CONNECTOR, *, since: int = 0, after: str = "", limit: int = 500) -> list[dict]:
    return await w.p.get_records_updated_since(connector, w.org, since=since, after_id=after, props_hash=HASH, props_gen=GEN, limit=limit)


async def _walk(w: World, connector: str = CONNECTOR, *, after: str | None = None, limit: int = 500) -> list[dict]:
    return await w.p.get_records_needing_projection(connector, w.org, after_key=after, props_hash=HASH, props_gen=GEN, limit=limit)


async def test_both_selections_agree_with_the_python_reference(w: World) -> None:
    """Kills: a statement that differs from the reference in what it calls stale (either selection), a number tested as text, a trashed record read."""
    docs = {name: _rec(w, name, **extra) for name, extra in _WORLDS.items()}
    await w.nodes("records", *docs.values())
    expected = {d["_key"] for d in docs.values() if d.get("isDeleted") is not True and stale(d, HASH, GEN)}
    assert expected, "the world holds stale records"
    assert {r["_key"] for r in await _seek(w)} == expected
    assert {r["_key"] for r in await _walk(w)} == expected
    assert {r["_key"] for r in await _seek(w, connector="another-connector")} == set()
    assert {r["_key"] for r in await _walk(w, connector="another-connector")} == set()


async def test_a_trashed_record_is_selected_by_neither(w: World) -> None:
    await w.nodes("records", _rec(w, "live"), _rec(w, "trashed", isDeleted=True))
    assert [r["_key"] for r in await _seek(w)] == ["live"]
    assert [r["_key"] for r in await _walk(w)] == ["live"]


async def test_records_come_back_whole_with_their_key(w: World) -> None:
    await w.nodes("records", _rec(w, "one", webUrl="https://x/1"))
    (row,) = await _seek(w)
    assert row["_key"] == "one" and row["webUrl"] == "https://x/1" and row["connectorId"] == CONNECTOR


async def test_the_seek_pages_through_a_tie_without_splitting_it(w: World) -> None:
    """3000 records with one write time, page 500. Kills: a keyset on the time alone, or a cursor without the id."""
    docs = [_rec(w, f"t{i:04d}", updated=7_000) for i in range(3000)]
    for start in range(0, len(docs), 500):
        await w.nodes("records", *docs[start:start + 500])
    seen: list[str] = []
    since, after = 0, ""
    while True:
        page = await _seek(w, since=since, after=after, limit=500)
        seen += [r["_key"] for r in page]
        if len(page) < 500:
            break
        since, after = page[-1]["updatedAtTimestamp"], page[-1]["_key"]
    assert seen == sorted(d["_key"] for d in docs), "every record once, in (time, id) order"


async def test_the_walk_pages_by_id(w: World) -> None:
    await w.nodes("records", *[_rec(w, f"k{i:03d}") for i in range(25)])
    seen: list[str] = []
    after: str | None = None
    while True:
        page = await _walk(w, after=after, limit=10)
        seen += [r["_key"] for r in page]
        if len(page) < 10:
            break
        after = page[-1]["_key"]
    assert seen == [f"k{i:03d}" for i in range(25)]


async def test_one_record_stamped_years_ahead_does_not_hide_later_changes(w: World) -> None:
    """A future-stamped record sorts last in the seek; the records after the cursor are still found. Kills: reading the cursor as a maximum."""
    await w.nodes("records", _rec(w, "future", updated=9_999_999_999_999), _rec(w, "a", updated=1_000), _rec(w, "b", updated=2_000))
    assert [r["_key"] for r in await _seek(w, since=1_500)] == ["b", "future"]
    assert [r["_key"] for r in await _seek(w, since=2_000, after="b")] == ["future"]


async def test_a_fractional_write_time_is_paged_exactly(w: World) -> None:
    """1200 records share the write time 5000.5. A cursor cut to 5000 would read the tie again; the numeric parameter keeps it.
    Kills: a bigint parameter or int() on the cursor."""
    await w.nodes("records", *[_rec(w, f"f{i:04d}", updated=5_000.5) for i in range(1200)])
    seen: list[str] = []
    since: int | float = 0
    after = ""
    while True:
        page = await _seek(w, since=since, after=after, limit=500)
        seen += [r["_key"] for r in page]
        if len(page) < 500:
            break
        since, after = page[-1]["updatedAtTimestamp"], page[-1]["_key"]
    assert seen == [f"f{i:04d}" for i in range(1200)]
    assert [r["_key"] for r in await _seek(w, since=5_000.5, after="f1198")] == ["f1199"]
    assert await _seek(w, since=5_000.6) == []


async def test_the_seek_is_bounded_and_refuses_a_page_it_cannot_serve(w: World) -> None:
    """A page over the function's bound raises (the sweep would otherwise read a short page as the end). Kills: clamping the limit."""
    with pytest.raises(ValueError):
        await _seek(w, limit=1001)
    with pytest.raises(ValueError):
        await _walk(w, limit=1001)


async def test_the_selection_is_the_callers_org_only(w: World, org_ctx: Callable[[str], AbstractContextManager]) -> None:
    await w.nodes("records", _rec(w, "mine"))
    with org_ctx("an-other-org"):
        assert await w.p.get_records_updated_since(CONNECTOR, "an-other-org", since=0, after_id="", props_hash=HASH, props_gen=GEN, limit=10) == []
        assert await w.p.get_records_needing_projection(CONNECTOR, "an-other-org", after_key=None, props_hash=HASH, props_gen=GEN, limit=10) == []


async def test_the_change_index_is_the_revisions_and_is_valid(w: World) -> None:
    """graph_0021 builds nodes_record_change before the upgrade commits. Kills: DDL at run time, an index that is not valid read as online."""
    await w.p.ensure_record_change_index()
    await w.p.ensure_record_change_index()
    assert await w.p.record_change_index_online() is True


async def test_the_online_check_is_false_for_an_invalid_index(w: World, pg_db: KernelDB) -> None:
    """An invalid index (a stopped build) is not online. Kills: reading existence as validity."""
    await pg_db.run(lambda tx: tx.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = to_regclass('graph.nodes_record_change')"), write=True)
    try:
        assert await w.p.record_change_index_online() is False
    finally:
        await pg_db.run(lambda tx: tx.execute("UPDATE pg_index SET indisvalid = true WHERE indexrelid = to_regclass('graph.nodes_record_change')"), write=True)


async def test_the_seek_reads_the_index(w: World, pg_db: KernelDB) -> None:
    """The plan of the vetted function's query is an index scan of nodes_record_change, not a read of every record of the org."""
    await w.nodes("records", *[_rec(w, f"p{i:04d}", updated=i + 1) for i in range(3000)])
    await pg_db.run(lambda tx: tx.execute("ANALYZE graph.nodes"), write=True)
    plan = await pg_db.run(lambda tx: tx.fetch("""EXPLAIN SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
        AND n.doc->>'connectorId' = $2 AND jsonb_typeof(n.doc->'updatedAtTimestamp') = 'number'
        AND (n.doc->'updatedAtTimestamp', n.id COLLATE ucs_basic) > (to_jsonb(2900::bigint), '' COLLATE ucs_basic)
        ORDER BY n.doc->>'connectorId', n.doc->'updatedAtTimestamp', n.id COLLATE ucs_basic LIMIT 500""", w.org, CONNECTOR))
    assert "nodes_record_change" in "\n".join(r[0] for r in plan)


async def test_the_epoch_is_a_compare_and_set_that_one_of_six_claimants_wins(w: World, org_ctx: Callable[[str], AbstractContextManager]) -> None:
    await w.nodes("apps", {"_key": "kb-claim", "name": "KB", "type": "KB", "appGroup": "Collections", "scope": "team", "isActive": True, "createdAtTimestamp": 1})
    store, scope = GraphStateStore(w.p), Scope("kb-claim", w.org, Connectors.KNOWLEDGE_BASE)
    with org_ctx(w.org):
        results = await asyncio.gather(*(store.claim(scope, None) for _ in range(6)))
        assert sorted(r for r in results if r is not None) == [1], "exactly one of six claimants of epoch 1 wins"
        state = SweepState(watermark=5, covered_hash=HASH, covered_gen=2, lap="seek", cursor_ts=9, cursor_id="x")
        assert await store.save(scope, 1, state)
        assert not await store.save(scope, 7, SweepState(watermark=99))
        loaded, epoch = await store.load(scope)
    assert loaded == state and epoch == 1
