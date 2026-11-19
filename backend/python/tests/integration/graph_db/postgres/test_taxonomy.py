"""Taxonomy nodes and the records' edges to them on PostgreSQL, against a real database, as ``Neo4jProvider`` answers them: resolution (find, create,
aliases, the subcategory chain), a record's taxonomy, reach (membership, unreached) and consolidation (moving edges, the legacy and merged pages).

Each call stays in the org of the call: a record or node whose ``orgId`` names another org is not counted or changed, and the same ids in another org
are neither read nor written. The consolidation cases of ``test_taxonomy_consolidation_real_backends.py`` run here through the same consolidator.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.core.org_context import OrgContext, OrgMismatch, bind
from app.modules.entity_resolution.consolidation import TaxonomyConsolidator
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.services.graph_db.postgres import taxonomy as pg_taxonomy
from app.services.graph_db.postgres.base import _doc
from app.services.graph_db.taxonomy import TAXONOMY_EDGE_COLLECTIONS, TaxonomyLink

from .test_grants import failing, in_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

TOPICS, CATS, SUB1, SUB2, SUB3, LANGS, DEPTS = "topics", "categories", "subcategories1", "subcategories2", "subcategories3", "languages", "departments"
EDGE = {**TAXONOMY_EDGE_COLLECTIONS, DEPTS: "belongsToDepartment"}


class Org:
    """A provider bound to one org, with the graph helpers of these tests."""

    def __init__(self, p: PostgresGraphProvider, org: str) -> None:
        self.p, self.org = p, org

    async def node(self, coll: str, key: str, name: str | None = None, *, org: str | None = "", **doc: object) -> str:
        """A taxonomy node: canonical (of this org unless ``org`` names another), or legacy with ``org=None``."""
        d: dict[str, Any] = {"_key": key, "name": name or key, "createdAtTimestamp": 1, **doc}
        if org is not None:
            d |= {"orgId": org or self.org, "normalizedName": (name or key).casefold().replace("-", " ")}
        await self.p.batch_upsert_nodes([d], coll)
        return key

    async def record(self, key: str, *, org: str | None = None, **doc: object) -> str:
        if await self.p.get_document(key, "records") is None:
            await self.p.batch_upsert_nodes([{"_key": key, "orgId": org or self.org, "recordName": key, "connectorId": "c1", **doc}], "records")
        return key

    async def link(self, record: str, coll: str, node: str, *, org: str | None = None, **doc: object) -> None:
        await self.record(record, org=org)
        await self.p.batch_create_edges([{"from_collection": "records", "from_id": record, "to_collection": coll, "to_id": node, "createdAtTimestamp": 1, **doc}], EDGE[coll])

    async def edges(self, record: str, coll: str = TOPICS) -> list[tuple[str, Any, Any]]:
        """(target, extractedName, mergedFrom) of the record's edges to ``coll``, by target."""
        rows = await self.p._q(lambda tx: tx.fetch("""SELECT to_id, doc FROM graph.edges WHERE org_id = $1 AND coll = $2 AND from_coll = 'records' AND from_id = $3
            AND to_coll = $4 ORDER BY to_id""", self.org, EDGE[coll], record, coll), None)
        docs = [(r["to_id"], _doc(r["doc"])) for r in rows]
        return [(t, d.get("extractedName"), d.get("mergedFrom")) for t, d in docs]

    async def edge_doc(self, record: str, coll: str, node: str) -> dict | None:
        r = await self.p._q(lambda tx: tx.fetchrow("""SELECT doc FROM graph.edges WHERE org_id = $1 AND coll = $2 AND from_coll = 'records' AND from_id = $3
            AND to_coll = $4 AND to_id = $5""", self.org, EDGE[coll], record, coll, node), None)
        return _doc(r["doc"]) if r else None

    async def doc(self, coll: str, key: str) -> dict | None:
        return await self.p.get_document(key, coll)

    async def count(self, coll: str) -> int:
        return int(await self.p._q(lambda tx: tx.fetchval("SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = $2", self.org, coll), None))

    async def hierarchy(self) -> list[tuple[str, str, str, str]]:
        rows = await self.p._q(lambda tx: tx.fetch("""SELECT from_coll, from_id, to_coll, to_id FROM graph.edges WHERE org_id = $1 AND coll = 'interCategoryRelations'
            ORDER BY from_coll, from_id, to_coll, to_id""", self.org), None)
        return [tuple(r) for r in rows]


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


def _consolidator(p: PostgresGraphProvider) -> TaxonomyConsolidator:
    return TaxonomyConsolidator(graph_provider=p, entity_store=None, logger=logging.getLogger("pg-taxonomy"))


async def _commit_or_roll_back(p: PostgresGraphProvider, tid: str, ok: bool) -> None:
    await (p.commit_transaction(tid) if ok else p.rollback_transaction(tid))


# ---- a record's taxonomy ---------------------------------------------------------------------------------------------------------------

async def test_a_records_taxonomy_entities_come_group_by_group_with_their_level_and_org(w: Org) -> None:
    await w.node(CATS, "cat", "Finance")
    await w.node(SUB2, "sub", "Budgets")
    await w.p.batch_upsert_nodes([{"_key": "dept", "departmentName": "Sales", "orgId": None}], DEPTS)
    await w.node(TOPICS, "mine", "Mine", aliases=["Me", "", "Myself"])
    await w.node(TOPICS, "legacy", "Legacy", org=None)
    await w.p.batch_upsert_nodes([{"_key": "nameless"}], LANGS)
    for coll, node in ((CATS, "cat"), (SUB2, "sub"), (DEPTS, "dept"), (TOPICS, "mine"), (TOPICS, "legacy"), (LANGS, "nameless")):
        await w.link("r1", coll, node)
    await w.p.batch_create_edges([{"from_collection": "records", "from_id": "r1", "to_collection": CATS, "to_id": "cat"}], "belongsToTopic")   # not a topic group edge
    await w.p.batch_create_edges([{"from_collection": "records", "from_id": "r1", "to_collection": TOPICS, "to_id": "gone"}], "belongsToTopic")  # its node is gone

    rows = await w.p.get_taxonomy_entities_for_record("r1")

    assert rows == [
        {"entityId": "cat", "name": "Finance", "entityType": "category", "level": None, "aliases": [], "orgId": w.org},
        {"entityId": "sub", "name": "Budgets", "entityType": "subcategory", "level": "2", "aliases": [], "orgId": w.org},
        {"entityId": "dept", "name": "Sales", "entityType": "department", "level": None, "aliases": [], "orgId": None},
        {"entityId": "legacy", "name": "Legacy", "entityType": "topic", "level": None, "aliases": [], "orgId": None},
        {"entityId": "mine", "name": "Mine", "entityType": "topic", "level": None, "aliases": ["Me", "Myself"], "orgId": w.org},
        {"entityId": "nameless", "name": "nameless", "entityType": "language", "level": None, "aliases": [], "orgId": None},
    ]
    assert await w.p.get_taxonomy_entities_for_record("") == []
    assert await w.p.get_taxonomy_entities_for_record("no-such-record") == []


