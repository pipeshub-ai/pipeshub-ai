"""Named-entity writes and the orphan sweep on PostgreSQL, against a real database, as ``Neo4jProvider`` (graph_ops) answers them.

Each write stays in the org of the call: the same ids in another org are neither read nor changed. Every guard has a case that fails without it.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.core.db.errors import (
    DbTimeout,
    DeadlockDetected,
    LockTimeout,
    PoolTimeout,
    SerializationConflict,
)
from app.core.org_context import OrgContext, OrgMismatch, bind
from app.modules.named_entities.keys import value_mention_key
from app.schema.node_validator import SchemaValidationError

from .test_grants import failing, in_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = pytest.mark.integration


class Org:
    """A provider bound to one org, with the graph helpers of these tests."""

    def __init__(self, p: PostgresGraphProvider, org: str) -> None:
        self.p, self.org = p, org

    def ne(self, key: str, **over: object) -> dict:
        return {"id": key, "orgId": self.org, "kind": "org", "name": key, "normKey": key.lower(), "aliases": [key], "normalizedAliases": [key.lower()],
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1, **over}

    def vm(self, record: str, norm: str, **over: object) -> dict:
        return {"id": value_mention_key(record, "date", norm), "orgId": self.org, "recordId": record, "kind": "date", "normKey": norm, "name": norm,
                "startMs": 1, "endMs": 2, **over}

    async def record(self, key: str, **doc: object) -> None:
        await self.p.batch_upsert_nodes([{"_key": key, "orgId": self.org, "recordName": key, "connectorId": "c1", **doc}], "records")

    async def mention(self, record: str, entity: str, **doc: object) -> None:
        await self.p.batch_create_edges([{"from_collection": "records", "from_id": record, "to_collection": "namedEntities", "to_id": entity,
                                          "orgId": self.org, "mentionCount": 1, **doc}], "mentionsEntity")

    async def node(self, coll: str, key: str) -> dict | None:
        return await self.p.get_document(key, coll)

    async def values(self, record: str) -> set[str]:
        rows = await self.p._q(lambda tx: tx.fetch("SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'valueMentions' AND doc->>'recordId' = $2", self.org, record), None)
        return {r["id"] for r in rows}

    async def edges(self, record: str) -> set[str]:
        rows = await self.p._q(lambda tx: tx.fetch("SELECT to_id FROM graph.edges WHERE org_id = $1 AND coll = 'mentionsEntity' AND from_id = $2", self.org, record), None)
        return {r["to_id"] for r in rows}


@pytest.fixture
async def orgs(pg_provider, pg_db) -> AsyncIterator[tuple[Org, Org]]:
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    with bind(OrgContext(a, None, "job", "job:test", "t")):
        yield Org(pg_provider, a), Org(pg_provider, b)
    for org in (a, b):
        with in_org(org):
            await pg_db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await pg_db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


@pytest.fixture
def w(orgs: tuple[Org, Org]) -> Org:
    return orgs[0]


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")


# ---- create ----------------------------------------------------------------------------------------------------------------------------

async def test_create_writes_absent_nodes_and_leaves_existing_ones_as_they_are(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A", name="first")])
    await w.p.create_named_entities_if_absent([w.ne("A", name="second"), w.ne("B", name="b", orphanedAt=None, tags=None)])
    assert (await w.node("namedEntities", "A"))["name"] == "first"
    b = await w.node("namedEntities", "B")
    assert b["name"] == "b" and "orphanedAt" not in b and "tags" not in b          # a null is not stored
    await w.p.create_named_entities_if_absent([])
    await w.p.create_named_entities_if_absent([w.ne("D", name="one"), w.ne("D", name="two")])
    assert (await w.node("namedEntities", "D"))["name"] == "one"                   # a repeated id in one batch: the first


async def test_create_does_not_write_a_node_that_is_there_and_unmarked(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    version = "SELECT xmin::text AS v FROM graph.nodes WHERE org_id = $1 AND coll = 'namedEntities' AND id = 'A'"
    before = (await w.p._q(lambda tx: tx.fetchrow(version, w.org), None))["v"]
    await w.p.create_named_entities_if_absent([w.ne("A", name="again")])
    assert (await w.p._q(lambda tx: tx.fetchrow(version, w.org), None))["v"] == before          # a popular node is never locked by the records that mention it


async def test_create_takes_an_orphan_mark_off_only_where_there_is_one(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B")])
    await w.p.update_node("A", "namedEntities", {"orphanedAt": 5})
    await w.p.update_node("B", "namedEntities", {"name": "kept"})
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B")])
    assert "orphanedAt" not in await w.node("namedEntities", "A") and (await w.node("namedEntities", "B"))["name"] == "kept"
    await w.p.create_named_entities_if_absent([w.ne("N", orphanedAt=7)])
    assert "orphanedAt" not in await w.node("namedEntities", "N")


async def test_a_bad_member_writes_none_of_the_batch_and_another_orgs_document_is_refused(w: Org, orgs: tuple[Org, Org], strict: None) -> None:
    with pytest.raises(SchemaValidationError):
        await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B", kind=3)])
    assert await w.node("namedEntities", "A") is None
    with pytest.raises(OrgMismatch):
        await w.p.create_named_entities_if_absent([w.ne("A"), orgs[1].ne("X")])
    assert await w.node("namedEntities", "A") is None


# ---- aliases ---------------------------------------------------------------------------------------------------------------------------

async def test_aliases_are_a_union_keyed_by_the_normalized_form_and_capped(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A", name="Acme")])
    await w.p.add_named_entity_aliases(w.org, "org", "A", ["Acme Inc", "ACME", "Acme Inc."], ["acme inc", "acme", "acme inc"])
    n = await w.node("namedEntities", "A")
    assert n["aliases"] == ["A", "Acme Inc", "ACME"] and n["normalizedAliases"] == ["a", "acme inc", "acme"] and n["updatedAtTimestamp"] > 1
    stamp = n["updatedAtTimestamp"]
    await w.p.add_named_entity_aliases(w.org, "org", "A", ["Acme Inc"], ["acme inc"])
    assert (await w.node("namedEntities", "A"))["updatedAtTimestamp"] == stamp           # nothing new: not written
    await w.p.add_named_entity_aliases(w.org, "org", "A", [f"x{i}" for i in range(30)], [f"x{i}" for i in range(30)])
    n = await w.node("namedEntities", "A")
    assert len(n["aliases"]) == len(n["normalizedAliases"]) == 20 and n["normalizedAliases"][3] == "x0"
    await w.p.add_named_entity_aliases(w.org, "org", "A", [], [])


async def test_aliases_go_only_to_a_node_of_this_org_and_kind(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    await w.p.add_named_entity_aliases(w.org, "person", "A", ["Zed"], ["zed"])           # another kind
    await w.p.add_named_entity_aliases(orgs[1].org, "org", "A", ["Zed"], ["zed"])        # another org's name for it
    await w.p.add_named_entity_aliases(w.org, "org", "missing", ["Zed"], ["zed"])
    assert (await w.node("namedEntities", "A"))["normalizedAliases"] == ["a"]
    with in_org(orgs[1].org):
        await orgs[1].p.create_named_entities_if_absent([orgs[1].ne("A")])
        await orgs[1].p.add_named_entity_aliases(w.org, "org", "A", ["Zed"], ["zed"])    # this org's id, from the other org
        assert (await orgs[1].node("namedEntities", "A"))["normalizedAliases"] == ["a"]


async def test_concurrent_alias_writes_keep_every_alias(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    await asyncio.gather(*(w.p.add_named_entity_aliases(w.org, "org", "A", [f"v{i}"], [f"v{i}"]) for i in range(8)))
    assert len((await w.node("namedEntities", "A"))["normalizedAliases"]) == 9


async def test_an_invalid_alias_batch_writes_nothing(w: Org, strict: None) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    with pytest.raises(SchemaValidationError):
        await w.p.add_named_entity_aliases(w.org, "org", "A", [12], ["x"])
    assert (await w.node("namedEntities", "A"))["normalizedAliases"] == ["a"]


# ---- redirects -------------------------------------------------------------------------------------------------------------------------

async def test_redirects_follow_merges_to_the_last_node_and_stop_at_a_cycle(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A", mergedInto="B"), w.ne("B", mergedInto="C"), w.ne("C"), w.ne("X", mergedInto="Y"), w.ne("Y", mergedInto="X")])
    assert await w.p.resolve_named_entity_redirects(w.org, ["A", "B", "C", "Z"]) == {"A": "C", "B": "C", "C": "C", "Z": "Z"}
    assert await w.p.resolve_named_entity_redirects(w.org, ["X"]) == {"X": "Y"}
    assert await w.p.resolve_named_entity_redirects(w.org, []) == {}
    assert await w.p.resolve_named_entity_redirects(orgs[1].org, ["A"]) == {"A": "A"}     # another org's names: nothing to follow
    with in_org(orgs[1].org):
        await orgs[1].p.create_named_entities_if_absent([orgs[1].ne("A", mergedInto="Q")])
        assert await orgs[1].p.resolve_named_entity_redirects(orgs[1].org, ["A"]) == {"A": "Q"}
    await w.p.create_named_entities_if_absent([w.ne(f"h{i}", mergedInto=f"h{i + 1}") for i in range(20)])
    assert await w.p.resolve_named_entity_redirects(w.org, ["h0"]) == {"h0": "h16"}        # the hop cap


async def test_a_failed_redirect_read_raises_and_answers_once_the_store_is_back(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A", mergedInto="B"), w.ne("B")])
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError, match="store down"):
        await w.p.resolve_named_entity_redirects(w.org, ["A"])
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.p.resolve_named_entity_redirects(w.org, ["A"]) == {"A": "B"}


# ---- value mentions --------------------------------------------------------------------------------------------------------------------

async def test_values_replace_the_records_rows_whole_and_touch_no_other_record(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "a"), w.vm("r1", "b")])
    await w.p.replace_named_entity_values("r2", [w.vm("r2", "a")])
    with in_org(orgs[1].org):
        await orgs[1].p.replace_named_entity_values("r1", [orgs[1].vm("r1", "z")])
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "b", name="changed"), w.vm("r1", "c"), w.vm("r1", "c", name="last")])
    assert await w.values("r1") == {value_mention_key("r1", "date", k) for k in "bc"} and len(await w.values("r2")) == 1
    assert (await w.node("valueMentions", value_mention_key("r1", "date", "b")))["name"] == "changed"
    assert (await w.node("valueMentions", value_mention_key("r1", "date", "c")))["name"] == "last"
    with in_org(orgs[1].org):
        assert len(await orgs[1].values("r1")) == 1
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "n", amountHighFloat=None, unit=None)])
    assert "amountHighFloat" not in await w.node("valueMentions", value_mention_key("r1", "date", "n"))         # a null is not stored
    await w.p.replace_named_entity_values("r1", [])
    assert await w.values("r1") == set() and len(await w.values("r2")) == 1


async def test_a_bad_value_row_changes_nothing_and_another_orgs_row_is_refused(w: Org, orgs: tuple[Org, Org], strict: None) -> None:
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "a")])
    with pytest.raises(SchemaValidationError):
        await w.p.replace_named_entity_values("r1", [w.vm("r1", "b"), w.vm("r1", "c", startMs="soon")])
    with pytest.raises(OrgMismatch):
        await w.p.replace_named_entity_values("r1", [orgs[1].vm("r1", "b")])
    await w.p.replace_named_entity_values("r2", [w.vm("r2", "a")])
    with pytest.raises(ValueError, match="other than the one"):
        await w.p.replace_named_entity_values("r1", [w.vm("r1", "b"), w.vm("r2", "a", name="taken over")])     # a row of r2 under r1
    assert (await w.node("valueMentions", value_mention_key("r2", "date", "a")))["name"] == "a"
    assert await w.values("r1") == {value_mention_key("r1", "date", "a")}


async def test_values_replace_inside_a_callers_transaction_and_roll_back_with_it(w: Org) -> None:
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "a")])
    tid = await w.p.begin_transaction([], [])
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "b")], tid)
    await w.p.rollback_transaction(tid)
    assert await w.values("r1") == {value_mention_key("r1", "date", "a")}


async def test_dangling_values_are_the_rows_of_records_that_are_gone(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.record("live")
    await w.record("trashed", isDeleted=True)
    for r in ("live", "trashed", "gone1", "gone2", "gone3"):
        await w.p.replace_named_entity_values(r, [w.vm(r, "a")])
    with in_org(orgs[1].org):
        await orgs[1].p.replace_named_entity_values("gone1", [orgs[1].vm("gone1", "a")])
        assert await orgs[1].p.delete_dangling_named_entity_values(orgs[1].org, 10) == 1
        assert await orgs[1].p.delete_dangling_named_entity_values(orgs[1].org, 10) == 0
    assert await w.p.delete_dangling_named_entity_values(orgs[1].org, 10) == 0           # not this org's rows
    assert await w.p.delete_dangling_named_entity_values(w.org, 2) == 2
    assert await w.p.delete_dangling_named_entity_values(w.org, 0) == 1                  # a batch is at least one
    assert await w.p.delete_dangling_named_entity_values(w.org, 10) == 0
    assert len(await w.values("live")) == len(await w.values("trashed")) == 1             # a trashed record still exists


# ---- copy ------------------------------------------------------------------------------------------------------------------------------

async def test_copy_gives_the_target_the_edges_and_values_of_the_source(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B")])
    await w.record("src")
    await w.record("dst")
    await w.mention("src", "A", extractedNames=["a"])
    await w.mention("src", "B", extractedNames=["b"])
    await w.mention("dst", "B", extractedNames=["old"], extra=1)
    await w.p.replace_named_entity_values("src", [w.vm("src", "a"), w.vm("src", "b")])
    await w.p.replace_named_entity_values("dst", [w.vm("dst", "stale")])
    assert await w.p.copy_named_entity_mentions("src", "dst") == 4
    assert await w.edges("dst") == {"A", "B"} and await w.edges("src") == {"A", "B"}
    rows = await w.p._q(lambda tx: tx.fetch("SELECT to_id, doc FROM graph.edges WHERE org_id = $1 AND coll = 'mentionsEntity' AND from_id = 'dst'", w.org), None)
    docs = {r["to_id"]: r["doc"] for r in rows}
    assert '"b"' in str(docs["B"]) and '"extra"' in str(docs["B"]) and '"a"' in str(docs["A"])      # the copy overwrites what it carries, keeps the rest
    assert await w.values("dst") == {value_mention_key("dst", "date", k) for k in ("a", "b")}      # the target's own stale row goes
    assert (await w.node("valueMentions", value_mention_key("dst", "date", "a")))["recordId"] == "dst"
    assert await w.p.copy_named_entity_mentions("src", "dst") == 4                                   # again: the same


async def test_copy_skips_what_it_must(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    for r, doc in (("src", {}), ("gone_target", {}), ("deleted", {"isDeleted": True}), ("foreign", {"orgId": orgs[1].org}), ("empty", {})):
        await w.record(r, **doc)
    await w.mention("src", "A")
    await w.mention("src", "missing_node")
    await w.p.replace_named_entity_values("src", [w.vm("src", "a")])
    assert await w.p.copy_named_entity_mentions("src", "nobody") == 0                                # no target record: no edge, no values
    assert await w.edges("nobody") == set() and await w.values("nobody") == set()
    assert await w.p.copy_named_entity_mentions("src", "deleted") == 1                               # the edge goes to a trashed target, its values do not
    assert await w.values("deleted") == set()
    assert await w.p.copy_named_entity_mentions("src", "foreign") == 0                               # a record of another org than the edge's
    await w.p.replace_named_entity_values("empty", [w.vm("empty", "keep")])
    assert await w.p.copy_named_entity_mentions("nothing", "empty") == 0 and len(await w.values("empty")) == 1     # a source with nothing leaves the target's values
    assert await w.p.copy_named_entity_mentions("src", "src") == 2 and await w.edges("src") == {"A", "missing_node"}


async def test_copy_never_crosses_orgs(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    await w.record("src")
    await w.mention("src", "A")
    with in_org(orgs[1].org):
        await orgs[1].record("dst")
        assert await orgs[1].p.copy_named_entity_mentions("src", "dst") == 0
        assert await orgs[1].edges("dst") == set()


class FailOn:
    """A transaction that runs the statements of the copy and fails on the one that replaces the target's values, after the edges were inserted."""

    def __init__(self, tx: object) -> None:
        self._tx = tx

    def __getattr__(self, name: str) -> object:
        return getattr(self._tx, name)

    async def execute(self, sql: str, *a: object) -> object:
        if "replace_named_entity_values_stale" in sql:
            raise RuntimeError("store down")
        return await self._tx.execute(sql, *a)


