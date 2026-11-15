"""The per-record lookups of the knowledge graph go through the vetted reads of graph_0006 (row-level security keeps a plain statement from their expression
indexes), answer what the plain statements answered, and read the org alone. Their SQL-level equivalence and plans are in backend/db/tests/test_graph.py.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.core.db.uow import Tx
from app.models.entities import Person

from .pg_support import OrgBound, StandInProvider, new_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


async def seed(p: OrgBound) -> None:
    await p.batch_upsert_nodes([{"_key": k, "orgId": p.org, "connectorId": "c1", "recordName": k} for k in ("r1", "r2", "R3")], "records")
    await p.batch_upsert_nodes([{"_key": "o1", "orgId": p.org, "name": "Acme", "normalizedName": "acme", "isExternal": True, "parentOrgId": p.org}], "organizations")
    await p.batch_upsert_nodes([{"_key": "p1", "orgId": p.org, "fullName": "Ann Lee", "email": "Ann@X.com", "sourceKey": "c1:u1"},
                                {"_key": "p2", "orgId": p.org, "fullName": "Bob Stone", "email": "bob@x.com"}], "person")
    await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "email": "ann@x.com"}], "groups")
    await p.batch_upsert_nodes([{"_key": "u1", "orgId": p.org, "userId": "x1", "email": "ann@x.com", "isActive": True}], "users")


@pytest.fixture
async def world(pg_db) -> AsyncIterator[tuple[OrgBound, OrgBound]]:
    a, b = OrgBound(StandInProvider(logging.getLogger("kgl"), pg_db), new_org()), OrgBound(StandInProvider(logging.getLogger("kgl"), pg_db), new_org())
    for p in (a, b):          # the same ids and keys in both orgs
        await seed(p)
    yield a, b
    from app.core.org_context import OrgContext, bind
    for p in (a, b):
        with bind(OrgContext(p.org, None, "job", "job:test", "t")):
            await pg_db.run(lambda tx, o=p.org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


def spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    for name in ("fetch", "fetchrow"):
        real = getattr(Tx, name)

        async def wrap(self, sql, *args, _real=real) -> object:
            if "pipeshub:graph." in sql and (label := sql.split("pipeshub:graph.")[1].split(" ")[0]) not in ("upsert_person_source_lock", "lower"):      # not the lookups
                sent.append(label)
            return await _real(self, sql, *args)
        monkeypatch.setattr(Tx, name, wrap)
    return sent


async def test_each_lookup_reads_its_vetted_function_and_a_value_out_of_bounds_the_plain_statement(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, _ = world
    sent = spy(monkeypatch)
    await a.page_records_for_vector_membership_backfill("c1", None, 10)
    await a.find_organizations(a.org, ["acme"])
    await a.get_person_by_source_key("c1:u1", a.org)
    await a.upsert_person_by_source_key(Person(source_key="c1:u1", org_id=a.org))
    await a.get_entity_id_by_email("ann@x.com")
    await a.bulk_get_entity_ids_by_email(["ann@x.com"])
    await a.get_user_by_email("ann@x.com")
    await a.get_users_by_emails(["ann@x.com"])
    await a.find_people(a.org, "ann@x.com")
    await a.find_people(a.org, "ann")
    assert sent == ["records_page_by_connector", "organizations_by_name", "person_by_source_key", "person_by_source_key", "principals_by_email", "principals_by_email",
                    "principals_by_email", "principals_by_email", "principals_by_email", "people_by_name"]
    sent.clear()
    await a.page_records_for_vector_membership_backfill("c" * 300, None, 10)
    await a.page_records_for_vector_membership_backfill("c1", None, 5000)
    await a.get_person_by_source_key("k" * 500, a.org)
    await a.get_user_by_email("e" * 2000)
    await a.find_people(a.org, "n" * 500)
    assert sent == ["page_records_for_vector_membership_backfill", "page_records_for_vector_membership_backfill", "get_person_by_source_key", "get_user_by_email", "find_people"]


async def test_the_entity_index_pages_a_connectors_records_by_the_vetted_page_in_byte_order_for_the_org_alone(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = world
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "connectorId": "c-a", "recordName": "x"}], "records")
    sent = spy(monkeypatch)
    assert [r["_key"] for r in await a.page_entity_index_source("records", "c1", None, 10)] == ["R3", "r1", "r2"]
    assert [r["_key"] for r in await a.page_entity_index_source("records", "c1", "R3", 1)] == ["r1"]
    assert [r["_key"] for r in await b.page_entity_index_source("records", "c-a", None, 10)] == [] and [r["_key"] for r in await a.page_entity_index_source("records", "c-a", None, 10)] == ["only-a"]
    assert sent == ["records_page_by_connector_for_index"] * 4
    sent.clear()
    assert [r["_key"] for r in await a.page_entity_index_source("records", "c1", "R3", 5000)] == ["r1", "r2"]          # a page over the function's bound: the statement, in the same order
    assert [r["_key"] for r in await a.page_entity_index_source("organizations", a.org, None, 10)] == ["o1"]
    assert sent == ["page_entity_index_source_c", "page_entity_index_source"]


async def test_the_stage_sweeps_read_stale_states_and_unheld_revisions_by_the_vetted_functions_for_the_org_alone(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = world
    for p in (a, b):
        await p.batch_upsert_nodes([{"_key": "s1", "orgId": p.org, "virtualRecordId": "v1", "rev": "1", "stage": "x", "status": "PENDING", "updatedAtMs": 100},
                                    {"_key": "s2", "orgId": p.org, "virtualRecordId": "v1", "rev": "2", "stage": "x", "status": "RUNNING", "updatedAtMs": 50},
                                    {"_key": "s3", "orgId": p.org, "virtualRecordId": "v2", "rev": "1", "stage": "x", "status": "DONE", "updatedAtMs": 10}], "stageStates")
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": p.org, "connectorId": "c1", "virtualRecordId": "v1", "contentRev": "2"}], "records")
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "virtualRecordId": "v3", "rev": "1", "stage": "x", "status": "PENDING", "updatedAtMs": 5}], "stageStates")
    sent = spy(monkeypatch)
    assert [d["id"] for d in await a.stage_states_stale(["PENDING", "RUNNING"], 1000, 10)] == ["only-a", "s2", "s1"]
    assert [d["id"] for d in await b.stage_states_stale(["PENDING", "RUNNING"], 1000, 10)] == ["s2", "s1"]
    assert await a.stage_states_unheld_revisions(["v1", "v2", "v3"], updated_before_ms=1000) == [("v1", "1"), ("v2", "1"), ("v3", "1")]
    assert await b.stage_states_unheld_revisions(["v1", "v3"], updated_before_ms=1000) == [("v1", "1")]
    assert sent == ["stage_states_stale_vetted"] * 2 + ["stage_states_unheld_vetted"] * 2
    sent.clear()
    assert [d["id"] for d in await a.stage_states_stale(["PENDING"] * 21, 1000, 10)] == ["only-a", "s1"]          # a status repeated past the function's item bound
    assert sent == ["stage_states_stale_vetted"]
    sent.clear()
    assert len(await a.stage_states_unheld_revisions(["v1", *(f"x{i}" for i in range(1500))], updated_before_ms=1000)) == 1          # a list over the function's bound is read in chunks
    assert sent == ["stage_states_unheld_vetted"] * 2
    sent.clear()
    await a.stage_states_stale(["s" * 200], 1000, 10)
    await a.stage_states_stale(["PENDING"], 1000, 5000)
    assert await a.stage_states_unheld_revisions(["v" * 300, "v3"], updated_before_ms=1000) == [("v3", "1")]
    assert sent == ["stage_states_stale", "stage_states_stale", "stage_states_unheld_revisions"]


async def test_a_full_page_of_matches_is_read_by_the_plain_statement(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, _ = world
    monkeypatch.setattr("app.services.graph_db.postgres.identity._PRINCIPALS_PAGE", 0)      # a page can never be short
    sent = spy(monkeypatch)
    assert await a.get_entity_id_by_email("ANN@x.com") == "u1"          # the user, before the group and the person of the address
    assert sent == ["principals_by_email", "get_entity_id_by_email"]


async def test_each_lookup_answers_for_the_org_alone(world, pg_db) -> None:
    a, b = world
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "fullName": "Only Aaron", "email": "only@a.com", "sourceKey": "c1:only"}], "person")
    await a.batch_upsert_nodes([{"_key": "only-a-rec", "orgId": a.org, "connectorId": "c-a"}], "records")
    assert [r["_key"] for r in await b.page_records_for_vector_membership_backfill("c-a", None, 10)] == []
    assert [r["_key"] for r in await a.page_records_for_vector_membership_backfill("c-a", None, 10)] == ["only-a-rec"]
    assert [r["_key"] for r in await a.page_records_for_vector_membership_backfill("c1", None, 10)] == ["R3", "r1", "r2"]          # byte order
    assert await b.get_person_by_source_key("c1:only", b.org) is None and (await a.get_person_by_source_key("c1:only", a.org)).id == "only-a"
    assert [p["id"] for p in await b.find_people(b.org, "only aaron")] == [] and [p["id"] for p in await a.find_people(a.org, "aar")] == ["only-a"]
    assert await b.get_entity_id_by_email("only@a.com") is None and await a.get_entity_id_by_email("only@a.com") == "only-a"
    assert await b.bulk_get_entity_ids_by_email(["only@a.com"]) == {} and await a.bulk_get_entity_ids_by_email(["only@a.com"]) == {"only@a.com": ("only-a", "person", "USER")}
    assert [o["id"] for o in await a.find_organizations(a.org, ["acme"])] == ["o1"] and await a.find_organizations(b.org, ["acme"]) == []
    # each org sees its own rows with the ids and keys they share
    assert (await a.get_user_by_email("ANN@x.com")).id == "u1" and (await b.get_user_by_email("ANN@x.com")).id == "u1"
    assert [p["id"] for p in await b.find_people(b.org, "ann lee")] == ["p1"]


async def seed_entities(p: OrgBound) -> None:
    await p.batch_upsert_nodes([{"_key": "ne1", "orgId": p.org, "kind": "person", "normKey": "anna lee", "normalizedAliases": ["annie"], "name": "Anna Lee"},
                                {"_key": "ne2", "orgId": p.org, "kind": "person", "normKey": "anna lee smith", "name": "Anna Lee Smith"}], "namedEntities")
    await p.batch_upsert_nodes([{"_key": "r1:v1", "orgId": p.org, "recordId": "r1", "kind": "date", "name": "May", "startMs": 5, "endMs": 9}], "valueMentions")
    await p.batch_upsert_nodes([{"_key": "s1", "orgId": p.org, "virtualRecordId": "v1", "rev": "1", "stage": "x", "status": "DONE"}], "stageStates")


async def test_each_entity_lookup_reads_its_vetted_function_for_the_org_alone(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = world
    for p in (a, b):
        await seed_entities(p)
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "kind": "person", "normKey": "anna lee a", "normalizedAliases": ["annie a"]}], "namedEntities")
    sent = spy(monkeypatch)
    assert [d["id"] for d in await a.find_named_entities(a.org, "person", ["annie", "anna lee smith"])] == ["ne1", "ne2"]
    assert [r["id"] for r in await a.find_named_entities_by_alias(a.org, [("person", "annie a")])] == ["only-a"]
    assert [r["id"] for r in await a.find_named_entities_by_key_prefix(a.org, [("person", "anna lee")])] == ["only-a", "ne2"]
    assert [d.get("id") for d in await a.get_named_entities_for_record("r1")][-1] == "r1:v1"
    assert await a.get_named_entity_date_spans(a.org, ["r1"], 0, 20) == {"r1": (True, 4)}
    assert [d["id"] for d in await a.stage_states_for_revision("v1", "1")] == ["s1"]
    assert sent == ["named_entities_by_keys", "named_entities_by_alias", "named_entities_by_prefix", "get_named_entities_for_record", "value_mentions_by_records",
                    "get_named_entity_date_spans_vetted", "stage_states_by_revision"]
    assert await b.find_named_entities(b.org, "person", ["annie a"]) == [] and await b.find_named_entities_by_alias(b.org, [("person", "annie a")]) == []
    assert await b.find_named_entities(a.org, "person", ["annie"]) == []          # an org argument other than the ambient one reads nothing
    sent.clear()
    await a.find_named_entities(a.org, "person", ["k" * 2000])
    await a.stage_states_for_revision("v" * 300, "1")
    assert sent == ["find_named_entities", "stage_states_for_revision"]


async def seed_external(p: OrgBound) -> None:
    await p.batch_upsert_nodes([{"_key": "ug1", "orgId": p.org, "connectorId": "c1", "connectorName": "DRIVE", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "externalGroupId": "e1", "name": "Group", "email": "g@x.com"}], "groups")
    await p.batch_upsert_nodes([{"_key": "rg1", "orgId": p.org, "connectorId": "c1", "connectorName": "DRIVE", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "externalGroupId": "e1", "groupName": "Record group"}], "recordGroups")
    await p.batch_upsert_nodes([{"_key": "ro1", "orgId": p.org, "connectorId": "c1", "connectorName": "DRIVE", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "externalRoleId": "e1", "name": "Role"}], "roles")
    await p.batch_upsert_nodes([{"_key": "s1", "orgId": p.org, "connectorId": "c1", "stage": "indexing", "status": "PENDING", "virtualRecordId": "v1", "rev": "1"},
                                {"_key": "s2", "orgId": p.org, "connectorId": "c1", "stage": "indexing", "status": "PENDING", "virtualRecordId": "v2", "rev": "1"}], "stageStates")
    await p.batch_upsert_nodes([{"_key": "r1", "orgId": p.org, "connectorId": "c1", "virtualRecordId": "v1", "contentRev": "1", "indexingStatus": "NOT_STARTED"},
                                {"_key": "r2", "orgId": p.org, "connectorId": "c1", "virtualRecordId": "v1", "contentRev": "1", "isDeleted": True},
                                {"_key": "r3", "orgId": p.org, "connectorId": "c2", "virtualRecordId": "v1", "contentRev": "1"}], "records")


async def test_groups_roles_and_the_stage_sweep_read_their_vetted_functions_for_the_org_alone(world, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = world
    for p in (a, b):
        await seed_external(p)
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "connectorId": "c1", "connectorName": "DRIVE", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "externalGroupId": "only-a", "name": "n"}], "groups")
    sent = spy(monkeypatch)
    assert (await a.get_user_group_by_external_id("c1", "e1")).id == "ug1"
    assert (await a.get_record_group_by_external_id("c1", "e1")).id == "rg1"
    assert (await a.get_app_role_by_external_id("c1", "e1")).id == "ro1"
    assert await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10) == [("s1", ["r1"]), ("s2", [])]
    assert await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10, after_key="s1", exclude_indexing_statuses=["NOT_STARTED"]) == [("s2", [])]
    assert sent == ["node_by_external", "node_by_external", "node_by_external", "get_stage_states_with_records_vetted", "get_stage_states_with_records_vetted"]
    assert await b.get_user_group_by_external_id("c1", "only-a") is None and (await a.get_user_group_by_external_id("c1", "only-a")).id == "only-a"
    assert await b.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10) == []          # an org argument other than the ambient one reads nothing
    sent.clear()
    await a.get_user_group_by_external_id("c" * 300, "e1")
    await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=5000)
    assert sent == ["node_by_external_plain", "get_stage_states_with_records"]


async def test_value_mentions_are_read_whole_for_a_chunk_and_a_record_over_a_hundred_thousand(world, pg_db) -> None:
    """A date-span chunk of 5,000 records holding 102,500 mentions, and one record of 100,001: the lookups answer as the statements did, none fails or loses rows."""
    from app.core.org_context import OrgContext, bind
    a, _ = world

    async def fill(tx: Tx) -> None:
        await tx.execute("""INSERT INTO graph.nodes SELECT $1, 'valueMentions', 'm' || r || '_' || i, jsonb_build_object('orgId', $1::text, 'recordId', 'rec' || r, 'kind', 'date',
                'startMs', 5, 'endMs', 9, 'name', 'n' || i) FROM generate_series(1, 4100) r, generate_series(1, 25) i""", a.org)

    async def fill_one(tx: Tx) -> None:
        await tx.execute("""INSERT INTO graph.nodes SELECT $1, 'valueMentions', 'h' || i, jsonb_build_object('orgId', $1::text, 'recordId', 'huge', 'kind', 'date',
                'startMs', 5, 'endMs', 9, 'name', 'n' || i) FROM generate_series(1, 100001) i""", a.org)
    with bind(OrgContext(a.org, None, "job", "job:test", "t")):
        await pg_db.run(fill, write=True)
        await pg_db.run(fill_one, write=True)
    spans = await a.get_named_entity_date_spans(a.org, [f"rec{r}" for r in range(1, 5001)], 0, 20)
    assert len(spans) == 4100 and set(spans.values()) == {(True, 4)}
    assert len(await a.get_named_entities_for_record("huge")) == 100001
    await a.replace_named_entity_values("huge", [])
    assert await a.get_named_entities_for_record("huge") == []
    assert len(await a.get_named_entities_for_record("rec7")) == 25


async def test_a_result_at_the_bound_of_a_vetted_read_is_read_by_the_plain_statement(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read that returns as many rows as its function's bound may be partial: the same call is repeated with the plain statement and answers the same."""
    a, _ = world
    await seed_entities(a)
    await seed_external(a)
    expected = {
        "keys": [d["id"] for d in await a.find_named_entities(a.org, "person", ["annie", "anna lee smith"])],
        "alias": [r["id"] for r in await a.find_named_entities_by_alias(a.org, [("person", "annie")])],
        "prefix": [r["id"] for r in await a.find_named_entities_by_key_prefix(a.org, [("person", "anna lee")])],
        "spans": await a.get_named_entity_date_spans(a.org, ["r1"], 0, 20),
        "entity": [d["id"] for d in await a.get_named_entities_for_record("r1")],
        "stages": [d["id"] for d in await a.stage_states_for_revision("v1", "1")],
        "sweep": await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10),
    }
    for name in ("entity_reads.VETTED_BOUND", "entity_reads.MENTIONS_BOUND", "entities.MENTIONS_BOUND", "codegraph.VETTED_BOUND"):
        monkeypatch.setattr(f"app.services.graph_db.postgres.{name}", 1)
    sent = spy(monkeypatch)
    assert [d["id"] for d in await a.find_named_entities(a.org, "person", ["annie", "anna lee smith"])] == expected["keys"]
    assert [r["id"] for r in await a.find_named_entities_by_alias(a.org, [("person", "annie")])] == expected["alias"]
    assert [r["id"] for r in await a.find_named_entities_by_key_prefix(a.org, [("person", "anna lee")])] == expected["prefix"]
    assert await a.get_named_entity_date_spans(a.org, ["r1"], 0, 20) == expected["spans"]
    assert [d["id"] for d in await a.get_named_entities_for_record("r1")] == expected["entity"]
    assert [d["id"] for d in await a.stage_states_for_revision("v1", "1")] == expected["stages"]
    assert await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10) == expected["sweep"]
    assert sent == ["named_entities_by_keys", "find_named_entities", "named_entities_by_alias", "find_named_entities_by_alias", "named_entities_by_prefix", "find_named_entities_by_key_prefix",
                    "get_named_entity_date_spans_vetted", "get_named_entity_date_spans", "get_named_entities_for_record", "value_mentions_by_records", "get_named_entities_for_record_values",
                    "stage_states_by_revision", "stage_states_for_revision", "get_stage_states_with_records_vetted", "get_stage_states_with_records"]


async def test_a_stage_state_with_an_overlong_revision_is_read_by_the_plain_statement_with_its_records(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """The vetted function does not take the revision, so it could not find the state's records; the page is read plainly instead of losing them."""
    a, _ = world
    await a.batch_upsert_nodes([{"_key": "long", "orgId": a.org, "connectorId": "c1", "stage": "indexing", "status": "PENDING", "virtualRecordId": "v1", "rev": "r" * 300}], "stageStates")
    await a.batch_upsert_nodes([{"_key": "rl", "orgId": a.org, "connectorId": "c1", "recordName": "rl", "virtualRecordId": "v1", "contentRev": "r" * 300}], "records")
    sent = spy(monkeypatch)
    assert await a.get_stage_states_with_records(a.org, "c1", "indexing", "PENDING", limit=10) == [("long", ["rl"])]
    assert sent == ["get_stage_states_with_records_vetted", "get_stage_states_with_records"]
