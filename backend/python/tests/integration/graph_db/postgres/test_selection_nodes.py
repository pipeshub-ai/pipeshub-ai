"""``get_selection_nodes`` on PostgreSQL: what a selection of record groups, folders and files covers, in one org and across two.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval import selection_scope as scope
from app.modules.retrieval.selection_scope import (
    outside_connection,
    resolve_selection_scope,
)

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("sel"), pg_db), org or new_org())


def rec(org: str, rid: str, **kw: object) -> dict:
    return {"_key": rid, "orgId": org, "recordName": rid, "connectorId": "c1", "virtualRecordId": f"v-{rid}", **kw}


def link(f: str, t: str, rel: str = "PARENT_CHILD", coll: str = "records") -> dict:
    return {"from_id": f, "from_collection": coll, "to_id": t, "to_collection": coll, "relationshipType": rel}


async def world(p: OrgBound) -> None:
    """folder -> sub -> file, folder -> note (ATTACHMENT), sub -> gone (deleted) -> under_gone, and a bookmark folder ~ other that is no hierarchy.
    Groups: top -> mid -> leaf, mid -> trashed (deleted)."""
    await p.batch_upsert_nodes([rec(p.org, i) for i in ("folder", "sub", "file", "note", "other", "under_gone")] + [rec(p.org, "gone", isDeleted=True)], "records")
    await p.batch_create_edges([link("folder", "sub"), link("sub", "file"), link("folder", "note", "ATTACHMENT"), link("sub", "gone"),
                                link("gone", "under_gone"), link("folder", "other", "BOOKMARK")], "nodeRelations")
    groups = [{"_key": i, "orgId": p.org, "connectorId": "c1"} for i in ("top", "mid", "leaf")] + [{"_key": "trashed", "orgId": p.org, "connectorId": "c1", "isDeleted": True}]
    await p.batch_upsert_nodes(groups, "recordGroups")
    await p.batch_create_edges([link("top", "mid", coll="recordGroups"), link("mid", "leaf", coll="recordGroups"), link("mid", "trashed", coll="recordGroups")], "nodeRelations")


async def ids(p: OrgBound, **kw: object) -> tuple[set[str], set[str]]:
    got = await p.get_selection_nodes(p.org, **{"group_ids": [], "record_ids": [], "exact_record_ids": [], "limit": 100, **kw})
    return {g["id"] for g in got["groups"]}, {r["id"] for r in got["records"]}


async def test_a_folder_covers_what_is_under_it_and_nothing_else(pg_db) -> None:
    p = bound(pg_db)
    await world(p)
    assert (await ids(p, record_ids=["folder"]))[1] == {"folder", "sub", "file", "note"}     # not the bookmark, not the deleted node or what hangs under it
    assert (await ids(p, record_ids=["sub"]))[1] == {"sub", "file"}


async def test_an_exact_file_covers_only_itself_and_a_deleted_one_nothing(pg_db) -> None:
    p = bound(pg_db)
    await world(p)
    assert (await ids(p, exact_record_ids=["sub"]))[1] == {"sub"}
    assert (await ids(p, exact_record_ids=["gone", "nope"]))[1] == set()
    got = await p.get_selection_nodes(p.org, group_ids=[], record_ids=[], exact_record_ids=["file"], limit=5)
    assert got["records"] == [{"id": "file", "vrid": "v-file", "connectorId": "c1"}]


async def test_a_record_group_covers_the_live_groups_nested_in_it(pg_db) -> None:
    p = bound(pg_db)
    await world(p)
    assert (await ids(p, group_ids=["top"]))[0] == {"top", "mid", "leaf"}
    assert (await ids(p, group_ids=["leaf"]))[0] == {"leaf"}
    assert (await ids(p, group_ids=["trashed"]))[0] == set()


async def test_nested_roots_give_each_node_once_and_a_big_selection_is_cut_one_past_the_limit(pg_db) -> None:
    p = bound(pg_db)
    await world(p)
    assert len((await ids(p, record_ids=["folder", "sub", "file"], exact_record_ids=["file"]))[1]) == 4
    assert len((await ids(p, record_ids=["folder"], limit=2))[1]) == 3                 # limit + 1: the caller reads it as too large


async def test_another_org_reads_nothing(pg_db) -> None:
    p, stranger = bound(pg_db), bound(pg_db)
    await world(p)
    assert await ids(stranger, record_ids=["folder"], group_ids=["top"]) == (set(), set())
    got = await stranger.get_selection_nodes(p.org, group_ids=["top"], record_ids=["folder"], exact_record_ids=[], limit=10)
    assert got == {"groups": [], "records": []}


async def test_a_limit_naming_a_folder_resolves_instead_of_raising_not_implemented(pg_db) -> None:
    p = bound(pg_db)
    await world(p)
    scope._memo.clear()
    resolved = await resolve_selection_scope(p, "u", p.org, {"records": ["folder"]}, readable_only=False)
    assert resolved is not None and set(resolved.records) == {"folder", "sub", "file", "note"}


async def test_a_failed_read_while_checking_a_limit_raises_and_the_same_call_answers_once_the_store_is_back(pg_db, monkeypatch) -> None:
    """The user lookup is soft on PostgreSQL unless asked to raise: a failure must not read as 'every id is outside the limit'."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "k1", "userId": "uid-1", "orgId": p.org}], "users")
    scope._memo.clear()
    inner = p._p
    real = inner._one

    async def down(*a: object, **k: object) -> None:
        raise ConnectionError("store down")

    monkeypatch.setattr(inner, "_one", down)
    with pytest.raises(PermissionVerificationUnavailableError):
        await outside_connection(p, "uid-1", p.org, ["x"], {"apps": ["c1"]})
    monkeypatch.setattr(inner, "_one", real)
    assert await outside_connection(p, "uid-1", p.org, ["x"], {"apps": ["c1"]}) == {"x"}   # a node the user cannot reach is outside, as it was