async def test_a_failed_copy_changes_nothing_and_a_retry_completes(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    await w.record("src")
    await w.record("dst")
    await w.mention("src", "A")
    await w.p.replace_named_entity_values("src", [w.vm("src", "a")])
    real = w.p._q

    async def failing_late(fn: object, transaction: object = None, **kw: object) -> object:
        return await real(lambda tx: fn(FailOn(tx)), transaction, **kw)
    monkeypatch.setattr(w.p, "_q", failing_late)
    with pytest.raises(RuntimeError):
        await w.p.copy_named_entity_mentions("src", "dst")
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.edges("dst") == set() and await w.values("dst") == set()          # the edge inserted before the failure went with it
    assert await w.p.copy_named_entity_mentions("src", "dst") == 2


# ---- the orphan sweep ------------------------------------------------------------------------------------------------------------------

async def sweep_world(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("linked"), w.ne("lone"), w.ne("lone2", kind="person"), w.ne("merged", mergedInto="linked"), w.ne("old"), w.ne("fresh")])
    await w.p.update_node("old", "namedEntities", {"orphanedAt": 100})        # a create takes a mark off, so the marks are set after
    await w.p.update_node("fresh", "namedEntities", {"orphanedAt": 900})
    await w.record("r1")
    await w.mention("r1", "linked")


