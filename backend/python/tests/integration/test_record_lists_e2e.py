"""The All Records and KB record lists, against a real Neo4j and a real ArangoDB.

Seeds one user with a knowledge base (a root file and a folder holding a file)
and one connector record shared with them directly, then lists them the way
kb_service does: ``list_all_records`` and ``list_kb_records`` with the user's
graph key. Each list must return the seeded records, a total that agrees with
the page, and honour pagination and a search filter.

On ArangoDB both lists used to answer every request with an empty page: the
count query was sent bind parameters it never declared, and the KB list read
``user_permission`` as a collection name. The error was logged and swallowed,
so users saw an empty list rather than a failure.

Needs Docker services, and fails, naming the backend, when one is not reachable:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_record_lists_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "record_lists_it"

logger = logging.getLogger("record-lists-it")

NAMES = ("kb_root", "kb_folder", "kb_file", "drive_file")


@dataclass
class _World:
    graph: IGraphDBProvider
    org_id: str
    user_id: str
    user_key: str
    connector_id: str
    kb_id: str
    team_user_key: str = ""
    team_ids: tuple[str, ...] = ()
    ids: dict[str, str] = field(default_factory=dict)


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    return provider


async def _connect_arango() -> IGraphDBProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    await provider.ensure_schema()
    return provider


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.user_key, w.connector_id, w.kb_id, w.team_user_key, *w.team_ids]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (
        CollectionNames.RECORDS.value,
        CollectionNames.FILES.value,
        CollectionNames.USERS.value,
        CollectionNames.APPS.value,
        CollectionNames.TEAMS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (
        CollectionNames.PERMISSION.value,
        CollectionNames.BELONGS_TO.value,
        CollectionNames.IS_OF_TYPE.value,
        CollectionNames.RECORD_RELATIONS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            pytest.fail(f"{request.param} is not reachable, so nothing was checked: {exc!r}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        suffix = uuid.uuid4().hex[:10]
        w = _World(
            graph=graph,
            org_id=f"org-lists-{suffix}",
            user_id=f"user-lists-{suffix}",
            user_key=f"ukey-lists-{suffix}",
            connector_id=f"drive-lists-{suffix}",
            kb_id=f"kb-lists-{suffix}",
            team_user_key=f"ukey-team-{suffix}",
            team_ids=(f"team-a-{suffix}", f"team-b-{suffix}"),
        )
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


def _file(w: _World, name: str, *, kb: bool, folder: bool = False) -> FileRecord:
    return FileRecord(
        id=w.ids[name],
        org_id=w.org_id,
        record_name=f"{name}-{w.org_id}",
        record_type=RecordType.FILE,
        external_record_id=f"ext-{w.ids[name]}",
        version=1,
        origin=OriginTypes.UPLOAD if kb else OriginTypes.CONNECTOR,
        connector_name=Connectors.KNOWLEDGE_BASE if kb else Connectors.GOOGLE_DRIVE,
        connector_id=w.kb_id if kb else w.connector_id,
        mime_type="application/vnd.folder" if folder else "application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value,
        is_file=not folder,
        extension=None if folder else "pdf",
    )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in NAMES:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "List Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [
            {"id": w.connector_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
             "scope": "team", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now},
            {"id": w.kb_id, "name": "Collection", "type": "KB", "appGroup": "Local Storage",
             "scope": "personal", "isActive": True, "orgId": w.org_id,
             "createdAtTimestamp": now, "updatedAtTimestamp": now},
        ],
        collection=CollectionNames.APPS.value,
    )
    await g.batch_upsert_records([
        _file(w, "kb_root", kb=True),
        _file(w, "kb_folder", kb=True, folder=True),
        _file(w, "kb_file", kb=True),
        _file(w, "drive_file", kb=False),
    ])

    def edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
        return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    users, apps, records = CollectionNames.USERS.value, CollectionNames.APPS.value, CollectionNames.RECORDS.value
    await g.batch_create_edges(
        [edge(w.user_key, users, w.kb_id, apps, role="OWNER", type="USER"),
         edge(w.user_key, users, w.ids["drive_file"], records, role="READER", type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [edge(w.ids[n], records, w.kb_id, apps, entityType="KB") for n in ("kb_root", "kb_folder", "kb_file")],
        collection=CollectionNames.BELONGS_TO.value,
    )
    await g.batch_create_edges(
        [edge(w.ids["kb_folder"], records, w.ids["kb_file"], records, relationshipType="PARENT_CHILD")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )

    # A second user who reaches the same KB only through two teams, with different roles.
    teams = CollectionNames.TEAMS.value
    await g.batch_upsert_nodes(
        [{"id": w.team_user_key, "userId": f"team-user-{w.org_id}", "orgId": w.org_id,
          "email": f"team-user-{w.org_id}@example.com", "fullName": "Team Member", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=users,
    )
    await g.batch_upsert_nodes(
        [{"id": t, "name": t, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for t in w.team_ids],
        collection=teams,
    )
    await g.batch_create_edges(
        [edge(w.team_user_key, users, w.team_ids[0], teams, role="READER", type="USER"),
         edge(w.team_user_key, users, w.team_ids[1], teams, role="WRITER", type="USER"),
         *(edge(t, teams, w.kb_id, apps, role="READER", type="TEAM") for t in w.team_ids)],
        collection=CollectionNames.PERMISSION.value,
    )


def _all_records_args(w: _World, **overrides: object) -> dict:
    args: dict = {
        "user_id": w.user_key, "org_id": w.org_id, "skip": 0, "limit": 50, "search": None,
        "record_types": None, "origins": None, "connectors": None, "indexing_status": None,
        "permissions": None, "date_from": None, "date_to": None,
        "sort_by": "recordName", "sort_order": "asc", "source": "all",
    }
    args.update(overrides)
    return args


def _kb_args(w: _World, **overrides: object) -> dict:
    args: dict = {
        "kb_id": w.kb_id, "user_id": w.user_key, "org_id": w.org_id, "skip": 0, "limit": 50,
        "search": None, "record_types": None, "origins": None, "connectors": None,
        "indexing_status": None, "date_from": None, "date_to": None,
        "sort_by": "recordName", "sort_order": "asc",
    }
    args.update(overrides)
    return args


async def test_all_records_lists_kb_and_connector_records(world: _World) -> None:
    records, total, _ = await world.graph.list_all_records(**_all_records_args(world))
    got = {r["id"] for r in records}
    assert {world.ids[n] for n in ("kb_root", "kb_file", "drive_file")} <= got
    assert total == len(records)


async def test_all_records_paginates_and_filters(world: _World) -> None:
    g = world.graph
    _, total, _ = await g.list_all_records(**_all_records_args(world))
    page, page_total, _ = await g.list_all_records(**_all_records_args(world, limit=1))
    assert len(page) == 1 and page_total == total

    found, found_total, _ = await g.list_all_records(**_all_records_args(world, search="drive_file"))
    assert [r["id"] for r in found] == [world.ids["drive_file"]]
    assert found_total == 1

    connector_only, _, _ = await g.list_all_records(**_all_records_args(world, source="connector"))
    assert {r["id"] for r in connector_only} == {world.ids["drive_file"]}

    kb_only, _, _ = await g.list_all_records(**_all_records_args(world, source="local"))
    got = {r["id"] for r in kb_only}
    assert {world.ids["kb_root"], world.ids["kb_file"]} <= got and world.ids["drive_file"] not in got

    # No KB is shared with this user as READER; the connector record must survive that.
    readers, readers_total, _ = await g.list_all_records(**_all_records_args(world, permissions=["READER"]))
    assert world.ids["drive_file"] in {r["id"] for r in readers}
    assert readers_total == len(readers)


async def test_kb_records_lists_the_folder_contents(world: _World) -> None:
    g = world.graph
    records, total, available = await g.list_kb_records(**_kb_args(world))
    got = {r["id"] for r in records}
    assert world.ids["kb_file"] in got
    assert total == len(records)
    assert world.ids["kb_folder"] in {f["id"] for f in available["folders"]}

    in_folder, folder_total, _ = await g.list_kb_records(**_kb_args(world, folder_id=world.ids["kb_folder"]))
    assert world.ids["kb_file"] in {r["id"] for r in in_folder}
    assert folder_total == len(in_folder)

    found, found_total, _ = await g.list_kb_records(**_kb_args(world, search="kb_file"))
    assert [r["id"] for r in found] == [world.ids["kb_file"]]
    assert found_total == 1


async def _records_route(w: _World, **overrides: object) -> dict:
    """GET /api/v1/records as the route runs it: the caller comes from the JWT."""
    from types import SimpleNamespace

    from app.connectors.api.router import get_records as records_route

    request = SimpleNamespace(
        app=SimpleNamespace(container=SimpleNamespace(logger=lambda: logger)),
        state=SimpleNamespace(user={"userId": w.user_id, "orgId": w.org_id}),
    )
    params: dict = {
        "page": 1, "limit": 50, "search": None, "record_types": None, "origins": None,
        "connectors": None, "indexing_status": None, "permissions": None,
        "date_from": None, "date_to": None, "sort_by": "recordName", "sort_order": "asc", "source": "all",
    }
    params.update(overrides)
    return await records_route(request=request, graph_provider=w.graph, **params)


async def test_the_records_route_lists_the_callers_records(world: _World) -> None:
    body = await _records_route(world)
    got = {r["id"] for r in body["records"]}
    assert {world.ids[n] for n in ("kb_root", "kb_file", "drive_file")} <= got
    assert body["pagination"]["totalCount"] == len(body["records"])

    connector_only = await _records_route(world, source="connector")
    assert {r["id"] for r in connector_only["records"]} == {world.ids["drive_file"]}


async def test_the_records_route_answers_404_for_an_unknown_caller(world: _World) -> None:
    stranger = _World(**{**world.__dict__, "user_id": f"nobody-{uuid.uuid4().hex[:8]}"})
    body = await _records_route(stranger)
    assert body == {"success": False, "code": 404, "reason": f"User not found for user_id: {stranger.user_id}"}


async def test_a_kb_reached_through_two_teams_is_listed_once(world: _World) -> None:
    """Each team grant used to add another copy of every record in the KB."""
    records, total, _ = await world.graph.list_all_records(
        **_all_records_args(world, user_id=world.team_user_key)
    )
    ids = [r["id"] for r in records]
    assert sorted(ids) == sorted({world.ids["kb_root"], world.ids["kb_file"]})
    assert total == len(records)
    assert {r["permission"]["role"] for r in records} == {"WRITER"}