async def test_a_records_taxonomy_entities_are_read_inside_the_callers_transaction_and_in_its_org_only(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "t1")
    await w.record("r1")
    tid = await w.p.begin_transaction([], [])
    try:
        await w.p.batch_create_edges([{"from_collection": "records", "from_id": "r1", "to_collection": TOPICS, "to_id": "t1"}], "belongsToTopic", transaction=tid)
        assert [r["entityId"] for r in await w.p.get_taxonomy_entities_for_record("r1", transaction=tid)] == ["t1"]
        assert await w.p.get_taxonomy_entities_for_record("r1") == []          # not committed
    finally:
        await w.p.rollback_transaction(tid)
    with in_org(orgs[1].org):
        await orgs[1].link("r1", TOPICS, "t1")      # the record's edge in the other org, to a node it does not have
        assert await orgs[1].p.get_taxonomy_entities_for_record("r1") == []
    assert await w.p.get_taxonomy_entities_for_record("r1") == []


async def test_record_taxonomy_links_carry_each_edges_spelling_and_departments_only_when_asked(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "canon", "Pricing")
    await w.node(TOPICS, "legacy", "Pricing strategy", org=None)
    await w.node(SUB1, "sub", "Budgets")
    await w.p.batch_upsert_nodes([{"_key": "dept", "departmentName": "Sales"}], DEPTS)
    await w.link("r2", TOPICS, "canon", extractedName="pricing", extractedNames=["pricing", "prices"], migratedFrom="legacy")
    await w.link("r1", TOPICS, "legacy")
    await w.link("r1", SUB1, "sub", extractedName="budget")
    await w.link("r1", DEPTS, "dept")
    await w.link("r3", TOPICS, "canon")
    with in_org(orgs[1].org):
        await orgs[1].node(TOPICS, "canon", "Theirs")
        await orgs[1].link("r1", TOPICS, "canon")

    rows = await w.p.get_record_taxonomy_links(["r2", "r1", "r1", "", "unknown"])

    assert rows == [
        {"recordId": "r1", "collection": SUB1, "entityId": "sub", "name": "Budgets", "canonical": True, "extractedName": "budget", "extractedNames": None, "migrated": False},
        {"recordId": "r1", "collection": TOPICS, "entityId": "legacy", "name": "Pricing strategy", "canonical": False, "extractedName": None, "extractedNames": None,
         "migrated": False},
        {"recordId": "r2", "collection": TOPICS, "entityId": "canon", "name": "Pricing", "canonical": True, "extractedName": "pricing",
         "extractedNames": ["pricing", "prices"], "migrated": True},
    ]
    (link,) = [x for x in map(TaxonomyLink.from_row, rows) if x and x.record_id == "r2"]
    assert link.extracted_names == ("pricing", "prices") and link.migrated
    with_departments = await w.p.get_record_taxonomy_links(["r1"], include_departments=True)
    assert [(r["collection"], r["entityId"], r["name"], r["extractedName"]) for r in with_departments] == [
        (DEPTS, "dept", "Sales", None), (SUB1, "sub", "Budgets", "budget"), (TOPICS, "legacy", "Pricing strategy", None)]
    assert await w.p.get_record_taxonomy_links([]) == []


async def test_record_taxonomy_links_raise_when_the_read_fails_and_follow_the_transaction(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.node(TOPICS, "t1")
    await w.record("r1")
    tid = await w.p.begin_transaction([], [])
    try:
        await w.p.batch_create_edges([{"from_collection": "records", "from_id": "r1", "to_collection": TOPICS, "to_id": "t1"}], "belongsToTopic", transaction=tid)
        assert [r["entityId"] for r in await w.p.get_record_taxonomy_links(["r1"], transaction=tid)] == ["t1"]
    finally:
        await w.p.rollback_transaction(tid)
    assert await w.p.get_record_taxonomy_links(["r1"]) == []
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError, match="store down"):       # never an answer of fewer labels
        await w.p.get_record_taxonomy_links(["r1"])


# ---- reach -----------------------------------------------------------------------------------------------------------------------------

async def test_membership_is_the_connectors_and_groups_of_the_orgs_live_records(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "t1")
    await w.node(TOPICS, "lonely")
    await w.node(SUB3, "deep")
    await w.p.batch_upsert_nodes([{"_key": "dept", "departmentName": "Sales"}], DEPTS)
    await w.record("a", connectorId="c2", recordGroupId="g1")
    await w.record("b", connectorId="c1", recordGroupId="g2")
    await w.record("c", connectorId="c1")
    await w.record("trashed", connectorId="c9", recordGroupId="g9", isDeleted=True)
    await w.record("theirs", org="someone-else", connectorId="c8", recordGroupId="g8")
    for r in ("a", "b", "c", "trashed", "theirs"):
        await w.link(r, TOPICS, "t1")
    await w.link("a", SUB3, "deep")
    await w.link("b", DEPTS, "dept")
    await w.link("trashed", TOPICS, "lonely")
    refs = [{"id": "t1", "type": "topic"}, {"id": "t1", "type": "topic"}, {"id": "lonely", "type": "topic"}, {"id": "missing", "type": "topic"},
            {"id": "deep", "type": "subcategory"}, {"id": "dept", "type": "department"}, {"id": "t1", "type": "record"}, {"id": "", "type": "topic"},
            {"id": "t1", "type": "category"}]

    got = await w.p.get_taxonomy_entity_membership(refs, w.org)

    assert got == {
        ("topic", "t1"): {"connectorIds": ["c1", "c2"], "recordGroupIds": ["g1", "g2"]},
        ("topic", "lonely"): {"connectorIds": [], "recordGroupIds": []},
        ("topic", "missing"): {"connectorIds": [], "recordGroupIds": []},
        ("subcategory", "deep"): {"connectorIds": ["c2"], "recordGroupIds": ["g1"]},
        ("department", "dept"): {"connectorIds": ["c1"], "recordGroupIds": ["g2"]},
        ("category", "t1"): {"connectorIds": [], "recordGroupIds": []},          # the id is a topic, not a category
    }
    assert await w.p.get_taxonomy_entity_membership(refs, "") == {}
    assert await w.p.get_taxonomy_entity_membership([], w.org) == {}
    with in_org(orgs[1].org):
        assert await orgs[1].p.get_taxonomy_entity_membership([{"id": "t1", "type": "topic"}], orgs[1].org) == {
            ("topic", "t1"): {"connectorIds": [], "recordGroupIds": []}}


