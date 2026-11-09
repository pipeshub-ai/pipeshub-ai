"""The people and organisations records name, on PostgreSQL, against a real database: persons keyed by a source's user id, a person promoted to a user, a principal
found by email, the ``entityRelations`` edges of a record, and external organisations (CRM accounts and extracted ones).

The answers are those of ``Neo4jProvider`` (the cases of ``test_record_people_real_backends``, ``test_record_organizations_real_backends`` and
``test_extracted_organizations_real_backends`` are mirrored here): an entity relation is one edge per pair and type, an external organisation is one of the org
by ``isExternal`` and ``parentOrgId``, ids come back in Neo4j's string order. Nothing of another org is read or changed.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets

import pytest

from app.config.constants.arangodb import PersonMigrationMode
from app.core import org_context
from app.core.org_context import MissingOrgContext, OrgMismatch, SystemContextNotAllowed
from app.models.entities import Person

from .kg_fresh import differences
from .test_access import APP, World, _org, rec
from .test_access import w as w
from .test_grants import failing, in_org
from .test_kg_derivation import ctx, held, prov
from .test_kg_projection_races import world2

pytestmark = pytest.mark.integration

MIGRATED, SPLIT = PersonMigrationMode.MIGRATED, PersonMigrationMode.SPLIT


def keyed(w: World, source_key: str | None, **kw: object) -> Person:
    return Person(source_key=source_key, org_id=w.org, **kw)


async def person(w: World, key: str, email: str, **doc: object) -> None:
    await w.nodes("person", {"_key": key, "email": email, **doc})


async def relate(w: World, f: tuple[str, str], t: tuple[str, str], edge_type: str, **doc: object) -> None:
    await w.p.batch_create_entity_relations([{"_from": f"{f[0]}/{f[1]}", "_to": f"{t[0]}/{t[1]}", "edgeType": edge_type, "createdAtTimestamp": 1, **doc}])


async def edges(w: World, coll: str, *, frm: tuple[str, str] | None = None, to: tuple[str, str] | None = None) -> list[tuple]:
    """(from, to, ekey, doc without timestamps) of the edges of ``coll`` from or to a node, in a fixed order."""
    async def go(tx) -> list:
        return await tx.fetch("""SELECT from_coll, from_id, to_coll, to_id, ekey, doc FROM graph.edges WHERE org_id = $1 AND coll = $2
            AND ($3::text IS NULL OR (from_coll = $3 AND from_id = $4)) AND ($5::text IS NULL OR (to_coll = $5 AND to_id = $6)) ORDER BY 1, 2, 3, 4, 5""",
                              w.org, coll, frm and frm[0], frm and frm[1], to and to[0], to and to[1])
    rows = await w.p._q(go, None)
    return [(f"{r['from_coll']}/{r['from_id']}", f"{r['to_coll']}/{r['to_id']}", r["ekey"],
             {k: v for k, v in json.loads(r["doc"]).items() if k not in ("createdAtTimestamp", "updatedAtTimestamp")}) for r in rows]


async def touching(w: World, coll: str, key: str) -> int:
    return await w.p._q(lambda tx: tx.fetchval("SELECT count(*) FROM graph.edges WHERE org_id = $1 AND ((from_coll = $2 AND from_id = $3) OR (to_coll = $2 AND to_id = $3))",
                                               w.org, coll, key), None)


# ---- persons keyed by a source's user id -----------------------------------------------------------------------------------------------------

async def test_source_keyed_people_coexist_and_upsert_to_one_node_that_is_never_changed(w: World) -> None:
    p = w.p
    first = keyed(w, "conn-1:acc-1", full_name="Ann", phone="1")
    assert await p.upsert_person_by_source_key(first, raise_on_error=True) == first.id
    second = keyed(w, "conn-1:acc-2", full_name="Bob")
    assert await p.upsert_person_by_source_key(second, raise_on_error=True) == second.id != first.id
    assert await p.upsert_person_by_source_key(keyed(w, "conn-1:acc-1", full_name="Renamed"), raise_on_error=True) == first.id     # the survivor's id
    got = await p.get_person_by_source_key("conn-1:acc-1", w.org)
    assert (got.id, got.full_name, got.phone, got.source_key, got.email, got.org_id) == (first.id, "Ann", "1", "conn-1:acc-1", None, w.org)
    assert await p.get_person_by_source_key("conn-1:acc-9", w.org) is None
    assert await p.get_person_by_source_key("conn-1:acc-1", _org()) is None                         # another org's argument reads nothing
    await p.upsert_person_by_email(Person(email="eve@x.io", org_id=w.org))
    assert await p.get_person_by_source_key("", w.org) is None                                       # an email person has no source key


async def test_racing_creators_of_one_source_key_converge_on_one_node(w: World) -> None:
    ids = await asyncio.gather(*(w.p.upsert_person_by_source_key(keyed(w, "jira:race"), raise_on_error=True) for _ in range(8)))
    assert len(set(ids)) == 1 and ids[0] is not None
    assert len(await w.p.get_nodes_by_filters("person", {"sourceKey": "jira:race"})) == 1


async def test_a_source_keyed_person_of_another_org_is_refused_and_each_org_has_its_own(w: World) -> None:
    with pytest.raises(OrgMismatch):
        await w.p.upsert_person_by_source_key(Person(source_key="jira:x", org_id=_org()))
    mine = keyed(w, "jira:same")
    await w.p.upsert_person_by_source_key(mine)
    other = _org()
    with in_org(other):
        theirs = Person(source_key="jira:same", org_id=other)
        assert await w.p.upsert_person_by_source_key(theirs) == theirs.id != mine.id
        assert (await w.p.get_person_by_source_key("jira:same", other)).id == theirs.id
    assert (await w.p.get_person_by_source_key("jira:same", w.org)).id == mine.id


async def test_a_failed_source_key_read_or_write_answers_none_unless_asked_to_raise(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    kept = keyed(w, "jira:back")
    await w.p.upsert_person_by_source_key(kept)
    assert await w.p.upsert_person_by_source_key(keyed(w, None)) is None                             # no key to upsert on
    with pytest.raises(ValueError, match="source key"):
        await w.p.upsert_person_by_source_key(keyed(w, None), raise_on_error=True)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_person_by_source_key("jira:back", w.org) is None
    assert await w.p.upsert_person_by_source_key(keyed(w, "jira:new")) is None
    with pytest.raises(RuntimeError):
        await w.p.get_person_by_source_key("jira:back", w.org, raise_on_error=True)
    with pytest.raises(RuntimeError):
        await w.p.upsert_person_by_source_key(keyed(w, "jira:new"), raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_person_by_source_key("jira:back", w.org)).id == kept.id
    assert await w.p.get_person_by_source_key("jira:new", w.org) is None


async def test_a_source_keyed_upsert_in_a_transaction_is_seen_there_and_goes_with_its_rollback(w: World) -> None:
    tx = await w.p.begin_transaction([], [])
    made = keyed(w, "jira:tx")
    assert await w.p.upsert_person_by_source_key(made, tx) == made.id
    assert (await w.p.get_person_by_source_key("jira:tx", w.org, tx)).id == made.id
    assert await w.p.get_person_by_source_key("jira:tx", w.org) is None                             # not committed
    await w.p.rollback_transaction(tx)
    assert await w.p.get_person_by_source_key("jira:tx", w.org) is None


async def test_no_org_is_an_error_for_a_source_keyed_person(pg_provider) -> None:
    with pytest.raises(MissingOrgContext):
        await pg_provider.upsert_person_by_source_key(Person(source_key="jira:x", org_id="o"))
    with pytest.raises(MissingOrgContext):
        await pg_provider.get_person_by_source_key("jira:x", "o")


# ---- a person promoted to a user ---------------------------------------------------------------------------------------------------------------

async def promotion_world(w: World) -> None:
    """Eve, a person, holds two grants (the user already has one of the nodes), a membership of the app, and records naming her two ways; the user already
    authored one of them."""
    await person(w, "eve", "eve@x.io", fullName="Eve")
    await w.nodes("users", {"_key": "ueve", "email": "eve@x.io", "fullName": "Eve"})
    await w.nodes("records", rec("r1"), rec("r2"), rec("doc"), rec("both"))
    await w.edge("permission", ("person", "eve"), ("records", "r1"), type="USER", role="WRITER")
    await w.edge("permission", ("person", "eve"), ("records", "r2"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "ueve"), ("records", "r2"), type="USER", role="READER")       # the user's own grant stays
    await w.edge("userAppRelation", ("person", "eve"), ("apps", APP), isExternalUser=True, sourceUserId="src-eve")
    await relate(w, ("records", "doc"), ("person", "eve"), "AUTHORED_BY", origin="INFERRED")
    await relate(w, ("records", "doc"), ("person", "eve"), "OWNED_BY")
    await relate(w, ("records", "both"), ("person", "eve"), "AUTHORED_BY", note="person's")
    await relate(w, ("records", "both"), ("users", "ueve"), "AUTHORED_BY", note="user's")
    await relate(w, ("person", "eve"), ("records", "r1"), "MENTIONED_IN")                            # from the person: not moved, gone with it


async def test_a_person_without_a_crm_edge_moves_onto_the_user_and_goes(w: World) -> None:
    await promotion_world(w)
    assert await w.p.migrate_person_to_user("Eve@X.io", "ueve", w.org) == MIGRATED                  # the address is matched lowercased
    assert await w.p.get_document("eve", "person") is None and await touching(w, "person", "eve") == 0
    grants = {e[1]: (e[2], e[3]["role"]) for e in await edges(w, "permission", frm=("users", "ueve"))}
    assert grants == {"records/r1": ("USER", "WRITER"), "records/r2": ("USER", "READER")}
    assert [(e[1], e[3]) for e in await edges(w, "userAppRelation", frm=("users", "ueve"))] == [(f"apps/{APP}", {"isExternalUser": True, "sourceUserId": "src-eve"})]
    assert [(e[0], e[2], e[3].get("note")) for e in await edges(w, "entityRelations", to=("users", "ueve"))] == [
        ("records/both", "AUTHORED_BY", "user's"), ("records/doc", "AUTHORED_BY", None), ("records/doc", "OWNED_BY", None)]
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) is None                     # a second run finds nobody


@pytest.mark.parametrize(("coll", "outbound"), [("memberOf", True), ("lead", False), ("contact", False), ("memberOf", False), ("contact", True)])
async def test_a_person_with_a_crm_edge_either_way_splits_keeping_it_and_a_second_run_moves_nothing(w: World, coll: str, outbound: bool) -> None:
    await promotion_world(w)
    await w.nodes("organizations", {"_key": "acme", "name": "Acme", "isExternal": True, "parentOrgId": w.org})
    await w.edge(coll, ("person", "eve"), ("organizations", "acme")) if outbound else await w.edge(coll, ("organizations", "acme"), ("person", "eve"))
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) == SPLIT
    assert (await w.p.get_document("eve", "person"))["fullName"] == "Eve"
    left = {c: await edges(w, c, frm=("person", "eve")) + await edges(w, c, to=("person", "eve")) for c in ("permission", "userAppRelation", "entityRelations", coll)}
    assert left["permission"] == left["userAppRelation"] == [] and len(left[coll]) == 1
    assert [e[1] for e in left["entityRelations"]] == ["records/r1"]                                   # what the person names stays with it
    after = [await edges(w, c, frm=("users", "ueve")) + await edges(w, c, to=("users", "ueve")) for c in ("permission", "userAppRelation", "entityRelations")]
    assert len(after[0]) == 2 and len(after[1]) == 1 and len(after[2]) == 3
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) == SPLIT
    assert [await edges(w, c, frm=("users", "ueve")) + await edges(w, c, to=("users", "ueve")) for c in ("permission", "userAppRelation", "entityRelations")] == after


async def test_every_person_with_the_address_moves_and_a_crm_one_stays(w: World) -> None:
    await w.nodes("users", {"_key": "ueve", "email": "eve@x.io", "fullName": "Eve"})
    await w.nodes("records", rec("r1"), rec("r2"))
    await w.nodes("organizations", {"_key": "acme", "name": "Acme", "isExternal": True, "parentOrgId": w.org})
    await person(w, "a-contact", "eve@x.io", fullName="Eve")                                        # sorts first, so a lookup of one person would stop here
    await w.edge("memberOf", ("person", "a-contact"), ("organizations", "acme"))
    await w.edge("permission", ("person", "a-contact"), ("records", "r1"), type="USER", role="READER")
    await person(w, "b-share", "eve@x.io", fullName="Eve")
    await w.edge("permission", ("person", "b-share"), ("records", "r1"), type="USER", role="OWNER")
    await w.edge("permission", ("person", "b-share"), ("records", "r2"), type="USER", role="WRITER")
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) == SPLIT
    grants = {e[1]: e[3]["role"] for e in await edges(w, "permission", frm=("users", "ueve"))}
    assert grants == {"records/r1": "READER", "records/r2": "WRITER"}                               # a node's first grant, as for one person
    assert await w.p.get_document("b-share", "person") is None and await touching(w, "person", "b-share") == 0
    assert await edges(w, "permission", frm=("person", "a-contact")) == [] and len(await edges(w, "memberOf", frm=("person", "a-contact"))) == 1


async def test_no_person_no_user_or_another_org_moves_nothing(w: World) -> None:
    await promotion_world(w)
    assert await w.p.migrate_person_to_user("nobody@x.io", "ueve", w.org) is None
    assert await w.p.migrate_person_to_user("eve@x.io", "no-such-user", w.org) is None              # Neo4j matches the user too
    assert await touching(w, "person", "eve") == 7
    with pytest.raises(OrgMismatch):
        await w.p.migrate_person_to_user("eve@x.io", "ueve", _org())
    other = _org()
    with in_org(other):
        theirs = Person(email="eve@x.io", org_id=other)
        await w.p.upsert_person_by_email(theirs)
        assert await w.p.migrate_person_to_user("eve@x.io", "ueve", other) is None                  # this org's user is not that org's
        assert (await w.p.get_person_by_email("eve@x.io", other)).id == theirs.id
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) == MIGRATED


async def test_a_promotion_is_one_unit_a_failure_leaves_everything_and_a_transaction_is_honoured(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await promotion_world(w)

    class Boom(logging.Logger):
        def info(self, *a: object, **k: object) -> None:
            raise RuntimeError("after the last statement")
    real = w.p.logger
    monkeypatch.setattr(w.p, "logger", Boom("boom"))
    with pytest.raises(RuntimeError, match="after the last statement"):
        await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org)
    monkeypatch.setattr(w.p, "logger", real)
    assert await w.p.get_document("eve", "person") is not None and await touching(w, "person", "eve") == 7      # all of it rolled back

    tx = await w.p.begin_transaction([], [])
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org, tx) == MIGRATED
    await w.p.rollback_transaction(tx)
    assert await touching(w, "person", "eve") == 7
    tx = await w.p.begin_transaction([], [])
    assert await w.p.migrate_person_to_user("eve@x.io", "ueve", w.org, tx) == MIGRATED
    await w.p.commit_transaction(tx)
    assert await w.p.get_document("eve", "person") is None


async def test_two_promotions_of_one_person_at_once_move_it_once(w: World) -> None:
    await promotion_world(w)
    got = await asyncio.gather(*(w.p.migrate_person_to_user("eve@x.io", "ueve", w.org) for _ in range(4)))
    assert sorted(got, key=str) == [None, None, None, MIGRATED]
    assert len(await edges(w, "permission", frm=("users", "ueve"))) == 2 and len(await edges(w, "entityRelations", to=("users", "ueve"))) == 3


async def test_a_new_app_user_takes_over_the_person_with_their_address(w: World) -> None:
    """The sync of an app's users adopts a person an external share made, now that the promotion is there."""
    from app.config.constants.arangodb import Connectors
    from app.models.entities import AppUser
    await w.nodes("organizations", {"_key": w.org, "isActive": True, "accountType": "enterprise"})
    await person(w, "pat", "pat@x.io")
    await w.nodes("records", rec("shared"))
    await w.edge("permission", ("person", "pat"), ("records", "shared"), type="USER", role="READER")
    await w.p.batch_upsert_app_users([AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id=APP, id="upat", source_user_id="s-pat", email="pat@x.io", full_name="Pat")])
    assert await w.p.get_document("pat", "person") is None
    assert [e[1] for e in await edges(w, "permission", frm=("users", "upat"))] == ["records/shared"]