async def test_a_deep_chain_is_walked_fifty_hops_and_no_further(pg_db) -> None:
    p = bound(pg_db)
    names = [f"n{i}" for i in range(60)]
    await p.batch_upsert_nodes([rec(p.org, n) for n in names], "records")
    await p.batch_create_edges([link(a, b) for a, b in zip(names, names[1:])], "nodeRelations")
    got = (await ids(p, record_ids=["n0"], limit=1000))[1]
    assert got == set(names[:51])                                                  # the root and 50 hops, as the other stores walk
    assert (await ids(p, record_ids=["n10"], limit=1000))[1] == set(names[10:61])


async def test_a_record_and_a_group_with_the_same_id_stay_apart(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "same"), rec(p.org, "rkid")], "records")
    await p.batch_upsert_nodes([{"_key": "same", "orgId": p.org, "connectorId": "c1"}, {"_key": "gkid", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
    await p.batch_create_edges([link("same", "rkid")], "nodeRelations")
    await p.batch_create_edges([link("same", "gkid", coll="recordGroups")], "nodeRelations")
    assert await ids(p, record_ids=["same"]) == (set(), {"same", "rkid"})
    assert await ids(p, group_ids=["same"]) == ({"same", "gkid"}, set())


async def test_a_wide_level_is_cut_only_past_the_limit_and_deleted_or_repeated_children_do_not_count(pg_db) -> None:
    p = bound(pg_db)
    kids = [f"k{i:02d}" for i in range(10)]
    dead = [f"d{i:02d}" for i in range(10)]
    await p.batch_upsert_nodes([rec(p.org, "wide")] + [rec(p.org, k) for k in kids] + [rec(p.org, d, isDeleted=True) for d in dead], "records")
    await p.batch_create_edges([link("wide", k) for k in (*dead, *kids)] + [link("k00", "wide")], "nodeRelations")     # k00 -> wide closes a cycle
    assert len((await ids(p, record_ids=["wide"], limit=11))[1]) == 11                 # exactly at the limit: every live node, none cut
    assert len((await ids(p, record_ids=["wide"], limit=10))[1]) == 11                 # one past it: the caller reads it as too large
    assert (await ids(p, record_ids=["wide"], limit=50))[1] == {"wide", *kids}