async def test_a_person_is_reached_either_way_and_an_organisation_from_the_record_only(w: Org) -> None:
    await w.p.batch_upsert_nodes([{"_key": "ann", "orgId": w.org}, {"_key": "bob", "orgId": w.org}, {"_key": "out", "orgId": "someone-else"}], "users")
    await w.p.batch_upsert_nodes([{"_key": "acme", "isExternal": True, "parentOrgId": w.org}, {"_key": "own", "isExternal": False, "parentOrgId": w.org},
                                  {"_key": "rival", "isExternal": True, "parentOrgId": "someone-else"}], "organizations")
    await w.record("mail", connectorId="c-mail", recordGroupId="inbox")
    await w.record("case", connectorId="c-crm")
    rel = [("records", "mail", "users", "ann"), ("users", "ann", "records", "case"), ("users", "bob", "records", "mail"), ("records", "case", "users", "out"),
           ("records", "case", "organizations", "acme"), ("organizations", "acme", "records", "mail"), ("records", "case", "organizations", "own"),
           ("records", "case", "organizations", "rival")]
    await w.p.batch_create_edges([{"from_collection": fc, "from_id": fi, "to_collection": tc, "to_id": ti, "edgeType": "MENTIONS"} for fc, fi, tc, ti in rel],
                                 "entityRelations")

    got = await w.p.get_taxonomy_entity_membership([{"id": i, "type": "person"} for i in ("ann", "bob", "out")]
                                                   + [{"id": i, "type": "organization"} for i in ("acme", "own", "rival")], w.org)

    assert got == {
        ("person", "ann"): {"connectorIds": ["c-crm", "c-mail"], "recordGroupIds": ["inbox"]},
        ("person", "bob"): {"connectorIds": ["c-mail"], "recordGroupIds": ["inbox"]},
        ("person", "out"): {"connectorIds": [], "recordGroupIds": []},        # a user of another org reaches nothing
        ("organization", "acme"): {"connectorIds": ["c-crm"], "recordGroupIds": []},      # an edge from the account to a record does not count
        ("organization", "own"): {"connectorIds": [], "recordGroupIds": []},
        ("organization", "rival"): {"connectorIds": [], "recordGroupIds": []},
    }


async def test_membership_follows_the_transaction_and_raises_when_the_read_fails(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.node(TOPICS, "t1")
    await w.record("a")
    tid = await w.p.begin_transaction([], [])
    try:
        await w.p.batch_create_edges([{"from_collection": "records", "from_id": "a", "to_collection": TOPICS, "to_id": "t1"}], "belongsToTopic", transaction=tid)
        assert (await w.p.get_taxonomy_entity_membership([{"id": "t1", "type": "topic"}], w.org, transaction=tid))[("topic", "t1")]["connectorIds"] == ["c1"]
    finally:
        await w.p.rollback_transaction(tid)
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError, match="store down"):
        await w.p.get_taxonomy_entity_membership([{"id": "t1", "type": "topic"}], w.org)


async def test_unreached_entities_are_those_no_live_record_of_the_org_links_or_whose_node_is_gone(w: Org, orgs: tuple[Org, Org]) -> None:
    for key in ("live", "trashed-only", "theirs-only", "bare"):
        await w.node(TOPICS, key)
    await w.node(SUB1, "s1")
    await w.node(LANGS, "en")
    await w.p.batch_upsert_nodes([{"_key": "dept", "departmentName": "Sales"}], DEPTS)
    await w.link("r", TOPICS, "live")
    await w.link("r", SUB1, "s1")
    await w.link("r", DEPTS, "dept")
    await w.link("gone-r", TOPICS, "trashed-only")
    await w.p.batch_upsert_nodes([{"_key": "gone-r", "isDeleted": True}], "records")
    await w.link("x", TOPICS, "theirs-only", org="someone-else")
    for i in range(30):                      # a language most records link to
        await w.link(f"m{i}", LANGS, "en")
    refs = [{"id": k, "type": "topic"} for k in ("live", "trashed-only", "theirs-only", "bare", "never-created", "live")] + [
        {"id": "s1", "type": "subcategory"}, {"id": "en", "type": "language"}, {"id": "dept", "type": "department"}, {"id": "s1", "type": "category"},
        {"id": "ann", "type": "person"}, {"id": "x", "type": "organization"}]

    assert await w.p.get_unreached_taxonomy_entities(refs, w.org) == {
        ("topic", "trashed-only"), ("topic", "theirs-only"), ("topic", "bare"), ("topic", "never-created"), ("category", "s1")}
    assert await w.p.get_unreached_taxonomy_entities(refs, "") == set()
    assert await w.p.get_unreached_taxonomy_entities([], w.org) == set()
    with in_org(orgs[1].org):
        assert await orgs[1].p.get_unreached_taxonomy_entities([{"id": "live", "type": "topic"}], orgs[1].org) == {("topic", "live")}


async def test_unreached_for_another_org_raises_instead_of_reporting_every_entity_unreached(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "t1")
    await w.link("r", TOPICS, "t1")
    with pytest.raises(OrgMismatch):
        await w.p.get_unreached_taxonomy_entities([{"id": "t1", "type": "topic"}], orgs[1].org)
    assert await w.p.get_unreached_taxonomy_entities([{"id": "t1", "type": "topic"}], w.org) == set()


async def test_unreached_follows_the_transaction_and_never_answers_empty_for_a_failure(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.node(TOPICS, "t1")
    await w.link("r", TOPICS, "t1")
    tid = await w.p.begin_transaction([], [])
    try:
        await w.p.batch_upsert_nodes([{"_key": "r", "isDeleted": True}], "records", transaction=tid)
        assert await w.p.get_unreached_taxonomy_entities([{"id": "t1", "type": "topic"}], w.org, transaction=tid) == {("topic", "t1")}
    finally:
        await w.p.rollback_transaction(tid)
    assert await w.p.get_unreached_taxonomy_entities([{"id": "t1", "type": "topic"}], w.org) == set()
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError, match="store down"):
        await w.p.get_unreached_taxonomy_entities([{"id": "t1", "type": "topic"}], w.org)


# ---- resolution ------------------------------------------------------------------------------------------------------------------------