async def test_a_promotion_projects_the_grants_it_moves(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"PG_KG_PROJECT": "1", "PG_KG_FLUSH": "inline", "PG_KG_STRICT": "1", "PG_KG_GUARD": "strict"}.items():
        monkeypatch.setenv(k, v)
    p, org = prov(pg_db), _org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "pat", "email": "pat@x.io", "orgId": org}], "person")
        await p.batch_upsert_nodes([{"_key": "newbie", "orgId": org, "email": "pat@x.io"}], "users")
        await p.batch_create_edges([{"from_id": "pat", "from_collection": "person", "to_id": "f2", "to_collection": "records", "type": "USER", "role": "WRITER"}], "permission")
        await p.batch_create_edges([{"from_id": "pat", "from_collection": "person", "to_id": "app2", "to_collection": "apps", "isExternalUser": True}], "userAppRelation")
    assert not await differences(p, org)
    with ctx(org):
        assert await p.migrate_person_to_user("pat@x.io", "newbie", org) == MIGRATED
    grants = {k: v for k, v in (await held(p, org))["grants"].items() if k[0] == "newbie"}
    assert grants == {("newbie", "f2", "USER"): ("WRITER", "app1", True), ("newbie", "app2", "@uar"): (None, "app2", False)}
    assert not await differences(p, org)