async def test_find_lists_the_orphans_a_delete_would_remove(w: Org, orgs: tuple[Org, Org]) -> None:
    await sweep_world(w)
    assert await w.p.find_orphan_named_entities(w.org) == ["fresh", "lone", "lone2", "old"]       # not linked, not merged
    assert await w.p.find_orphan_named_entities(w.org, 2) == ["fresh", "lone"]
    assert await w.p.find_orphan_named_entities(w.org, 0) == ["fresh"]
    assert await w.p.find_orphan_named_entities(w.org, marked_before_ms=500) == ["old"]           # marked at or before the cutoff
    assert await w.p.find_orphan_named_entities(w.org, marked_before_ms=100) == ["old"]
    assert await w.p.find_orphan_named_entities(w.org, marked_before_ms=50) == []
    assert await w.p.find_orphan_named_entities(w.org, skip_kinds=["person"]) == ["fresh", "lone", "old"]
    assert await w.p.find_orphan_named_entities(w.org, skip_kinds=[]) == ["fresh", "lone", "lone2", "old"]
    assert await w.p.find_orphan_named_entities(orgs[1].org) == []


async def test_delete_removes_only_orphans_and_only_those_asked_for(w: Org, orgs: tuple[Org, Org]) -> None:
    await sweep_world(w)
    assert await w.p.delete_orphan_named_entities(w.org, 10, entity_ids=[]) == []                  # an empty list is no ids, not all
    assert await w.p.delete_orphan_named_entities(w.org, 10, entity_ids=["linked", "merged", "lone2", "nobody"]) == ["lone2"]
    assert await w.node("namedEntities", "linked") and await w.node("namedEntities", "merged") and await w.node("namedEntities", "lone2") is None
    assert await w.p.delete_orphan_named_entities(orgs[1].org, 10) == []
    assert await w.p.delete_orphan_named_entities(w.org, 10, marked_before_ms=500) == ["old"]
    assert await w.p.delete_orphan_named_entities(w.org, 0) == ["fresh"]                           # a batch is at least one; no cutoff: any orphan
    assert await w.p.delete_orphan_named_entities(w.org, 10) == ["lone"]
    assert await w.node("namedEntities", "linked") and await w.node("namedEntities", "merged")


