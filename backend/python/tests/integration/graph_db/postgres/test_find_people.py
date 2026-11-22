"""``find_records_by_person`` on PostgreSQL, against a real database: a person who is not a member (a ``person`` node) is found only through a record the caller can
read, and the role edges are walked from a ``person`` start as from a ``users`` one. Both directions: the reader gets the record, everyone else gets the reply of a name
that matches nobody.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_filters, people
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.agents.actions.knowledge_graph.ops.people import execute_find_records_by_person
from app.modules.retrieval.entity_permissions import EntityAccessContext, _person_ref

from .test_access import APP, rec
from .test_access import w as w
from .test_grants import failing, in_org

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration
NOBODY = "Nobody Atall"
OTHER_ORG = "f" * 24


def done(key: str, stamp: int, **kw: object) -> dict:
    return rec(key, recordName=f"Doc {key}", indexingStatus="COMPLETED", recordType="FILE", sourceLastModifiedTimestamp=stamp, **kw)


@pytest.fixture
async def world(w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> World:
    await w.nodes("users", {"_key": "mia", "userId": "mia", "email": "mia@acme.test", "fullName": "Mia Member"},
                  {"_key": "u", "userId": "u", "email": "me@acme.test", "fullName": "Me Myself"})
    outsiders = [("ext", "Quincy Outsider", "quincy@partner.test"), ("hid", "Hidden Hank", "hank@partner.test"), ("trash", "Trashed Tina", "tina@partner.test"),
                 ("shared", "Mia Member", "mia@acme.test"), ("eve", "Eve Short", "eve@partner.test"), ("steve", "Steve Long", "steve@partner.test"),
                 ("pat", "Pat Paged", "pat@partner.test")]
    await w.nodes("person", *({"_key": f"p-{k}", "fullName": n, "email": e} for k, n, e in outsiders))
    readable = ["ext", "trash", "shared", "mia", "eve", "steve", "pat1", "pat2", "pat3"]
    await w.nodes("records", *(done(k, 1000 - i, isDeleted=(k == "trash")) for i, k in enumerate([*readable, "hid"])))
    for k in readable:
        await w.edge("permission", ("users", "u"), ("records", k), type="USER", role="READER")
    links = [("ext", "person", "p-ext"), ("hid", "person", "p-hid"), ("trash", "person", "p-trash"), ("shared", "person", "p-shared"), ("mia", "users", "mia"),
             ("eve", "person", "p-eve"), ("steve", "person", "p-steve"), *((f"pat{i}", "person", "p-pat") for i in (1, 2, 3))]
    for k, coll, i in links:
        await w.edge("entityRelations", ("records", k), (coll, i), edgeType="AUTHORED_BY")
    with in_org(OTHER_ORG):                       # a person of another org, with the name of a person here
        await w.p.batch_upsert_nodes([{"_key": "p-olga", "orgId": OTHER_ORG, "fullName": "Olga Other", "email": "olga@partner.test"},
                                      {"_key": "p-quincy", "orgId": OTHER_ORG, "fullName": "Quincy Outsider", "email": "quincy@partner.test"}], "person")
    context = EntityAccessContext(org_id=w.org, user_key="u", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({APP}),
                                  record_group_ids=frozenset(), app_names={APP: "Docs"})
    monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=context))
    monkeypatch.setattr(people, "resolve_frontend_url", AsyncMock(return_value=None))
    monkeypatch.setattr(people, "cite_listed_records", AsyncMock(return_value={}))
    yield w
    with in_org(OTHER_ORG):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", OTHER_ORG), write=True)


async def ask(world: World, who: str, **kw: Any) -> str:  # noqa: ANN401
    ok, text = await execute_find_records_by_person({"org_id": world.org, "user_id": "u", "user_email": "me@acme.test", "graph_provider": world.p}, who, **kw)
    assert ok, text
    return text


async def test_a_reader_finds_a_non_member_by_name_and_gets_the_record(world: World) -> None:
    text = await ask(world, "Quincy Outsider")
    assert "Quincy Outsider (not a member)" in text and "Doc ext" in text and "quincy@partner.test" not in text
    assert "Doc ext" in await ask(world, "QUINCY@partner.test")


async def test_a_person_with_no_readable_record_reads_as_a_name_that_matches_nobody(world: World) -> None:
    nobody = (await ask(world, NOBODY)).replace(NOBODY, "X")
    for who in ("Hidden Hank", "hank@partner.test", "Trashed Tina", "Olga Other"):      # unreadable record, trashed record, a person of another org
        text = await ask(world, who)
        assert text.replace(who, "X") == nobody and "not a member" not in text


async def test_a_trashed_record_is_not_a_candidate_of_its_person(world: World) -> None:
    got = await world.p.get_entity_candidate_records([{"type": "person", "id": "p-trash", "connectorIds": [APP], "personIds": ["p-trash"]},
                                                      {"type": "person", "id": "p-ext", "connectorIds": [APP], "personIds": ["p-ext"]}], world.org)
    assert [r["_key"] for r in got[("person", "p-trash")]] == [] and [r["_key"] for r in got[("person", "p-ext")]] == ["ext"]


async def test_no_fragment_of_an_address_finds_anybody(world: World) -> None:
    nobody = await ask(world, NOBODY)
    for fragment in ("@partner.test", "partner.test", "quincy@partner", "uincy@partner.test", "@acme"):
        assert (await ask(world, fragment)).replace(fragment, "X") == nobody.replace(NOBODY, "X")


async def test_an_address_matches_only_the_whole_address(world: World) -> None:
    assert [p["id"] for p in await world.p.find_people(world.org, "eve@partner.test")] == ["p-eve"]      # not steve@partner.test, which holds it as a suffix
    assert await world.p.find_people(world.org, "@partner.test") == [] and await world.p.find_people(world.org, "partner.test") == []
    assert await world.p.find_people(world.org, "eve@partner") == [] and await world.p.find_people(world.org, "eve@") == []


async def test_find_people_matches_names_exact_before_prefix_in_the_ambient_org_only(world: World) -> None:
    assert [p["id"] for p in await world.p.find_people(world.org, "  quincy   OUTSIDER ")] == ["p-ext"]
    assert [p["id"] for p in await world.p.find_people(world.org, "Qui")] == ["p-ext"] and [p["id"] for p in await world.p.find_people(world.org, "Out")] == ["p-ext"]
    assert await world.p.find_people(world.org, "Qu") == []                       # under three characters, only a whole name
    assert [p["id"] for p in await world.p.find_people(world.org, "p", limit=1)] == []
    assert [p["id"] for p in await world.p.find_people(world.org, "Pat Paged", limit=1)] == ["p-pat"]
    assert await world.p.find_people(OTHER_ORG, "Quincy Outsider") == []        # an org other than the ambient one answers nothing
    assert await world.p.find_people("", "Quincy Outsider") == [] and await world.p.find_people(world.org, "  ") == []
    with in_org(OTHER_ORG):
        assert [p["id"] for p in await world.p.find_people(OTHER_ORG, "Quincy Outsider")] == ["p-quincy"]


async def test_a_person_with_a_members_email_never_makes_a_record_the_members_own(world: World) -> None:
    text = await ask(world, "Mia Member")
    lines = {k: next(line for line in text.splitlines() if f"Doc {k}" in line) for k in ("mia", "shared")}
    assert "not a member" not in lines["mia"]
    assert "(linked to Mia Member (not a member), not to Mia Member (mia@acme.test))" in lines["shared"]
    assert text.startswith("Records linked to Mia Member (mia@acme.test)")


async def test_a_non_member_pages_to_the_last_readable_record(world: World) -> None:
    text = await ask(world, "Pat Paged", limit=2)
    cursor = re.search(r'cursor="([^"]+)"', text)
    seen = re.findall(r"Doc pat\d", text)
    assert cursor and len(seen) == 2
    text = await ask(world, "Pat Paged", limit=2, cursor=cursor.group(1))
    assert sorted(seen + re.findall(r"Doc pat\d", text)) == ["Doc pat1", "Doc pat2", "Doc pat3"] and "cursor=" not in text


@pytest.mark.parametrize(("edge", "role", "toward"), [("AUTHORED_BY", "author", "person"), ("CREATED_BY", "creator", "person"), ("LAST_MODIFIED_BY", "editor", "person"),
                                                      ("OWNED_BY", "owner", "person"), ("MENTIONED_IN", "mentioned", "record")])
async def test_each_role_edge_is_walked_from_a_person_start_as_from_a_member(world: World, edge: str, role: str, toward: str) -> None:
    await world.nodes("records", done("role-p", 5), done("role-u", 4))
    for k in ("role-p", "role-u"):
        await world.edge("permission", ("users", "u"), ("records", k), type="USER", role="READER")
    for k, coll, i in (("role-p", "person", "p-ext"), ("role-u", "users", "mia")):
        ends = (("records", k), (coll, i)) if toward == "person" else ((coll, i), ("records", k))
        await world.edge("entityRelations", *ends, edgeType=edge)
    for who, key in (("Quincy Outsider", "role-p"), ("Mia Member", "role-u")):
        text = await ask(world, who, roles=[role])
        assert f"Doc {key}" in text
        assert ("Doc ext" in text) is (edge == "AUTHORED_BY" and key == "role-p")      # ext is authored by the person, so no other role lists it


async def test_a_failed_read_is_not_a_no_match_and_the_record_comes_back_with_the_store(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as broken:
        broken.setattr(world.p, "_q", failing)
        with pytest.raises(RuntimeError, match="store down"):
            await world.p.find_people(world.org, "Quincy Outsider")
        ok, text = await execute_find_records_by_person({"org_id": world.org, "user_id": "u", "user_email": "me@acme.test", "graph_provider": world.p}, "Quincy Outsider")
        assert not ok and text == LOOKUP_FAILED_MSG
    assert "Doc ext" in await ask(world, "Quincy Outsider")


async def test_a_user_and_a_person_with_one_key_each_reach_only_their_own_records(w: World) -> None:
    await w.nodes("users", {"_key": "same", "userId": "same", "email": "same@acme.test"})
    await w.nodes("person", {"_key": "same", "fullName": "Same Key", "email": "same@partner.test"})
    await w.nodes("records", done("of-user", 2), done("of-person", 1))
    await w.edge("entityRelations", ("records", "of-user"), ("users", "same"), edgeType="AUTHORED_BY")
    await w.edge("entityRelations", ("records", "of-person"), ("person", "same"), edgeType="AUTHORED_BY")

    async def reached(coll: str) -> list[str]:
        ref = {**_person_ref([(coll, "same")], ["AUTHORED_BY"]), "connectorIds": [APP]}
        return [r["_key"] for r in (await w.p.get_entity_candidate_records([ref], w.org))[(ref["type"], ref["id"])]]
    assert await reached("users") == ["of-user"]
    assert await reached("person") == ["of-person"]


async def test_a_person_node_naming_another_org_reaches_nothing(w: World) -> None:
    await w.nodes("person", {"_key": "foreign", "orgId": OTHER_ORG, "fullName": "Far Away"})
    await w.nodes("records", done("here", 1))
    await w.edge("entityRelations", ("records", "here"), ("person", "foreign"), edgeType="AUTHORED_BY")
    ref = {**_person_ref([("person", "foreign")], ["AUTHORED_BY"]), "connectorIds": [APP]}
    assert (await w.p.get_entity_candidate_records([ref], w.org))[(ref["type"], ref["id"])] == []