# ---- a principal found by email ----------------------------------------------------------------------------------------------------------------

async def test_an_address_names_the_user_else_the_group_else_the_person_of_the_org(w: World) -> None:
    await w.nodes("users", {"_key": "ua", "email": "Ann@X.io"}, {"_key": "ub", "email": "both@x.io"})
    await w.nodes("groups", {"_key": "ga", "email": "team@x.io"}, {"_key": "gb", "email": "Both@x.io"}, {"_key": "gc", "email": "pg@x.io"})
    await person(w, "pa", "pat@x.io")
    await person(w, "pb", "pg@x.io")
    p = w.p
    assert [await p.get_entity_id_by_email(e) for e in ("ann@x.io", "TEAM@x.io", "pat@x.io", "both@x.io", "pg@x.io", "none@x.io")] == ["ua", "ga", "pa", "ub", "gc", None]
    assert await p.bulk_get_entity_ids_by_email(["ann@x.io", "team@x.io", "pat@x.io", "Both@x.io", "pg@x.io", "none@x.io", "ann@x.io", ""]) == {
        "Ann@X.io": ("ua", "users", "USER"), "team@x.io": ("ga", "groups", "GROUP"), "pat@x.io": ("pa", "person", "USER"),
        "both@x.io": ("ub", "users", "USER"), "Both@x.io": ("gb", "groups", "GROUP"), "pg@x.io": ("gc", "groups", "GROUP")}      # keyed by the address as stored
    assert await p.bulk_get_entity_ids_by_email([]) == {}
    with in_org(_org()):
        assert await p.get_entity_id_by_email("ann@x.io") is None and await p.bulk_get_entity_ids_by_email(["ann@x.io"]) == {}