async def test_find_returns_the_orgs_live_canonical_nodes_by_name_or_alias(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "b-node", "Onboarding", aliases=["New hire onboarding"], normalizedAliases=["new hire onboarding"])
    await w.node(TOPICS, "a-node", "Hiring")
    await w.node(TOPICS, "merged", "Onboarding", mergedInto="b-node")
    await w.node(TOPICS, "legacy", "Onboarding", org=None, normalizedName="onboarding")
    await w.node(TOPICS, "theirs", "Onboarding", org="someone-else")
    await w.node(CATS, "cat", "Onboarding")
    await w.node(TOPICS, "scalar", "Other", normalizedAliases="onboarding")       # not a list: no alias matches
    with in_org(orgs[1].org):
        await orgs[1].node(TOPICS, "b-node", "Onboarding")

    rows = await w.p.find_taxonomy_nodes(TOPICS, w.org, ["onboarding", "new hire onboarding", "hiring", "onboarding"])

    assert rows == [
        {"id": "a-node", "name": "Hiring", "normalizedName": "hiring", "aliases": [], "normalizedAliases": []},
        {"id": "b-node", "name": "Onboarding", "normalizedName": "onboarding", "aliases": ["New hire onboarding"], "normalizedAliases": ["new hire onboarding"]},
    ]
    assert [r["id"] for r in await w.p.find_taxonomy_nodes(TOPICS, w.org, ["new hire onboarding"])] == ["b-node"]
    assert await w.p.find_taxonomy_nodes("records", w.org, ["onboarding"]) == []
    assert await w.p.find_taxonomy_nodes(TOPICS, "", ["onboarding"]) == []
    assert await w.p.find_taxonomy_nodes(TOPICS, w.org, []) == []
    with in_org(orgs[1].org):
        assert [(r["id"], r["aliases"]) for r in await orgs[1].p.find_taxonomy_nodes(TOPICS, orgs[1].org, ["onboarding", "new hire onboarding"])] == [("b-node", [])]


async def test_create_keeps_the_first_spelling_and_writes_no_aliases(w: Org, orgs: tuple[Org, Org]) -> None:
    key = taxonomy_node_key(w.org, TOPICS, "onboarding")
    node = {"id": key, "name": "Onboarding", "normalizedName": "onboarding", "orgId": w.org, "createdAtTimestamp": 1, "aliases": ["x"], "mergedInto": None}

    await w.p.create_taxonomy_node_if_absent(TOPICS, node)
    await w.p.create_taxonomy_node_if_absent(TOPICS, {**node, "name": "On-boarding", "createdAtTimestamp": 2})

    doc = await w.doc(TOPICS, key)
    assert (doc["name"], doc["createdAtTimestamp"]) == ("Onboarding", 1) and "aliases" not in doc and "mergedInto" not in doc
    assert [r["id"] for r in await w.p.find_taxonomy_nodes(TOPICS, w.org, ["onboarding"])] == [key]
    await w.p.create_taxonomy_node_if_absent(TOPICS, {"_key": "by-key", "name": "K", "normalizedName": "k", "orgId": w.org})
    assert (await w.doc(TOPICS, "by-key"))["name"] == "K"
    with pytest.raises(ValueError, match="not a taxonomy collection"):
        await w.p.create_taxonomy_node_if_absent("records", node)
    with pytest.raises(ValueError, match="needs an id"):
        await w.p.create_taxonomy_node_if_absent(TOPICS, {"name": "No id", "orgId": w.org})
    with pytest.raises(OrgMismatch):
        await w.p.create_taxonomy_node_if_absent(TOPICS, {**node, "id": "other", "orgId": orgs[1].org})
    assert await w.doc(TOPICS, "other") is None
    with in_org(orgs[1].org):
        assert await orgs[1].doc(TOPICS, key) is None


async def test_concurrent_creators_converge_on_one_node(w: Org) -> None:
    key = taxonomy_node_key(w.org, TOPICS, "release")
    await asyncio.gather(*(w.p.create_taxonomy_node_if_absent(TOPICS, {"id": key, "name": f"Release {i}", "normalizedName": "release", "orgId": w.org})
                           for i in range(8)))
    assert await w.count(TOPICS) == 1 and (await w.doc(TOPICS, key))["name"].startswith("Release ")


async def test_create_follows_the_transaction_and_a_second_creator_waits_for_the_first(w: Org) -> None:
    node = {"id": "k", "name": "First", "normalizedName": "first", "orgId": w.org}
    tid = await w.p.begin_transaction([], [])
    await w.p.create_taxonomy_node_if_absent(TOPICS, node, transaction=tid)
    await w.p.rollback_transaction(tid)
    assert await w.doc(TOPICS, "k") is None

    t1 = await w.p.begin_transaction([], [])
    ok = False
    try:
        await w.p.create_taxonomy_node_if_absent(TOPICS, node, transaction=t1)
        second = asyncio.ensure_future(w.p.create_taxonomy_node_if_absent(TOPICS, {**node, "name": "Second"}))
        await asyncio.sleep(0.3)
        assert not second.done()                      # it waits on the first's key, then finds it taken
        ok = True
    finally:
        await _commit_or_roll_back(w.p, t1, ok)
    await asyncio.wait_for(second, 10)
    assert (await w.doc(TOPICS, "k"))["name"] == "First" and await w.count(TOPICS) == 1


async def test_the_hierarchy_edge_links_each_level_to_its_parent_once(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(CATS, "c")
    await w.node(SUB1, "s1")
    await w.node(SUB2, "s2")
    await w.node(SUB3, "s3")
    await w.p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "c")
    await w.p.ensure_taxonomy_hierarchy_edge(SUB2, "s2", "s1")
    await w.p.ensure_taxonomy_hierarchy_edge(SUB3, "s3", "s2")
    created = await w.p._q(lambda tx: tx.fetchval("SELECT doc FROM graph.edges WHERE org_id = $1 AND coll = 'interCategoryRelations' AND from_id = 's1'", w.org), None)
    await w.p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "c")
    again = await w.p._q(lambda tx: tx.fetchval("SELECT doc FROM graph.edges WHERE org_id = $1 AND coll = 'interCategoryRelations' AND from_id = 's1'", w.org), None)

    assert await w.hierarchy() == [(SUB1, "s1", CATS, "c"), (SUB2, "s2", SUB1, "s1"), (SUB3, "s3", SUB2, "s2")]
    assert isinstance(_doc(created)["createdAtTimestamp"], int) and again == created          # an existing edge is left as it is
    await w.p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "missing-parent")
    await w.p.ensure_taxonomy_hierarchy_edge(SUB2, "missing-child", "s1")
    await w.p.ensure_taxonomy_hierarchy_edge(SUB2, "s2", "c")             # c is a category, not a level-1 subcategory
    assert len(await w.hierarchy()) == 3
    for bad in (CATS, TOPICS, "records"):
        with pytest.raises(ValueError, match="not a subcategory level"):
            await w.p.ensure_taxonomy_hierarchy_edge(bad, "s1", "c")
    with in_org(orgs[1].org):
        await orgs[1].p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "c")      # the same ids, nodes this org does not have
        assert await orgs[1].hierarchy() == []


