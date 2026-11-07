"""Against real servers: ``find_records_by_person`` finds a person who is not a
member (a Person node) only through a record the caller can read, on Neo4j and
ArangoDB.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_find_person_non_members_real_backends.py -m integration
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_filters, people
from app.agents.actions.knowledge_graph.ops.people import execute_find_records_by_person
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes, ProgressStatus
from app.models.entities import Person, Record, RecordType
from app.modules.retrieval.entity_permissions import EntityAccessContext
from tests.integration.graph_db.test_record_graph_write_concurrency import _open_arango, _open_neo4j
from tests.integration.graph_db.test_record_people_real_backends import _cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("find-person-it")
NOBODY = "Nobody Atall"


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    try:
        provider = await (_open_arango() if request.param == "arango" else _open_neo4j(monkeypatch, explicit=False))
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org, other_org = f"org-it-{uuid.uuid4().hex[:10]}", f"org-it-{uuid.uuid4().hex[:10]}"
    app = f"{org}-app"
    try:
        await _seed(provider, org, other_org, app)
        context = EntityAccessContext(
            org_id=org, user_key=f"{org}-me", app_level_app_ids=frozenset(),
            record_level_app_ids=frozenset({app}), record_group_ids=frozenset(), app_names={app: "Docs"},
        )
        monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=context))
        monkeypatch.setattr(people, "resolve_frontend_url", AsyncMock(return_value=None))
        monkeypatch.setattr(people, "cite_listed_records", AsyncMock(return_value={}))
        yield {"state": {"org_id": org, "user_id": "u", "user_email": f"me@{org}.test", "graph_provider": provider}}
    finally:
        await _cleanup(provider, org, disconnect=False)
        await _cleanup(provider, other_org)


async def _seed(provider: Any, org: str, other_org: str, app: str) -> None:  # noqa: ANN401
    users = CollectionNames.USERS.value
    await provider.batch_upsert_nodes([
        {"id": f"{org}-me", "userId": f"{org}-me", "orgId": org, "email": f"me@{org}.test", "fullName": "Me Myself"},
        {"id": f"{org}-mia", "userId": f"{org}-mia", "orgId": org, "email": "mia@acme.test", "fullName": "Mia Member"},
    ], users)
    await provider.batch_upsert_nodes(
        [{"id": app, "orgId": org, "name": "Docs", "type": Connectors.CONFLUENCE.value, "appGroup": "Docs",
          "scope": "team", "isActive": True, "createdAtTimestamp": 1}], CollectionNames.APPS.value,
    )
    await provider.batch_upsert_nodes(
        [{"id": f"{org}-rg", "orgId": org, "groupName": "Closed", "groupType": "CONFLUENCE_SPACES",
          "connectorName": Connectors.CONFLUENCE.value, "connectorId": app, "createdAtTimestamp": 1}],
        CollectionNames.RECORD_GROUPS.value,
    )
    ext = [("ext", "Quincy Outsider", "quincy@partner.test"), ("hid", "Hidden Hank", "hank@partner.test"),
           ("trash", "Trashed Tina", "tina@partner.test"), ("shared", "Mia Member", "mia@acme.test"),
           ("pat", "Pat Paged", "pat@partner.test")]
    await provider.batch_upsert_people(
        [Person(id=f"{org}-p-{k}", org_id=org, email=e, full_name=n) for k, n, e in ext]
        + [Person(id=f"{other_org}-p-olga", org_id=other_org, email="olga@partner.test", full_name="Olga Other")],
    )
    records = {  # key: (readable, trashed, newest-first stamp)
        "ext": (True, False), "hid": (False, False), "trash": (True, True), "shared": (True, False),
        "mia": (True, False), "pat1": (True, False), "pat2": (True, False), "pat3": (True, False),
    }
    docs = [
        Record(
            id=f"{org}-r-{k}", org_id=org, record_name=f"Doc {k}", record_type=RecordType.FILE,
            external_record_id=f"ext-{org}-{k}", version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.CONFLUENCE, connector_id=app, is_deleted=trashed,
            indexing_status=ProgressStatus.COMPLETED.value, source_updated_at=1000 - i,
        ).to_arango_base_record()
        for i, (k, (_, trashed)) in enumerate(records.items())
    ]
    await provider.batch_upsert_nodes([{**d, "id": d.pop("_key")} for d in docs], CollectionNames.RECORDS.value)
    rg = f"recordGroups/{org}-rg"
    placed = [(f"records/{org}-r-{k}", rg) for k in records]
    for edges, collection in (
        ([{"_from": f"users/{org}-me", "_to": f"apps/{app}", "syncState": "COMPLETED", "lastSyncUpdate": 1,
           "createdAtTimestamp": 1}], CollectionNames.USER_APP_RELATION.value),
        ([{"_from": f"apps/{app}", "_to": rg, "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1},
          *({"_from": parent, "_to": child, "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1}
            for child, parent in placed)], CollectionNames.NODE_RELATIONS.value),
        ([{"_from": rg, "_to": f"apps/{app}", "createdAtTimestamp": 1},
          *({"_from": child, "_to": parent, "createdAtTimestamp": 1} for child, parent in placed)],
         CollectionNames.BELONGS_TO.value),
        ([{"_from": child, "_to": parent, "createdAtTimestamp": 1} for child, parent in placed],
         CollectionNames.INHERIT_PERMISSIONS.value),
        ([{"_from": f"users/{org}-me", "_to": f"records/{org}-r-{k}", "type": "USER", "role": "READER",
           "createdAtTimestamp": 1} for k, (readable, _) in records.items() if readable],
         CollectionNames.PERMISSION.value),
    ):
        await provider.batch_create_edges(edges, collection)
    people_coll = CollectionNames.PEOPLE.value
    links = [("ext", people_coll, f"{org}-p-ext"), ("hid", people_coll, f"{org}-p-hid"),
             ("trash", people_coll, f"{org}-p-trash"), ("shared", people_coll, f"{org}-p-shared"),
             ("ext", people_coll, f"{other_org}-p-olga"), ("mia", users, f"{org}-mia"),
             *((f"pat{i}", people_coll, f"{org}-p-pat") for i in (1, 2, 3))]
    await provider.batch_create_entity_relations([
        {"_from": f"records/{org}-r-{k}", "_to": f"{c}/{i}", "edgeType": "AUTHORED_BY", "origin": "INFERRED",
         "source": app, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}
        for k, c, i in links
    ])


async def _ask(world: dict[str, Any], who: str, **kwargs: Any) -> str:  # noqa: ANN401
    ok, text = await execute_find_records_by_person(world["state"], who, **kwargs)
    assert ok, text
    return text


def _cursor(text: str) -> str | None:
    m = re.search(r'cursor="([^"]+)"', text)
    return m.group(1) if m else None


async def test_a_reader_finds_a_non_member_by_name_and_gets_the_record(world) -> None:
    text = await _ask(world, "Quincy Outsider")
    assert "Quincy Outsider (not a member)" in text and "Doc ext" in text
    assert "quincy@partner.test" not in text


async def test_the_full_email_finds_the_person_but_no_fragment_does(world) -> None:
    assert "Doc ext" in await _ask(world, "QUINCY@partner.test")
    nobody = await _ask(world, NOBODY)
    for fragment in ("@partner.test", "partner.test", "partner", "quincy@partner"):
        assert (await _ask(world, fragment)).replace(fragment, "X") == nobody.replace(NOBODY, "X")


async def test_a_person_with_no_readable_record_reads_as_no_match(world) -> None:
    nobody = (await _ask(world, NOBODY)).replace(NOBODY, "X")
    for who in ("Hidden Hank", "hank@partner.test", "Trashed Tina", "Olga Other"):
        text = await _ask(world, who)
        assert text.replace(who, "X") == nobody
        assert "not a member" not in text


async def test_a_person_with_a_members_email_never_takes_the_members_name_on_a_record(world) -> None:
    text = await _ask(world, "Mia Member")
    lines = {k: next(line for line in text.splitlines() if f"Doc {k}" in line) for k in ("mia", "shared")}
    assert "not a member" not in lines["mia"]
    assert "(linked to Mia Member (not a member), not to Mia Member (mia@acme.test))" in lines["shared"]
    assert text.count("Doc shared") == 1 and text.startswith("Records linked to Mia Member (mia@acme.test)")


async def test_a_non_member_pages_and_stops_at_the_last_readable_record(world) -> None:
    seen: list[str] = []
    text = await _ask(world, "Pat Paged", limit=2)
    cursor = _cursor(text)
    seen += re.findall(r"Doc pat\d", text)
    assert cursor and len(seen) == 2
    text = await _ask(world, "Pat Paged", limit=2, cursor=cursor)
    seen += re.findall(r"Doc pat\d", text)
    assert sorted(seen) == ["Doc pat1", "Doc pat2", "Doc pat3"] and _cursor(text) is None