async def test_a_failed_lookup_by_email_answers_nothing(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "ua", "email": "ann@x.io"})
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_entity_id_by_email("ann@x.io") is None and await w.p.bulk_get_entity_ids_by_email(["ann@x.io"]) == {}


async def test_no_org_is_an_error_for_a_lookup_by_email(pg_provider) -> None:
    with pytest.raises(MissingOrgContext):
        await pg_provider.get_entity_id_by_email("a@x.io")
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await pg_provider.bulk_get_entity_ids_by_email(["a@x.io"])


# ---- the entity relations of a record ----------------------------------------------------------------------------------------------------------

async def test_one_edge_per_pair_and_type_rewritten_by_a_second_write(w: World) -> None:
    await w.nodes("users", {"_key": "bob"})
    await w.nodes("records", rec("case"), rec("other"))
    assert await w.p.batch_create_entity_relations([
        {"_from": "records/case", "_to": "users/bob", "edgeType": "ASSIGNED_TO", "origin": "INFERRED", "createdAtTimestamp": 1},
        {"from_collection": "records", "from_id": "case", "to_collection": "users", "to_id": "bob", "edgeType": "CREATED_BY", "createdAtTimestamp": 1},
        {"_from": "records/case", "_to": "users/bob", "edgeType": "ASSIGNED_TO", "origin": "INFERRED", "source": "first", "createdAtTimestamp": 1},   # the last wins
    ]) is True
    assert [(e[2], e[3]) for e in await edges(w, "entityRelations", frm=("records", "case"))] == [
        ("ASSIGNED_TO", {"edgeType": "ASSIGNED_TO", "origin": "INFERRED", "source": "first"}), ("CREATED_BY", {"edgeType": "CREATED_BY"})]
    await w.p.batch_create_entity_relations([{"_from": "records/case", "_to": "users/bob", "edgeType": "ASSIGNED_TO", "source": "second", "sourceTimestamp": None,
                                              "createdAtTimestamp": 2}])
    assert [(e[2], e[3]) for e in await edges(w, "entityRelations", frm=("records", "case"))] == [
        ("ASSIGNED_TO", {"edgeType": "ASSIGNED_TO", "source": "second"}), ("CREATED_BY", {"edgeType": "CREATED_BY"})]       # SET r = props: the old fields go
    assert await w.p.batch_create_entity_relations([]) is True


async def test_an_edge_without_both_ends_or_a_type_is_skipped(w: World) -> None:
    await w.nodes("users", {"_key": "bob"})
    await w.nodes("records", rec("case"))
    assert await w.p.batch_create_entity_relations([
        {"_from": "records/case", "_to": "users/ghost", "edgeType": "ASSIGNED_TO"},                 # not a node
        {"_from": "records/case", "_to": "users/bob"},                                              # no type
        {"from_id": "case", "to_id": "bob", "edgeType": "ASSIGNED_TO"},                              # no collections
        {"_from": "records/case", "edgeType": "ASSIGNED_TO"},                                        # no end
        {"_from": "case", "_to": "users/bob", "edgeType": "ASSIGNED_TO"},                            # no collection in the id
        {"_from": "records/case", "_to": "users/bob", "edgeType": "REPORTED_BY"},
    ]) is True
    assert [e[2] for e in await edges(w, "entityRelations")] == ["REPORTED_BY"]
    with in_org(_org()):
        await w.p.batch_create_entity_relations([{"_from": "records/case", "_to": "users/bob", "edgeType": "OWNED_BY"}])     # another org's nodes are not there
    assert [e[2] for e in await edges(w, "entityRelations")] == ["REPORTED_BY"]


