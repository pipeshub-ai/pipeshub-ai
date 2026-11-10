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