async def test_concurrent_hierarchy_writers_leave_one_edge(w: Org) -> None:
    await w.node(CATS, "c")
    await w.node(SUB1, "s1")
    await asyncio.gather(*(w.p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "c") for _ in range(10)))
    assert await w.hierarchy() == [(SUB1, "s1", CATS, "c")]


async def test_aliases_union_by_normalized_form_stay_aligned_and_are_capped(w: Org) -> None:
    await w.node(TOPICS, "t", "Release", aliases=["Ship it"], normalizedAliases=["ship it"])
    await w.p.add_taxonomy_aliases(TOPICS, "t", ["Go live", "SHIP IT", "Launch", "Go-live", ""], ["go live", "ship it", "launch", "go live", "blank"], org_id=w.org)
    doc = await w.doc(TOPICS, "t")
    assert (doc["aliases"], doc["normalizedAliases"]) == (["Ship it", "Go live", "Launch"], ["ship it", "go live", "launch"])

    await w.p.add_taxonomy_aliases(TOPICS, "t", ["Rollout", "Deploy"], ["rollout", "deploy"], org_id=w.org, max_aliases=4)
    doc = await w.doc(TOPICS, "t")
    assert (doc["aliases"], doc["normalizedAliases"]) == (["Ship it", "Go live", "Launch", "Rollout"], ["ship it", "go live", "launch", "rollout"])
    await w.p.add_taxonomy_aliases(TOPICS, "t", ["Go live"], ["go live"], org_id=w.org, max_aliases=2)       # a lower cap cuts what is stored
    assert (await w.doc(TOPICS, "t"))["normalizedAliases"] == ["ship it", "go live"]
    await w.p.add_taxonomy_aliases(TOPICS, "t", ["X"], ["x"], org_id=w.org, max_aliases=0)                   # at least one
    assert (await w.doc(TOPICS, "t"))["normalizedAliases"] == ["ship it"]


async def test_a_skewed_or_missing_alias_list_heals_on_the_next_write(w: Org) -> None:
    await w.node(TOPICS, "skewed", "A", aliases=["One", "Two", "Three"], normalizedAliases=["one", "two"])
    await w.node(TOPICS, "none", "B")
    await w.node(TOPICS, "garbled", "C", aliases="not a list", normalizedAliases=None)
    await w.p.add_taxonomy_aliases(TOPICS, "skewed", ["Three"], ["three"], org_id=w.org)
    await w.p.add_taxonomy_aliases(TOPICS, "none", ["Bee"], ["bee"], org_id=w.org)
    await w.p.add_taxonomy_aliases(TOPICS, "garbled", ["Sea"], ["sea"], org_id=w.org)
    assert [(d["aliases"], d["normalizedAliases"]) for d in [await w.doc(TOPICS, k) for k in ("skewed", "none", "garbled")]] == [
        (["One", "Two", "Three"], ["one", "two", "three"]), (["Bee"], ["bee"]), (["Sea"], ["sea"])]
    await w.node(TOPICS, "skewed2", "D", aliases=["One"], normalizedAliases=["one", "two"])
    await w.p.add_taxonomy_aliases(TOPICS, "skewed2", ["One"], ["one"], org_id=w.org)         # nothing new: the lists are still cut to one length
    assert (await w.doc(TOPICS, "skewed2"))["normalizedAliases"] == ["one"]


async def test_aliases_land_only_on_a_node_of_the_org(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "mine", "A")
    await w.node(TOPICS, "theirs", "A", org=orgs[1].org)
    await w.node(TOPICS, "legacy", "A", org=None)
    for key in ("mine", "theirs", "legacy", "missing"):
        await w.p.add_taxonomy_aliases(TOPICS, key, ["Alpha"], ["alpha"], org_id=w.org)
    assert {k: (await w.doc(TOPICS, k)).get("aliases") for k in ("mine", "theirs", "legacy")} == {"mine": ["Alpha"], "theirs": None, "legacy": None}
    assert await w.doc(TOPICS, "missing") is None
    with in_org(orgs[1].org):
        await orgs[1].node(TOPICS, "mine", "A")
        await orgs[1].p.add_taxonomy_aliases(TOPICS, "mine", ["Zed"], ["zed"], org_id=w.org)      # this org's id, from the other org
        assert (await orgs[1].doc(TOPICS, "mine")).get("aliases") is None
    await w.p.add_taxonomy_aliases(TOPICS, "mine", [], [], org_id=w.org)
    await w.p.add_taxonomy_aliases(TOPICS, "", ["B"], ["b"], org_id=w.org)
    assert (await w.doc(TOPICS, "mine"))["aliases"] == ["Alpha"]
    with pytest.raises(ValueError, match="not a taxonomy collection"):
        await w.p.add_taxonomy_aliases("records", "mine", ["B"], ["b"], org_id=w.org)
    with pytest.raises(ValueError, match="needs an org"):
        await w.p.add_taxonomy_aliases(TOPICS, "mine", ["B"], ["b"], org_id="")
    with pytest.raises(ValueError, match="align"):
        await w.p.add_taxonomy_aliases(TOPICS, "mine", ["B", "C"], ["b"], org_id=w.org)


async def test_concurrent_alias_writes_keep_every_alias(w: Org) -> None:
    await w.node(TOPICS, "t", "Release")
    await asyncio.gather(*(w.p.add_taxonomy_aliases(TOPICS, "t", [f"Release {i}"], [f"release {i}"], org_id=w.org) for i in range(12)))
    doc = await w.doc(TOPICS, "t")
    assert sorted(doc["normalizedAliases"]) == sorted(f"release {i}" for i in range(12))
    assert [a.lower() for a in doc["aliases"]] == doc["normalizedAliases"]          # still aligned pair by pair


async def test_an_alias_write_that_changes_nothing_takes_no_lock_and_one_that_does_follows_the_transaction(w: Org) -> None:
    await w.node(TOPICS, "t", "Release", aliases=["Release notes"], normalizedAliases=["release notes"])
    version = "SELECT xmin::text FROM graph.nodes WHERE org_id = $1 AND coll = 'topics' AND id = 't'"
    before = await w.p._q(lambda tx: tx.fetchval(version, w.org), None)
    t1 = await w.p.begin_transaction([], [])
    try:
        await w.p.add_taxonomy_aliases(TOPICS, "t", ["Release notes"], ["release notes"], org_id=w.org, transaction=t1)
        await asyncio.wait_for(w.p.add_taxonomy_aliases(TOPICS, "t", ["Release notes"], ["release notes"], org_id=w.org), 10)      # not blocked by t1
        await w.p.add_taxonomy_aliases(TOPICS, "t", ["Changelog"], ["changelog"], org_id=w.org, transaction=t1)
        assert (await w.doc(TOPICS, "t"))["normalizedAliases"] == ["release notes"]           # not committed
    finally:
        await w.p.rollback_transaction(t1)
    assert await w.p._q(lambda tx: tx.fetchval(version, w.org), None) == before
    assert (await w.doc(TOPICS, "t"))["normalizedAliases"] == ["release notes"]