async def test_entity_relations_written_in_a_transaction_go_with_its_rollback(w: World) -> None:
    await w.nodes("users", {"_key": "bob"})
    await w.nodes("records", rec("case"))
    tx = await w.p.begin_transaction([], [])
    await w.p.batch_create_entity_relations([{"_from": "records/case", "_to": "users/bob", "edgeType": "ASSIGNED_TO"}], tx)
    assert await w.p.delete_record_entity_relations("case", "users", "INFERRED", tx) == 1
    await w.p.batch_create_entity_relations([{"_from": "records/case", "_to": "users/bob", "edgeType": "ASSIGNED_TO"}], tx)
    await w.p.rollback_transaction(tx)
    assert await edges(w, "entityRelations") == []


async def test_each_writer_clears_only_its_own_origin(w: World) -> None:
    await w.nodes("organizations", {"_key": "acme", "isExternal": True, "parentOrgId": w.org}, {"_key": "globex", "isExternal": True, "parentOrgId": w.org})
    await w.nodes("users", {"_key": "bob"})
    await w.nodes("records", rec("deal"), rec("other"))
    await relate(w, ("records", "deal"), ("organizations", "acme"), "FOR_ACCOUNT", origin="INFERRED")
    await relate(w, ("records", "deal"), ("organizations", "globex"), "MENTIONS", origin="EXTRACTED")
    await relate(w, ("records", "deal"), ("users", "bob"), "ASSIGNED_TO", origin="EXTRACTED")              # another collection
    await relate(w, ("records", "other"), ("organizations", "globex"), "MENTIONS", origin="EXTRACTED")      # another record
    assert await w.p.delete_record_entity_relations("deal", "organizations", "EXTRACTED") == 1
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "deal"))] == [("organizations/acme", "FOR_ACCOUNT"), ("users/bob", "ASSIGNED_TO")]
    await relate(w, ("records", "deal"), ("organizations", "acme"), "FOR_ACCOUNT")                          # from before origins existed: a connector's
    await relate(w, ("records", "deal"), ("organizations", "globex"), "MENTIONS", origin="EXTRACTED")
    assert await w.p.delete_record_entity_relations("deal", "organizations", "INFERRED") == 1
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "deal"))] == [("organizations/globex", "MENTIONS"), ("users/bob", "ASSIGNED_TO")]
    assert len(await edges(w, "entityRelations", frm=("records", "other"))) == 1
    assert await w.p.delete_record_entity_relations("deal", "organizations", "INFERRED") == 0
    for args in (("", "organizations", "INFERRED"), ("deal", "", "INFERRED"), ("deal", "organizations", "")):
        with pytest.raises(ValueError, match="needs a record"):
            await w.p.delete_record_entity_relations(*args)


async def test_a_replace_swaps_the_origins_links_and_leaves_the_rest(w: World) -> None:
    await w.nodes("organizations", {"_key": "acme", "isExternal": True, "parentOrgId": w.org}, {"_key": "globex", "isExternal": True, "parentOrgId": w.org})
    await w.nodes("users", {"_key": "bob"}, {"_key": "ann"})
    await w.nodes("records", rec("deal"), rec("other"))
    await relate(w, ("records", "deal"), ("organizations", "acme"), "FOR_ACCOUNT", origin="INFERRED")
    await relate(w, ("records", "deal"), ("organizations", "globex"), "MENTIONS", origin="EXTRACTED")
    await relate(w, ("records", "deal"), ("users", "bob"), "ASSIGNED_TO")                                  # from before origins existed: INFERRED
    await relate(w, ("records", "other"), ("organizations", "acme"), "FOR_ACCOUNT", origin="INFERRED")      # another record
    new = [{"_from": "records/deal", "_to": "users/ann", "edgeType": "ASSIGNED_TO", "origin": "INFERRED", "createdAtTimestamp": 2}]
    await w.p.replace_record_entity_relations("deal", ["users", "person"], "INFERRED", new)
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "deal"))] == [
        ("organizations/acme", "FOR_ACCOUNT"), ("organizations/globex", "MENTIONS"), ("users/ann", "ASSIGNED_TO")]      # the organisations are not in to_collections
    keep = {"_from": "records/deal", "_to": "organizations/acme", "edgeType": "FOR_ACCOUNT", "origin": "INFERRED", "createdAtTimestamp": 3}
    await w.p.replace_record_entity_relations("deal", ["organizations"], "INFERRED", [keep])                  # the same edge again: kept, not duplicated
    await w.p.replace_record_entity_relations("deal", ["organizations"], "INFERRED", [{**keep, "_to": "organizations/globex", "edgeType": "FOR_ACCOUNT"}])
    assert [(e[1], e[2], e[3].get("origin")) for e in await edges(w, "entityRelations", frm=("records", "deal"))] == [
        ("organizations/globex", "FOR_ACCOUNT", "INFERRED"), ("organizations/globex", "MENTIONS", "EXTRACTED"), ("users/ann", "ASSIGNED_TO", "INFERRED")]
    await w.p.replace_record_entity_relations("deal", ["organizations", "users"], "INFERRED", [])             # no edges clears the origin
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "deal"))] == [("organizations/globex", "MENTIONS")]
    assert len(await edges(w, "entityRelations", frm=("records", "other"))) == 1
    for args in (("", ["users"], "INFERRED", []), ("deal", [], "INFERRED", []), ("deal", ["users"], "", [])):
        with pytest.raises(ValueError, match="needs a record"):
            await w.p.replace_record_entity_relations(*args)