async def test_delete_leaves_a_node_a_writer_holds_and_deletes_it_once_the_writer_is_done(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B")])
    for k in ("A", "B"):
        await w.p.update_node(k, "namedEntities", {"orphanedAt": 100})
    tid = await w.p.begin_transaction([], [])
    try:
        await w.p.update_node("A", "namedEntities", {"updatedAtTimestamp": 2}, transaction=tid)          # the writer holds A's row
        gone = await asyncio.wait_for(w.p.delete_orphan_named_entities(w.org, 10, marked_before_ms=500, entity_ids=["A", "B"]), 10)
        assert gone == ["B"] and await w.node("namedEntities", "A")                                      # not waited for, not deleted
        await w.p.update_node("A", "namedEntities", {"orphanedAt": None}, transaction=tid)               # and it takes the mark off
    except BaseException:
        await w.p.rollback_transaction(tid)       # a lock left open would hang the cleanup
        raise
    await w.p.commit_transaction(tid)
    assert await w.p.delete_orphan_named_entities(w.org, 10, marked_before_ms=500, entity_ids=["A"]) == []     # no mark: kept
    assert await w.node("namedEntities", "A")


async def test_a_create_that_clears_the_mark_between_the_find_and_the_delete_keeps_the_node(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B")])
    for k in ("A", "B"):
        await w.p.update_node(k, "namedEntities", {"orphanedAt": 100})
    found = await w.p.find_orphan_named_entities(w.org, 10, marked_before_ms=500)
    assert found == ["A", "B"]
    await w.p.create_named_entities_if_absent([w.ne("A")])                                      # a writer about to link A takes its mark back
    assert await w.p.delete_orphan_named_entities(w.org, 10, marked_before_ms=500, entity_ids=found) == ["B"]
    assert await w.node("namedEntities", "A") and "orphanedAt" not in await w.node("namedEntities", "A")


async def hold(w: Org, key: str) -> str:
    """Another connection holds the node's row."""
    tid = await w.p.begin_transaction([], [])
    await w.p.update_node(key, "namedEntities", {"updatedAtTimestamp": 2}, transaction=tid)
    return tid


async def test_mark_and_clear_skip_a_row_another_connection_holds_and_take_it_the_next_pass(w: Org) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A"), w.ne("B"), w.ne("L")])
    await w.record("r1")
    await w.mention("r1", "L")
    tid = await hold(w, "A")
    try:
        assert await asyncio.wait_for(w.p.mark_orphan_named_entities(w.org, 5), 10) == 1            # B; A is held, not waited for
    except BaseException:
        await w.p.rollback_transaction(tid)
        raise
    await w.p.commit_transaction(tid)
    assert "orphanedAt" not in await w.node("namedEntities", "A") and await w.p.mark_orphan_named_entities(w.org, 6) == 1
    assert (await w.node("namedEntities", "A"))["orphanedAt"] == 6
    await w.p.update_node("A", "namedEntities", {"orphanedAt": 9})
    await w.mention("r1", "A")
    await w.p.update_node("L", "namedEntities", {"orphanedAt": 9})
    tid = await hold(w, "A")
    try:
        assert await asyncio.wait_for(w.p.clear_orphan_named_entity_marks(w.org), 10) == 1           # L; A is held
    except BaseException:
        await w.p.rollback_transaction(tid)
        raise
    await w.p.commit_transaction(tid)
    assert await w.p.clear_orphan_named_entity_marks(w.org) == 1 and "orphanedAt" not in await w.node("namedEntities", "A")


async def test_mark_marks_the_unmarked_orphans_and_clear_takes_off_the_marks_of_linked_nodes(w: Org, orgs: tuple[Org, Org]) -> None:
    await sweep_world(w)
    assert await w.p.mark_orphan_named_entities(orgs[1].org, 5) == 0
    assert await w.p.mark_orphan_named_entities(w.org, 5, 1) == 1 and (await w.node("namedEntities", "lone"))["orphanedAt"] == 5      # in id order
    assert await w.p.mark_orphan_named_entities(w.org, 6) == 1                                                                        # lone2; old and fresh already marked
    assert (await w.node("namedEntities", "lone2"))["orphanedAt"] == 6 and (await w.node("namedEntities", "old"))["orphanedAt"] == 100
    assert "orphanedAt" not in (await w.node("namedEntities", "merged")) and "orphanedAt" not in (await w.node("namedEntities", "linked"))
    assert await w.p.mark_orphan_named_entities(w.org, 7) == 0
    await w.p.update_node("linked", "namedEntities", {"orphanedAt": 9})
    await w.record("r2")
    await w.mention("r2", "old")
    assert await w.p.clear_orphan_named_entity_marks(orgs[1].org) == 0
    assert await w.p.clear_orphan_named_entity_marks(w.org, 1) == 1                                # id order: linked
    assert await w.p.clear_orphan_named_entity_marks(w.org) == 1                                   # old
    assert await w.p.clear_orphan_named_entity_marks(w.org) == 0
    assert "orphanedAt" not in await w.node("namedEntities", "old") and (await w.node("namedEntities", "fresh"))["orphanedAt"] == 900


async def test_an_invalid_mark_time_changes_nothing(w: Org, strict: None) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A")])
    with pytest.raises(SchemaValidationError):
        await w.p.mark_orphan_named_entities(w.org, "soon")           # type: ignore[arg-type]
    assert "orphanedAt" not in await w.node("namedEntities", "A")


async def test_dangling_mentions_are_the_edges_to_nodes_that_are_gone(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("kept")])
    await w.record("r1")
    for e in ("kept", "gone1", "gone2", "untouched"):
        await w.mention("r1", e)
    assert await w.p.delete_dangling_named_entity_mentions([]) == 0
    assert await w.p.delete_dangling_named_entity_mentions(["kept", "gone1", "gone1", "nobody"]) == 1
    assert await w.edges("r1") == {"kept", "gone2", "untouched"}                                    # only the ones named; a present node keeps its edge
    with in_org(orgs[1].org):
        assert await orgs[1].p.delete_dangling_named_entity_mentions(["gone2"]) == 0
    assert await w.p.delete_dangling_named_entity_mentions(["gone2"]) == 1


async def test_a_failed_sweep_step_leaves_everything_and_works_again(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await sweep_world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    for call in (w.p.mark_orphan_named_entities(w.org, 1), w.p.clear_orphan_named_entity_marks(w.org), w.p.find_orphan_named_entities(w.org),
                 w.p.delete_orphan_named_entities(w.org, 10), w.p.delete_dangling_named_entity_values(w.org)):
        with pytest.raises(RuntimeError, match="store down"):
            await call
    assert await w.p.delete_dangling_named_entity_mentions([]) == 0               # nothing to look for: the store is not asked
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.node("namedEntities", "lone") and await w.p.mark_orphan_named_entities(w.org, 1) == 2
    assert await w.p.delete_orphan_named_entities(w.org, 10, marked_before_ms=1000) == ["fresh", "lone", "lone2", "old"]


async def test_the_sweep_retries_a_conflict_and_not_other_errors(w: Org) -> None:
    for retry in (SerializationConflict(), DeadlockDetected(), LockTimeout()):
        assert w.p.is_named_entity_write_retryable(retry)
    for other in (DbTimeout(), PoolTimeout(), RuntimeError("x")):
        assert not w.p.is_named_entity_write_retryable(other)


# ---- retries and jobs ------------------------------------------------------------------------------------------------------------------

async def test_a_retry_doubles_its_wait_with_each_failure_and_is_capped(w: Org) -> None:
    assert await w.p.schedule_named_entity_persist_retry(w.org, "r1", "boom") == 1
    first = (await w.node("namedEntityPersistRetries", "r1"))
    assert first["attempts"] == 1 and first["lastError"] == "boom" and first["orgId"] == w.org and first["recordId"] == "r1"
    assert 59_000 <= first["dueAt"] - first["createdAtTimestamp"] <= 61_000
    assert await w.p.schedule_named_entity_persist_retry(w.org, "r1", "x" * 500) == 2
    second = await w.node("namedEntityPersistRetries", "r1")
    assert second["createdAtTimestamp"] == first["createdAtTimestamp"] and len(second["lastError"]) == 200 and 119_000 <= second["dueAt"] - second["updatedAtTimestamp"] <= 121_000
    assert await w.p.schedule_named_entity_persist_retry(w.org, "r1", "e", attempts_so_far=9) == 10       # a count carried from a claimed retry
    assert 6 * 3600 * 1000 - 1000 <= (await w.node("namedEntityPersistRetries", "r1"))["dueAt"] - (await w.node("namedEntityPersistRetries", "r1"))["updatedAtTimestamp"] <= 6 * 3600 * 1000
    assert await w.p.schedule_named_entity_persist_retry(w.org, "r2", "e", attempts_so_far=5000) == 5001   # no overflow
    assert await w.p.schedule_named_entity_persist_retry(w.org, "r3", "e", attempts_so_far=2) == 3
    r3 = await w.node("namedEntityPersistRetries", "r3")
    assert 239_000 <= r3["dueAt"] - r3["updatedAtTimestamp"] <= 241_000


async def test_two_first_failures_of_a_record_both_count(w: Org) -> None:
    got = await asyncio.gather(w.p.schedule_named_entity_persist_retry(w.org, "r1", "a"), w.p.schedule_named_entity_persist_retry(w.org, "r1", "b"))
    assert sorted(got) == [1, 2] and (await w.node("namedEntityPersistRetries", "r1"))["attempts"] == 2


async def test_a_marker_for_another_org_is_refused_and_nothing_is_written(w: Org, orgs: tuple[Org, Org]) -> None:
    for call in (w.p.schedule_named_entity_persist_retry(orgs[1].org, "r1", "e"), w.p.schedule_named_entity_reextract("", "r1"), w.p.schedule_named_entity_reproject(orgs[1].org, "r1")):
        with pytest.raises(OrgMismatch):
            await call
    assert await w.node("namedEntityPersistRetries", "r1") is None


async def test_invalid_markers_are_refused_before_any_write(w: Org, strict: None) -> None:
    for call in (w.p.schedule_named_entity_persist_retry(w.org, 5, "e"), w.p.schedule_named_entity_reextract(w.org, 5), w.p.schedule_named_entity_reproject(w.org, 5)):
        with pytest.raises(SchemaValidationError):
            await call
    assert await w.p.get_due_named_entity_persist_retries(10) == []


async def test_a_reextract_job_is_due_now_keeps_its_count_and_replaces_a_write_retry_with_a_fresh_one(w: Org) -> None:
    await w.p.schedule_named_entity_reextract(w.org, "r1")
    job = await w.node("namedEntityPersistRetries", "r1")
    assert job["reextract"] is True and job["attempts"] == 0 and job["dueAt"] == job["createdAtTimestamp"]
    await w.p.update_node("r1", "namedEntityPersistRetries", {"attempts": 3, "dueAt": 5})
    await w.p.schedule_named_entity_reextract(w.org, "r1")
    job = await w.node("namedEntityPersistRetries", "r1")
    assert job["attempts"] == 3 and job["dueAt"] == 5                                              # an earlier due time and the count stay
    await w.p.schedule_named_entity_persist_retry(w.org, "r2", "boom", 4)
    await w.p.schedule_named_entity_reextract(w.org, "r2")
    r2 = await w.node("namedEntityPersistRetries", "r2")
    assert r2["reextract"] is True and r2["attempts"] == 0 and r2["dueAt"] <= r2["updatedAtTimestamp"]


async def test_a_reproject_is_due_now_counts_no_failure_and_leaves_a_pending_marker_alone(w: Org) -> None:
    await w.p.schedule_named_entity_reproject(w.org, "r1")
    m = await w.node("namedEntityPersistRetries", "r1")
    assert m["attempts"] == 0 and m["lastError"] == "reproject" and m["dueAt"] == m["createdAtTimestamp"]
    await w.p.schedule_named_entity_persist_retry(w.org, "r2", "boom")
    before = await w.node("namedEntityPersistRetries", "r2")
    await w.p.schedule_named_entity_reproject(w.org, "r2")
    assert await w.node("namedEntityPersistRetries", "r2") == before


async def test_due_retries_are_the_ones_due_now_oldest_first_and_jobs_are_apart(w: Org, orgs: tuple[Org, Org]) -> None:
    async def put(key: str, due: int, org: Org = w, **doc: object) -> None:
        await org.p.batch_upsert_nodes([{"_key": key, "orgId": org.org, "recordId": key, "attempts": 1, "dueAt": due, **doc}], "namedEntityPersistRetries")
    far = 10 ** 15
    await put("c", 30)
    await put("a", 10)
    await put("b", 20, reextract=False)
    await put("later", far)
    await put("job2", 25, reextract=True)
    await put("job1", 5, reextract=True)
    assert await w.p.get_due_named_entity_persist_retries(10) == [{"recordId": k, "orgId": w.org, "attempts": 1, "dueAt": d} for k, d in (("a", 10), ("b", 20), ("c", 30))]
    assert [r["recordId"] for r in await w.p.get_due_named_entity_persist_retries(2)] == ["a", "b"]
    assert [r["recordId"] for r in await w.p.get_due_named_entity_persist_retries(0)] == ["a"]
    assert [r["recordId"] for r in await w.p.get_due_named_entity_persist_retries(10, reextract=True)] == ["job1", "job2"]
    with in_org(orgs[1].org):
        assert await orgs[1].p.get_due_named_entity_persist_retries(10) == []                         # another org's retries are not this org's


async def test_clear_drops_a_retry_only_while_it_is_the_one_read(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.batch_upsert_nodes([{"_key": "r1", "orgId": w.org, "recordId": "r1", "attempts": 1, "dueAt": 10}], "namedEntityPersistRetries")
    assert await w.p.clear_named_entity_persist_retry("r1", 11) is False and await w.node("namedEntityPersistRetries", "r1")     # rescheduled meanwhile
    with in_org(orgs[1].org):
        assert await orgs[1].p.clear_named_entity_persist_retry("r1", 10) is False
    assert await w.node("namedEntityPersistRetries", "r1")
    assert await w.p.clear_named_entity_persist_retry("r1", 10) is True and await w.node("namedEntityPersistRetries", "r1") is None
    assert await w.p.clear_named_entity_persist_retry("r1", 10) is False
    await w.p.batch_upsert_nodes([{"_key": "r2", "orgId": w.org, "recordId": "r2", "attempts": 1, "dueAt": 10}], "namedEntityPersistRetries")
    assert await w.p.clear_named_entity_persist_retry("r2", None) is True                              # none given: whatever is pending


async def test_clear_inside_a_transaction_rolls_back_with_it(w: Org) -> None:
    await w.p.schedule_named_entity_reproject(w.org, "r1")
    tid = await w.p.begin_transaction([], [])
    assert await w.p.clear_named_entity_persist_retry("r1", None, tid) is True
    await w.p.rollback_transaction(tid)
    assert await w.node("namedEntityPersistRetries", "r1")


async def test_a_system_sweep_reads_and_clears_the_retries_of_every_org(pg_provider, pg_db, orgs: tuple[Org, Org]) -> None:
    a, b = orgs
    tag = uuid.uuid4().hex[:6]
    for o in (a, b):
        with in_org(o.org):
            await o.p.batch_upsert_nodes([{"_key": o.org, "name": "o", "isActive": True}], "organizations")      # the sweep lists orgs by their node
            await o.p.schedule_named_entity_reproject(o.org, f"r-{tag}")
    token = org_context.push(None)
    try:
        with org_context.system("test.retry-sweep"):
            rows = [r for r in await pg_provider.get_due_named_entity_persist_retries(10_000) if r["recordId"] == f"r-{tag}"]
            assert {r["orgId"] for r in rows} == {a.org, b.org}
            assert len(await pg_provider.get_due_named_entity_persist_retries(1)) == 1                  # the limit bounds the whole pass, not each org
            assert await pg_provider.clear_named_entity_persist_retry(f"r-{tag}", None) is True
    finally:
        org_context.pop(token)
    for o in (a, b):
        with in_org(o.org):
            assert await o.node("namedEntityPersistRetries", f"r-{tag}") is None


async def test_failed_retry_reads_and_writes_raise_and_work_again(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.p.schedule_named_entity_reproject(w.org, "r1")
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    for call in (w.p.get_due_named_entity_persist_retries(10), w.p.clear_named_entity_persist_retry("r1", None), w.p.schedule_named_entity_reextract(w.org, "r1"),
                 w.p.schedule_named_entity_persist_retry(w.org, "r1", "e"), w.p.schedule_named_entity_reproject(w.org, "r9")):
        with pytest.raises(RuntimeError, match="store down"):
            await call
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p.get_due_named_entity_persist_retries(10)) == 1
    assert await w.p.clear_named_entity_persist_retry("r1", None) is True


async def test_no_org_is_an_error_not_an_empty_answer(pg_provider) -> None:
    token = org_context.push(None)
    try:
        for call in (pg_provider.create_named_entities_if_absent([{"id": "x"}]), pg_provider.find_orphan_named_entities("o"), pg_provider.mark_orphan_named_entities("o", 1),
                     pg_provider.resolve_named_entity_redirects("o", ["a"]), pg_provider.schedule_named_entity_reproject("o", "r"), pg_provider.copy_named_entity_mentions("a", "b")):
            with pytest.raises(org_context.MissingOrgContext):
                await call
    finally:
        org_context.pop(token)