# ---- consolidation: the cases of test_taxonomy_consolidation_real_backends.py ------------------------------------------------------------

async def test_merge_then_unmerge(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "win", "Bug bash", createdAtTimestamp=1)
    await w.node(TOPICS, "lose", "Bug-bash", createdAtTimestamp=2)
    await w.link("r1", TOPICS, "lose", extractedName="bug-bash")
    await w.link("r2", TOPICS, "lose")
    await w.link("r2", TOPICS, "win")
    await w.link("x1", TOPICS, "lose", org="other")
    consolidator = _consolidator(w.p)

    (group,) = await consolidator.duplicate_groups(TOPICS, org)
    assert (group.winner.key, [n.key for n in group.losers]) == ("win", ["lose"])
    planned = await consolidator.merge(TOPICS, org, "win", "lose", dry_run=True)
    assert planned.edges_moved == 2
    assert await w.edges("r1") == [("lose", "bug-bash", None)]

    applied = await consolidator.merge(TOPICS, org, "win", "lose", dry_run=False)
    assert applied.edges_moved == 2
    assert await w.edges("r1") == [("win", "bug-bash", "lose")]
    assert await w.edges("r2") == [("win", None, None)]           # deduped, the original kept
    assert await w.edges("x1") == [("lose", None, None)]          # another org's record stays
    assert [r["id"] for r in await w.p.find_taxonomy_nodes(TOPICS, org, ["bug bash"])] == ["win"]
    assert await consolidator.duplicate_groups(TOPICS, org) == []

    restored = await consolidator.unmerge(TOPICS, org, "lose", dry_run=False)
    assert restored.edges_moved == 1
    assert await w.edges("r1") == [("lose", "bug-bash", None)]
    assert await w.edges("r2") == [("win", None, None)]


async def test_legacy_migration_then_undo(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "legacy", "Pricing strategy", org=None)
    await w.link("r1", TOPICS, "legacy", extractedName="pricing strategy")
    await w.link("x1", TOPICS, "legacy", org="other")
    consolidator = _consolidator(w.p)

    assert [(n.key, n.records) for n in await consolidator.legacy_nodes(TOPICS, org)] == [("legacy", 1)]
    result = await consolidator.migrate_legacy(TOPICS, org, "legacy", dry_run=False)
    target = taxonomy_node_key(org, TOPICS, "pricing strategy")
    assert result.target_key == target and result.edges_moved == 1
    assert await w.edges("r1") == [(target, "pricing strategy", None)]          # migration provenance is its own field
    assert (await w.edge_doc("r1", TOPICS, target))["migratedFrom"] == "legacy"
    assert await w.edges("x1") == [("legacy", None, None)]
    assert await consolidator.legacy_nodes(TOPICS, org) == []
    assert [r["id"] for r in await w.p.find_taxonomy_nodes(TOPICS, org, ["pricing strategy"])] == [target]

    restored = await consolidator.unmigrate_legacy(TOPICS, org, "legacy", target, dry_run=False)
    assert restored.edges_moved == 1
    assert await w.edges("r1") == [("legacy", "pricing strategy", None)]
    assert "migratedFrom" not in await w.edge_doc("r1", TOPICS, "legacy")


async def test_chained_merges_keep_origins_and_undo_one_link_at_a_time(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "a", "bug-bash", createdAtTimestamp=3)
    await w.node(TOPICS, "b", "Bug bash", createdAtTimestamp=2)
    await w.node(TOPICS, "c", "Bugbash", createdAtTimestamp=1)
    await w.link("ra", TOPICS, "a", extractedName="bug-bash")
    await w.link("rb", TOPICS, "b")
    consolidator = _consolidator(w.p)

    await consolidator.merge(TOPICS, org, "b", "a", dry_run=False)
    await consolidator.merge(TOPICS, org, "c", "b", dry_run=False)
    assert await w.edges("ra") == [("c", "bug-bash", "a")]
    assert await w.edges("rb") == [("c", None, "b")]
    (node,) = await w.p.get_nodes_by_field_in(TOPICS, "id", ["a"], return_fields=["id", "mergedInto"])
    assert node["mergedInto"] == "c"

    assert (await consolidator.unmerge(TOPICS, org, "a", dry_run=False)).edges_moved == 1
    assert await w.edges("ra") == [("a", "bug-bash", None)]
    assert await w.edges("rb") == [("c", None, "b")]
    assert (await consolidator.unmerge(TOPICS, org, "b", dry_run=False)).edges_moved == 1
    assert await w.edges("rb") == [("b", None, None)]