async def test_a_replace_that_fails_keeps_the_old_links_without_a_transaction(w: World) -> None:
    await w.nodes("users", {"_key": "bob"}, {"_key": "ann"})
    await w.nodes("records", rec("case"))
    await relate(w, ("records", "case"), ("users", "bob"), "ASSIGNED_TO", origin="INFERRED")
    good = {"_from": "records/case", "_to": "users/ann", "edgeType": "ASSIGNED_TO", "origin": "INFERRED", "createdAtTimestamp": 2}
    # an edge type no index row can hold: the insert fails inside the statement, after its delete is planned
    bad = {**good, "edgeType": secrets.token_hex(8000)}
    with pytest.raises(Exception, match="index row requires"):
        await w.p.replace_record_entity_relations("case", ["users"], "INFERRED", [good, bad])
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "case"))] == [("users/bob", "ASSIGNED_TO")]
    tx = await w.p.begin_transaction([], [])
    await w.p.replace_record_entity_relations("case", ["users"], "INFERRED", [good], tx)
    await w.p.rollback_transaction(tx)
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "case"))] == [("users/bob", "ASSIGNED_TO")]


async def test_a_replace_in_one_org_never_touches_another(w: World) -> None:
    await w.nodes("users", {"_key": "bob"})
    await w.nodes("records", rec("case"))
    await relate(w, ("records", "case"), ("users", "bob"), "ASSIGNED_TO", origin="INFERRED")
    other = _org()
    with in_org(other):
        await w.p.replace_record_entity_relations("case", ["users"], "INFERRED", [])        # same ids, another org: nothing there to clear
        await w.p.replace_record_entity_relations("case", ["users"], "INFERRED", [{"_from": "records/case", "_to": "users/bob", "edgeType": "OWNED_BY"}])   # nor to link
    assert [(e[1], e[2]) for e in await edges(w, "entityRelations", frm=("records", "case"))] == [("users/bob", "ASSIGNED_TO")]


async def test_no_org_is_an_error_for_a_replace(pg_provider) -> None:
    with pytest.raises(MissingOrgContext):
        await pg_provider.replace_record_entity_relations("case", ["users"], "INFERRED", [])


async def test_the_people_of_a_record_are_its_orgs_users_linked_either_way_each_once(w: World) -> None:
    await w.nodes("users", {"_key": "bob", "fullName": "Bob", "email": "bob@x.io"}, {"_key": "ann", "email": "ann@x.io"}, {"_key": "out", "orgId": "elsewhere", "fullName": "Out"})
    await person(w, "eve", "eve@x.io")
    await w.nodes("records", rec("case"), rec("foreign", orgId="elsewhere"))
    await relate(w, ("records", "case"), ("users", "bob"), "ASSIGNED_TO")
    await relate(w, ("records", "case"), ("users", "bob"), "CREATED_BY")
    await relate(w, ("users", "ann"), ("records", "case"), "MENTIONED_IN")                                 # a mention points at the record
    await relate(w, ("records", "case"), ("users", "out"), "ASSIGNED_TO")                                  # a user of another org
    await relate(w, ("records", "case"), ("person", "eve"), "AUTHORED_BY")                                 # not a member
    await relate(w, ("records", "foreign"), ("users", "bob"), "ASSIGNED_TO")
    assert await w.p.get_record_people("case", w.org) == [{"id": "ann", "name": None, "email": "ann@x.io"}, {"id": "bob", "name": "Bob", "email": "bob@x.io"}]
    assert await w.p.get_record_people("foreign", w.org) == []                                             # a record of another org names nobody here
    assert await w.p.get_record_people("case", "elsewhere") == [] and await w.p.get_record_people("", w.org) == [] and await w.p.get_record_people("case", "") == []
    assert await w.p.get_record_people("no-such", w.org) == []


# ---- external organisations ----------------------------------------------------------------------------------------------------------------

def account(w: World, key: str, name: str, *, parent: str | None = "mine", external: object = True, **doc: object) -> dict:
    d = {"_key": key, "name": name, "accountType": "enterprise", "isActive": True, "isExternal": external, **doc}
    if parent is not None:
        d["parentOrgId"] = w.org if parent == "mine" else parent
    return d


async def org_world(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org, "accountType": "enterprise", "isActive": True},
                  account(w, "acme", "Acme", normalizedName="acme"), account(w, "legacy", "Legacy Co", parent=None),
                  account(w, "other", "Other Inc", parent="elsewhere", normalizedName="acme"), account(w, "inside", "Inside", external=False, normalizedName="acme"),
                  account(w, "beta", "Beta", normalizedName="beta"))
    await w.nodes("recordGroups", {"_key": "rg-acme", "connectorId": APP}, {"_key": "rg-other", "connectorId": APP}, {"_key": "rg-two", "connectorId": APP})


async def test_the_organisations_of_a_record_are_the_external_ones_of_its_org(w: World) -> None:
    await org_world(w)
    await w.nodes("records", rec("case"), rec("deal-other"), rec("foreign", orgId="elsewhere"))
    for target, typ in (("beta", "MENTIONS"), ("acme", "FOR_ACCOUNT"), ("acme", "MENTIONS"), ("other", "FOR_ACCOUNT"), ("inside", "MENTIONS"), ("legacy", "MENTIONS")):
        await relate(w, ("records", "case"), ("organizations", target), typ)
    await relate(w, ("records", "deal-other"), ("organizations", "other"), "FOR_ACCOUNT")              # written by mistake: still not this org's
    await relate(w, ("records", "foreign"), ("organizations", "acme"), "FOR_ACCOUNT")
    await w.p.batch_create_edges([{"from_collection": "organizations", "from_id": "beta", "to_collection": "records", "to_id": "deal-other", "edgeType": "X"}], "entityRelations")
    assert await w.p.get_record_organizations("case", w.org) == [{"id": "acme", "name": "Acme"}, {"id": "beta", "name": "Beta"}]
    assert await w.p.get_record_organizations("deal-other", w.org) == []
    assert await w.p.get_record_organizations("foreign", w.org) == []
    assert await w.p.get_record_organizations("case", "elsewhere") == [] and await w.p.get_record_organizations("", w.org) == []


async def test_a_record_group_belongs_to_the_external_organisation_its_deal_of_edge_points_at(w: World) -> None:
    await org_world(w)
    await w.edge("dealOf", ("recordGroups", "rg-acme"), ("organizations", "acme"))
    await w.edge("dealOf", ("recordGroups", "rg-other"), ("organizations", "other"))
    await w.edge("dealOf", ("recordGroups", "rg-two"), ("organizations", "beta"))
    await w.edge("dealOf", ("recordGroups", "rg-two"), ("organizations", "acme"))
    await w.edge("belongsTo", ("recordGroups", "rg-other"), ("organizations", "beta"))
    await w.edge("dealOf", ("recordGroups", "ghost"), ("organizations", "acme"))                        # not a node
    p = w.p
    assert [await p.get_record_group_organization(g, w.org) for g in ("rg-acme", "rg-other", "rg-two", "ghost", "")] == ["acme", None, "acme", None, None]
    assert await p.get_record_group_organization("rg-acme", "elsewhere") is None and await p.get_record_group_organization("rg-acme", "") is None
    tx = await p.begin_transaction([], [])
    await p.batch_create_edges([{"from_collection": "recordGroups", "from_id": "rg-other", "to_collection": "organizations", "to_id": "legacy"}], "dealOf", tx)
    await p.update_node("legacy", "organizations", {"parentOrgId": w.org}, tx)
    assert await p.get_record_group_organization("rg-other", w.org, tx) == "legacy"
    await p.rollback_transaction(tx)
    assert await p.get_record_group_organization("rg-other", w.org) is None


async def test_the_org_stamps_itself_on_the_accounts_it_sells_to_that_carry_no_owner(w: World) -> None:
    await org_world(w)
    await w.nodes("organizations", account(w, "cust", "Customer", parent=None), account(w, "nul", "Null", parent=None), account(w, "lone", "Lone", parent=None),
                  account(w, "notext", "Not external", parent=None, external=False), account(w, "strext", "String flag", parent=None, external="true"))
    await w.edge("prospect", ("organizations", w.org), ("organizations", "legacy"))
    await w.edge("customer", ("organizations", w.org), ("organizations", "cust"))
    await w.edge("prospect", ("organizations", w.org), ("organizations", "other"))                       # owned already
    await w.edge("prospect", ("organizations", w.org), ("organizations", "notext"))
    await w.edge("prospect", ("organizations", w.org), ("organizations", "strext"))                      # Neo4j's ``= true`` is the boolean only
    await w.edge("prospect", ("organizations", "beta"), ("organizations", "lone"))                       # not from the org's own node
    await w.edge("belongsTo", ("organizations", w.org), ("organizations", "nul"))
    assert await w.p.stamp_external_org_parents(w.org) == 2
    parents = {k: (await w.p.get_document(k, "organizations")).get("parentOrgId") for k in ("legacy", "cust", "other", "notext", "strext", "lone", "nul", "acme")}
    assert parents == {"legacy": w.org, "cust": w.org, "other": "elsewhere", "notext": None, "strext": None, "lone": None, "nul": None, "acme": w.org}
    assert await w.p.stamp_external_org_parents(w.org) == 0 and await w.p.stamp_external_org_parents("") == 0
    with pytest.raises(OrgMismatch):
        await w.p.stamp_external_org_parents("elsewhere")


async def test_two_stampers_at_once_count_each_account_once(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org}, *(account(w, f"a{i}", f"A{i}", parent=None) for i in range(20)))
    for i in range(20):
        await w.edge("prospect", ("organizations", w.org), ("organizations", f"a{i}"))
    assert sum(await asyncio.gather(*(w.p.stamp_external_org_parents(w.org) for _ in range(4)))) == 20


async def test_an_extracted_organisation_is_created_once_for_its_org_and_found_by_key(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org, "accountType": "enterprise", "isActive": True})
    node = {"id": "x-globex", "name": "Globex Corporation", "normalizedName": "globex"}
    await w.p.create_organization_if_absent(w.org, node)
    await w.p.create_organization_if_absent(w.org, {**node, "name": "Globex"})                          # a no-op
    await w.nodes("organizations", account(w, "acme", "Acme Corp", normalizedName="acme"), account(w, "theirs", "Globex", parent="elsewhere", normalizedName="globex"),
                  account(w, "flat", "Flat", external=False, normalizedName="globex"), account(w, "b-acme", "Acme Two", normalizedName="acme"))
    rows = await w.p.find_organizations(w.org, ["globex", "acme", "initech", "acme", ""])
    assert rows == [{"id": "acme", "name": "Acme Corp", "normalizedName": "acme"}, {"id": "b-acme", "name": "Acme Two", "normalizedName": "acme"},
                    {"id": "x-globex", "name": "Globex Corporation", "normalizedName": "globex"}]
    made = await w.p.get_document("x-globex", "organizations")
    assert {k: made.get(k) for k in ("isExternal", "parentOrgId", "isActive", "accountType", "name")} == {
        "isExternal": True, "parentOrgId": w.org, "isActive": True, "accountType": "enterprise", "name": "Globex Corporation"}
    assert await w.p.find_organizations(w.org, []) == [] and await w.p.find_organizations("", ["acme"]) == [] and await w.p.find_organizations("elsewhere", ["globex"]) == []
    await w.p.create_organization_if_absent(w.org, {"_key": "acme", "name": "Renamed"})                 # an existing account is left as it is
    assert (await w.p.get_document("acme", "organizations"))["name"] == "Acme Corp"