async def test_edges_move_in_batches(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    """More edges than one batch all move, and the count is per edge."""
    monkeypatch.setattr(pg_taxonomy, "_EDGE_MOVE_BATCH", 2)
    await w.node(TOPICS, "w", "Hub", createdAtTimestamp=1)
    await w.node(TOPICS, "l", "hub!", createdAtTimestamp=2)
    for i in range(5):
        await w.link(f"r{i}", TOPICS, "l")
    await w.link("r0", TOPICS, "w")              # one record already on the winner
    assert await w.p.move_taxonomy_edges(TOPICS, "l", "w", w.org, set_merged_from="l") == 5
    for i in range(5):
        assert [t for t, _, _ in await w.edges(f"r{i}")] == ["w"]


async def test_migrate_then_merge_then_undo_both(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "legacy", "Pricing strategy", org=None)
    await w.link("r1", TOPICS, "legacy", extractedName="pricing strategy")
    consolidator = _consolidator(w.p)
    target = (await consolidator.migrate_legacy(TOPICS, org, "legacy", dry_run=False)).target_key
    await w.node(TOPICS, "winner", "Pricing-strategy", createdAtTimestamp=0)
    await consolidator.merge(TOPICS, org, "winner", target, dry_run=False)
    assert [t for t, _, _ in await w.edges("r1")] == ["winner"]

    assert (await consolidator.unmerge(TOPICS, org, target, dry_run=False)).edges_moved == 1
    assert [t for t, _, _ in await w.edges("r1")] == [target]
    assert (await consolidator.unmigrate_legacy(TOPICS, org, "legacy", target, dry_run=False)).edges_moved == 1
    assert await w.edges("r1") == [("legacy", "pricing strategy", None)]


async def test_moving_to_a_missing_node_is_refused(w: Org) -> None:
    await w.node(TOPICS, "a", "Alpha")
    await w.link("r1", TOPICS, "a")
    with pytest.raises(ValueError, match="not found"):
        await w.p.move_taxonomy_edges(TOPICS, "a", "missing", w.org, set_merged_from="a")
    assert [t for t, _, _ in await w.edges("r1")] == ["a"]


async def test_a_shared_legacy_hub_moves_only_this_orgs_edges_in_batches(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pg_taxonomy, "_EDGE_MOVE_BATCH", 2)
    await w.node(TOPICS, "legacy", "Pricing strategy", org=None)
    for i in range(5):
        await w.link(f"x{i}", TOPICS, "legacy", org="other")
        await w.link(f"r{i}", TOPICS, "legacy")
    consolidator = _consolidator(w.p)

    result = await consolidator.migrate_legacy(TOPICS, w.org, "legacy", dry_run=False)
    assert result.edges_moved == 5
    for i in range(5):
        assert [t for t, _, _ in await w.edges(f"r{i}")] == [result.target_key]
        assert [t for t, _, _ in await w.edges(f"x{i}")] == ["legacy"]
    restored = await consolidator.unmigrate_legacy(TOPICS, w.org, "legacy", result.target_key, dry_run=False)
    assert restored.edges_moved == 5
    for i in range(5):
        assert [t for t, _, _ in await w.edges(f"r{i}")] == ["legacy"]


async def test_edges_never_move_onto_another_orgs_node_or_a_legacy_one(w: Org) -> None:
    await w.node(TOPICS, "a", "Alpha")
    await w.node(TOPICS, "theirs", "Alpha", org="other")
    await w.node(TOPICS, "legacy", "Alpha", org=None)
    await w.link("r1", TOPICS, "a")
    for to in ("theirs", "legacy"):
        with pytest.raises(ValueError, match="not a node of org"):
            await w.p.move_taxonomy_edges(TOPICS, "a", to, w.org, set_merged_from="a")
    assert [t for t, _, _ in await w.edges("r1")] == ["a"]


async def test_legacy_nodes_are_paged_in_key_order_with_this_orgs_counts(w: Org) -> None:
    keys = [f"legacy{i}" for i in range(5)]
    for i, key in enumerate(keys):
        await w.node(TOPICS, key, f"Legacy {i}", org=None)
        if i != 2:
            await w.link(f"r{i}a", TOPICS, key)
            await w.link(f"r{i}b", TOPICS, key)
        await w.link(f"x{i}", TOPICS, key, org="other")
    seen, after = [], None
    while True:
        page = await w.p.find_legacy_taxonomy_nodes(TOPICS, w.org, 2, after_key=after)
        seen += [(r["_key"], r["records"]) for r in page]
        if len(page) < 2:
            break
        after = page[-1]["_key"]
    assert seen == [(k, 2) for i, k in enumerate(keys) if i != 2]


async def test_edges_linked_to_a_merged_node_are_found_and_swept(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "win", "Bug bash", createdAtTimestamp=1)
    await w.node(TOPICS, "lose", "Bug-bash", createdAtTimestamp=2)
    await w.link("r1", TOPICS, "lose")
    consolidator = _consolidator(w.p)
    await consolidator.merge(TOPICS, org, "win", "lose", dry_run=False)
    assert await w.p.find_merged_taxonomy_nodes_with_edges(TOPICS, org, 10) == []

    await w.link("r2", TOPICS, "lose", extractedName="bug-bash")
    await w.link("x1", TOPICS, "lose", org="other")        # another org's record is not ours to move
    rows = await w.p.find_merged_taxonomy_nodes_with_edges(TOPICS, org, 10)
    assert [(r["_key"], r["mergedInto"], r["records"]) for r in rows] == [("lose", "win", 1)]
    assert await w.p.find_merged_taxonomy_nodes_with_edges(TOPICS, org, 10, after_key="lose") == []

    (stray,) = await consolidator.stray_nodes(TOPICS, org)
    assert (await consolidator.sweep(TOPICS, org, stray.key, dry_run=False)).edges_moved == 1
    assert await w.edges("r2") == [("win", "bug-bash", "lose")]
    assert await w.edges("x1") == [("lose", None, None)]
    assert await consolidator.stray_nodes(TOPICS, org) == []


async def test_strays_on_a_chain_sweep_to_its_end_and_each_undo_restores_them(w: Org) -> None:
    org = w.org
    await w.node(TOPICS, "a", "bug-bash", createdAtTimestamp=3)
    await w.node(TOPICS, "b", "Bug bash", createdAtTimestamp=2)
    await w.node(TOPICS, "c", "Bugbash", createdAtTimestamp=1)
    await w.link("ra", TOPICS, "a")
    await w.link("rb", TOPICS, "b")
    consolidator = _consolidator(w.p)
    await consolidator.merge(TOPICS, org, "b", "a", dry_run=False)
    await consolidator.merge(TOPICS, org, "c", "b", dry_run=False)
    await w.link("la", TOPICS, "a")
    await w.link("lb", TOPICS, "b")

    assert [s.key for s in await consolidator.stray_nodes(TOPICS, org)] == ["a", "b"]
    for stray in await consolidator.stray_nodes(TOPICS, org):
        await consolidator.sweep(TOPICS, org, stray.key, dry_run=False)
    assert await w.edges("la") == [("c", None, "a")]
    assert await w.edges("lb") == [("c", None, "b")]

    await consolidator.unmerge(TOPICS, org, "a", dry_run=False)
    assert [t for t, _, _ in await w.edges("ra")] == ["a"]
    assert [t for t, _, _ in await w.edges("la")] == ["a"]
    await consolidator.unmerge(TOPICS, org, "b", dry_run=False)
    assert [t for t, _, _ in await w.edges("lb")] == ["b"]


# ---- consolidation: what the provider adds ---------------------------------------------------------------------------------------------

async def test_a_move_keeps_the_edges_fields_counts_trashed_records_and_refuses_bad_calls(w: Org) -> None:
    await w.node(CATS, "from", "A")
    await w.node(CATS, "to", "B")
    await w.link("r1", CATS, "from", extractedNames=["a", "aa"], confidence=0.9, mergedFrom=None)
    await w.link("trashed", CATS, "from")
    await w.p.batch_upsert_nodes([{"_key": "trashed", "isDeleted": True}], "records")

    assert await w.p.move_taxonomy_edges(CATS, "from", "to", w.org, set_merged_from="from", dry_run=True) == 2
    assert await w.p.move_taxonomy_edges(CATS, "from", "missing", w.org, set_merged_from="from", dry_run=True) == 2      # a dry run checks no target
    assert await w.p.move_taxonomy_edges(CATS, "from", "to", w.org, set_merged_from="from") == 2
    assert await w.edge_doc("r1", CATS, "to") == {"createdAtTimestamp": 1, "extractedNames": ["a", "aa"], "confidence": 0.9, "mergedFrom": "from"}
    assert await w.edge_doc("trashed", CATS, "to") is not None and await w.edge_doc("r1", CATS, "from") is None
    assert await w.p.move_taxonomy_edges(CATS, "from", "to", w.org, set_merged_from="from") == 0          # idempotent
    for args, match in (((TOPICS, "a", "a", w.org), "same node"), (("records", "a", "b", w.org), "not a taxonomy collection"),
                        ((TOPICS, "", "b", w.org), "both keys"), ((TOPICS, "a", "b", ""), "both keys")):
        with pytest.raises(ValueError, match=match):
            await w.p.move_taxonomy_edges(*args, set_merged_from=None)
    with pytest.raises(ValueError, match="provenance"):
        await w.p.move_taxonomy_edges(TOPICS, "a", "b", w.org, set_merged_from=None, provenance="name")


async def test_a_restore_moves_only_the_edges_of_its_own_provenance(w: Org) -> None:
    await w.node(TOPICS, "target", "T")
    await w.node(TOPICS, "a", "A")
    await w.node(TOPICS, "legacy", "L", org=None)
    await w.link("merged", TOPICS, "target", mergedFrom="a")
    await w.link("migrated", TOPICS, "target", migratedFrom="legacy", mergedFrom="a")
    await w.link("native", TOPICS, "target")

    assert await w.p.move_taxonomy_edges(TOPICS, "target", "legacy", w.org, set_merged_from=None, only_merged_from="legacy", provenance="migratedFrom") == 1
    assert await w.edge_doc("migrated", TOPICS, "legacy") == {"createdAtTimestamp": 1}        # back on its legacy node with no history left
    assert await w.p.move_taxonomy_edges(TOPICS, "target", "a", w.org, set_merged_from=None, only_merged_from="a") == 1
    assert await w.edge_doc("merged", TOPICS, "a") == {"createdAtTimestamp": 1}
    assert [t for t, _, _ in await w.edges("native")] == ["target"]


async def test_a_move_follows_the_transaction_and_leaves_the_other_org_alone(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.node(TOPICS, "l", "L")
    await w.node(TOPICS, "w", "W")
    await w.link("r1", TOPICS, "l")
    with in_org(orgs[1].org):
        await orgs[1].node(TOPICS, "l", "L")
        await orgs[1].node(TOPICS, "w", "W")
        await orgs[1].link("r1", TOPICS, "l")
    tid = await w.p.begin_transaction([], [])
    try:
        assert await w.p.move_taxonomy_edges(TOPICS, "l", "w", w.org, set_merged_from="l", transaction=tid) == 1
        assert await w.edges("r1") == [("l", None, None)]       # not committed
    finally:
        await w.p.rollback_transaction(tid)
    assert await w.edges("r1") == [("l", None, None)]
    assert await w.p.move_taxonomy_edges(TOPICS, "l", "w", w.org, set_merged_from="l") == 1
    with in_org(orgs[1].org):
        assert await orgs[1].edges("r1") == [("l", None, None)]
        assert await orgs[1].p.move_taxonomy_edges(TOPICS, "l", "w", w.org, set_merged_from="l", dry_run=True) == 0    # its record is not of this org


async def test_the_legacy_and_merged_pages_walk_keys_in_byte_order_and_refuse_a_non_taxonomy_collection(w: Org) -> None:
    keys = ["b-1", "B-2", "_3", "a.4"]               # byte order: B-2, _3, a.4, b-1 (a linguistic order would differ)
    for key in keys:
        await w.node(LANGS, key, key, org=None)
        await w.link(f"r-{key}", LANGS, key)
        await w.node(LANGS, f"m{key}", key, mergedInto="x")
        await w.link(f"r-m{key}", LANGS, f"m{key}")
    await w.link("r-again", LANGS, "b-1")
    walked, after = [], None
    while page := await w.p.find_legacy_taxonomy_nodes(LANGS, w.org, 1, after_key=after):
        walked += [(r["_key"], r["name"], r["records"]) for r in page]
        after = page[-1]["_key"]
    assert walked == [("B-2", "B-2", 1), ("_3", "_3", 1), ("a.4", "a.4", 1), ("b-1", "b-1", 2)]
    merged = await w.p.find_merged_taxonomy_nodes_with_edges(LANGS, w.org, 0)          # at least one
    assert [(r["_key"], r["name"], r["mergedInto"], r["records"]) for r in merged] == [("mB-2", "B-2", "x", 1)]
    assert [r["_key"] for r in await w.p.find_merged_taxonomy_nodes_with_edges(LANGS, w.org, 10, after_key="mB-2")] == ["m_3", "ma.4", "mb-1"]
    assert await w.p.find_legacy_taxonomy_nodes(LANGS, "", 10) == [] and await w.p.find_merged_taxonomy_nodes_with_edges(LANGS, "", 10) == []
    for find in (w.p.find_legacy_taxonomy_nodes, w.p.find_merged_taxonomy_nodes_with_edges):
        with pytest.raises(ValueError, match="not a taxonomy collection"):
            await find("departments", w.org, 10)


async def test_two_concurrent_moves_of_one_nodes_edges_move_each_edge_once(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pg_taxonomy, "_EDGE_MOVE_BATCH", 3)
    await w.node(TOPICS, "l", "L")
    await w.node(TOPICS, "w", "W")
    for i in range(10):
        await w.link(f"r{i}", TOPICS, "l", extractedName=f"x{i}")
    await w.link("r0", TOPICS, "w")

    moved = await asyncio.gather(*(w.p.move_taxonomy_edges(TOPICS, "l", "w", w.org, set_merged_from="l") for _ in range(2)))

    assert sum(moved) == 10
    for i in range(10):
        assert [t for t, _, _ in await w.edges(f"r{i}")] == ["w"]
    assert (await w.edge_doc("r5", TOPICS, "w"))["extractedName"] == "x5" and "extractedName" not in await w.edge_doc("r0", TOPICS, "w")


async def test_the_writers_pass_schema_validation_with_what_the_resolver_writes(w: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    for coll, key in ((CATS, "c"), (SUB1, "s1")):
        await w.p.create_taxonomy_node_if_absent(coll, {"id": key, "name": key, "normalizedName": key, "orgId": w.org, "createdAtTimestamp": 1})
    await w.p.ensure_taxonomy_hierarchy_edge(SUB1, "s1", "c")
    await w.p.add_taxonomy_aliases(SUB1, "s1", ["S one"], ["s one"], org_id=w.org)
    assert await w.hierarchy() == [(SUB1, "s1", CATS, "c")] and (await w.doc(SUB1, "s1"))["aliases"] == ["S one"]