async def test_an_extracted_organisation_needs_an_org_an_id_and_a_name_and_follows_its_transaction(w: World) -> None:
    for org, node in (("", {"id": "k", "name": "n"}), (w.org, {"name": "n"}), (w.org, {"id": "k"})):
        with pytest.raises(ValueError, match="needs an org"):
            await w.p.create_organization_if_absent(org, node)
    with pytest.raises(OrgMismatch):
        await w.p.create_organization_if_absent("elsewhere", {"id": "k", "name": "n"})
    tx = await w.p.begin_transaction([], [])
    await w.p.create_organization_if_absent(w.org, {"id": "k", "name": "n", "normalizedName": "n"}, tx)
    assert await w.p.find_organizations(w.org, ["n"], tx) == [{"id": "k", "name": "n", "normalizedName": "n"}]
    await w.p.rollback_transaction(tx)
    assert await w.p.find_organizations(w.org, ["n"]) == []


async def test_reach_counts_live_records_naming_it_and_whether_a_connector_knows_it(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org}, account(w, "named", "Named"), account(w, "account", "Account"), account(w, "linked", "Linked"),
                  account(w, "theirs", "Theirs", parent="elsewhere"), account(w, "prospect", "Prospect"), account(w, "flat", "Flat", external=False),
                  account(w, "dangling", "Dangling"), account(w, "trashlinked", "Trash linked"))
    await w.nodes("recordGroups", {"_key": "rg", "connectorId": APP})
    await w.edge("dealOf", ("recordGroups", "rg"), ("organizations", "account"))
    await w.edge("customer", ("organizations", w.org), ("organizations", "prospect"))
    await w.edge("dealOf", ("recordGroups", "ghost"), ("organizations", "dangling"))                    # from no node
    await w.nodes("records", rec("r1"), rec("r2"), rec("r3"), rec("gone", isDeleted=True), rec("foreign", orgId="elsewhere"))
    for r, target, typ, origin in (("r1", "named", "MENTIONS", "EXTRACTED"), ("gone", "named", "MENTIONS", "EXTRACTED"), ("foreign", "named", "MENTIONS", "EXTRACTED"),
                                   ("r1", "account", "MENTIONS", "EXTRACTED"), ("r2", "linked", "FOR_ACCOUNT", "INFERRED"), ("r3", "theirs", "MENTIONS", "EXTRACTED"),
                                   ("r3", "flat", "MENTIONS", "EXTRACTED"), ("gone", "trashlinked", "FOR_ACCOUNT", "INFERRED"), ("r3", "dangling", "MENTIONS", "EXTRACTED")):
        await relate(w, ("records", r), ("organizations", target), typ, origin=origin)
    await relate(w, ("records", "r3"), ("organizations", "prospect"), "FOR_ACCOUNT")                     # no origin: INFERRED
    reach = await w.p.get_organization_record_reach(w.org, ["named", "account", "linked", "theirs", "flat", "prospect", "dangling", "trashlinked", "none", "named", ""])
    assert reach == {"named": {"records": 1, "inferred": False}, "account": {"records": 1, "inferred": True}, "linked": {"records": 0, "inferred": True},
                     "prospect": {"records": 0, "inferred": True}, "dangling": {"records": 1, "inferred": False}, "trashlinked": {"records": 0, "inferred": False}}
    await w.nodes("records", rec("r4"), rec("r5"))
    await relate(w, ("records", "r4"), ("organizations", "named"), "MENTIONS", origin="EXTRACTED")
    assert (await w.p.get_organization_record_reach(w.org, ["named"]))["named"]["records"] == 2
    await relate(w, ("records", "r4"), ("organizations", "named"), "FOR_ACCOUNT", origin="EXTRACTED")     # a second extracted type: still one record
    assert (await w.p.get_organization_record_reach(w.org, ["named"]))["named"]["records"] == 2
    await relate(w, ("records", "r5"), ("organizations", "named"), "MENTIONS", origin="EXTRACTED")
    assert await w.p.get_organization_record_reach(w.org, ["named"], record_cap=2) == {"named": {"records": 2, "inferred": False}}
    assert (await w.p.get_organization_record_reach(w.org, ["named"], record_cap=0))["named"]["records"] == 1      # a cap is at least one
    assert await w.p.get_organization_record_reach(w.org, []) == {} and await w.p.get_organization_record_reach("", ["named"]) == {}
    assert await w.p.get_organization_record_reach("elsewhere", ["theirs"]) == {}


async def test_organisation_writes_leave_kg_as_a_whole_projection_would(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """The organisations are principals of kg: an extracted one appears in it, a stamp and the entity relations change nothing it holds."""
    for k, v in {"PG_KG_PROJECT": "1", "PG_KG_FLUSH": "inline", "PG_KG_STRICT": "1", "PG_KG_GUARD": "strict"}.items():
        monkeypatch.setenv(k, v)
    p, org = prov(pg_db), _org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "legacy", "name": "Legacy", "isExternal": True}], "organizations")
        await p.batch_create_edges([{"from_id": org, "from_collection": "organizations", "to_id": "legacy", "to_collection": "organizations"}], "prospect")
        await p.create_organization_if_absent(org, {"id": "x-acme", "name": "Acme", "normalizedName": "acme"})
        assert await p.stamp_external_org_parents(org) == 1
        await p.batch_create_entity_relations([{"_from": "records/f2", "_to": "organizations/x-acme", "edgeType": "MENTIONS", "origin": "EXTRACTED"},
                                               {"_from": "records/f2", "_to": "users/u1", "edgeType": "AUTHORED_BY"}])
        assert await p.delete_record_entity_relations("f2", "users", "INFERRED") == 1
    assert not await differences(p, org)
