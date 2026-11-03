"""Domain, "anyone" and "anyone with the link" shares grant nobody access.

Sources such as Google Drive report shares to a whole domain, to anyone, or to
anyone holding the link. PipesHub deliberately does not honour them: the Drive
connector hands the sync no grant for them (the permission model has no such
grantee), so a colleague who was not named on a file cannot find or open it.
That is a product decision, and this test keeps it from changing by accident,
for example by someone mapping one of them to an organization grant. Search
does not read ``anyone`` documents, so one left by older data or a stray writer
grants nothing; the last test pins that too.

It drives the production path on a real graph: the Drive connector reads a
file shared with the owner plus a domain, an anyone and an anyone-with-link
share, the processor stores it, and stores a control record shared with the
colleague by name. Then it checks:

* whether the sync wrote any grant for them: a permission edge, or an ``anyone``
  document; and
* ``get_accessible_virtual_record_ids``, what search returns to the colleague.

The control record proves search can see a grant, so a denial means something.
tests/unit/connectors/core/test_link_shares_stay_off.py guards the other way in:
production code starting to call the ``anyone`` writers.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.models.entities import FileRecord, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "link_and_domain_shares_it"
DOMAIN = "falconry.example"

logger = logging.getLogger("link-and-domain-shares-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    owner: dict
    colleague: dict
    anyone_ids: list[str]


def _user(run: str, name: str, org_id: str) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {
        "id": f"{run}-{name}-key",
        "userId": f"{run}-{name}",
        "orgId": org_id,
        "email": f"{name}-{run}@{DOMAIN}",
        "fullName": name,
        "isActive": True,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }


async def _remove(graph: IGraphDBProvider, env_ids: dict) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={
                "c": env_ids["connector_id"],
                "ids": [*env_ids["node_ids"], *env_ids["keys_by_collection"][CollectionNames.ANYONE.value]],
            },
        )
        return
    for collection, field, value in (
        (CollectionNames.RECORDS.value, "connectorId", env_ids["connector_id"]),
        (CollectionNames.RECORD_GROUPS.value, "connectorId", env_ids["connector_id"]),
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.{field} == @v REMOVE d IN {collection}", {"v": value}
        )
    for collection, keys in env_ids["keys_by_collection"].items():
        await graph.http_client.execute_aql(
            f"FOR k IN @keys REMOVE k IN {collection} OPTIONS {{ignoreErrors: true}}", {"keys": keys}
        )
    for edge in (
        CollectionNames.PERMISSION.value, CollectionNames.USER_APP_RELATION.value, CollectionNames.BELONGS_TO.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edge} FILTER e._from IN @h OR e._to IN @h REMOVE e IN {edge}",
            {"h": env_ids["handles"]},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-shares-{run}"
        connector_id = f"drive-shares-{run}"
        owner, colleague = _user(run, "owner", org_id), _user(run, "colleague", org_id)
        now = get_epoch_timestamp_in_ms()
        ids = {
            "connector_id": connector_id,
            "node_ids": [connector_id, owner["id"], colleague["id"], org_id],
            "keys_by_collection": {
                CollectionNames.ORGS.value: [org_id],
                CollectionNames.APPS.value: [connector_id],
                CollectionNames.USERS.value: [owner["id"], colleague["id"]],
                CollectionNames.ANYONE.value: [],
            },
            "handles": [
                f"users/{owner['id']}", f"users/{colleague['id']}", f"apps/{connector_id}",
                f"{CollectionNames.ORGS.value}/{org_id}",
            ],
        }
        cleanup.push_async_callback(_remove, graph, ids)

        assert await graph.batch_upsert_nodes(
            [{
                "id": connector_id, "name": "Google Drive", "type": Connectors.GOOGLE_DRIVE.value,
                "appGroup": "Google Workspace", "scope": "team", "isActive": True, "orgId": org_id,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes([owner, colleague], collection=CollectionNames.USERS.value)
        # Both belong to the organization, so a share stored as a grant to it would reach the colleague.
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "name": "Falconry", "accountType": "enterprise", "isActive": True}],
            collection=CollectionNames.ORGS.value,
        )
        assert await graph.batch_create_edges(
            [
                {
                    "from_id": user["id"], "from_collection": CollectionNames.USERS.value,
                    "to_id": org_id, "to_collection": CollectionNames.ORGS.value,
                    "entityType": "ORGANIZATION", "createdAtTimestamp": now, "updatedAtTimestamp": now,
                }
                for user in (owner, colleague)
            ],
            collection=CollectionNames.BELONGS_TO.value,
        )
        assert await graph.batch_create_edges(
            [
                {
                    "from_id": user["id"], "from_collection": CollectionNames.USERS.value,
                    "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
                    "syncState": "COMPLETED", "lastSyncUpdate": now,
                    "createdAtTimestamp": now, "updatedAtTimestamp": now,
                }
                for user in (owner, colleague)
            ],
            collection=CollectionNames.USER_APP_RELATION.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)
        yield _Env(
            graph, processor, org_id, connector_id, owner, colleague,
            ids["keys_by_collection"][CollectionNames.ANYONE.value],
        )


def _file(env: _Env, name: str) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    return FileRecord(
        org_id=env.org_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=f"drive-{uuid.uuid4().hex[:8]}",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id=env.connector_id,
        mime_type="text/plain",
        is_file=True,
        extension="txt",
        created_at=now,
        updated_at=now,
        source_created_at=now,
        source_updated_at=now,
    )


async def _drive_permissions(env: _Env, shares: list[dict]) -> list[Permission]:
    """What the Drive connector hands the sync for a file shared this way at the source."""
    connector = object.__new__(GoogleDriveTeamConnector)
    connector.logger = logger
    connector.synced_user_emails = set()
    connector._external_emails = set()
    connector.drive_data_source = MagicMock()
    connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": shares})
    permissions, is_fallback, _ = await connector._fetch_permissions("file", user_email=env.owner["email"])
    assert not is_fallback, permissions
    return permissions


async def _sync_the_two_files(env: _Env) -> tuple[FileRecord, FileRecord]:
    widely_shared = _file(env, "widely-shared.txt")
    named_share = _file(env, "shared-with-colleague.txt")
    link_style = await _drive_permissions(env, [
        {"id": "p-owner", "type": "user", "role": "owner", "emailAddress": env.owner["email"]},
        {"id": "p-domain", "type": "domain", "role": "reader", "domain": DOMAIN},
        {"id": "p-anyone", "type": "anyone", "role": "reader"},
        {"id": "p-link", "type": "anyoneWithLink", "role": "reader"},
    ])
    named = [Permission(type=PermissionType.READ, entity_type=EntityType.USER, email=env.colleague["email"])]
    await env.processor.on_new_records([(widely_shared, link_style), (named_share, named)])

    # Search only returns indexed records; stand in for indexing.
    for record in (widely_shared, named_share):
        await env.graph.update_node(
            record.id,
            CollectionNames.RECORDS.value,
            {"indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": f"vr-{record.id}"},
        )
    return widely_shared, named_share


async def _anyone_documents_for(graph: IGraphDBProvider, file_id: str) -> int:
    """The "anyone" documents naming this file."""
    if isinstance(graph, Neo4jProvider):
        rows = await graph.client.execute_query(
            "MATCH (a:Anyone {file_key: $id}) RETURN count(a) AS c", parameters={"id": file_id}
        )
        return int(rows[0]["c"]) if rows else 0
    rows = await graph.http_client.execute_aql(
        f"FOR a IN {CollectionNames.ANYONE.value} FILTER a.file_key == @id COLLECT WITH COUNT INTO c RETURN c",
        {"id": file_id},
    )
    return int(rows[0]) if rows else 0


async def test_the_sync_writes_no_grant_for_link_style_shares(env: _Env) -> None:
    """The sync writes neither a permission edge nor an "anyone" document for these shares.

    The owner's edge is checked first: the permission write shares one try block,
    so a share type that raised would drop the owner's edge too, and "no extra
    edge" would then prove nothing.
    """
    widely_shared, _ = await _sync_the_two_files(env)

    edges = await env.graph.get_edges_to_node(
        f"{CollectionNames.RECORDS.value}/{widely_shared.id}", CollectionNames.PERMISSION.value
    )
    sources = [
        (e.get("from_collection") or e.get("_from", "").split("/")[0],
         e.get("from_id") or e.get("_from", "").split("/")[-1])
        for e in edges
    ]
    assert (CollectionNames.USERS.value, env.owner["id"]) in sources, (
        f"The owner's permission was not written ({sources}), so the permission step failed as a whole."
    )
    assert sources == [(CollectionNames.USERS.value, env.owner["id"])], (
        f"Expected only the owner's permission on the widely shared file; found {sources}. "
        "A domain, anyone or anyone-with-link share has started writing a permission."
    )
    assert await _anyone_documents_for(env.graph, widely_shared.id) == 0, (
        "The sync wrote an \"anyone\" document for the file, for a share PipesHub decided grants nothing."
    )


async def test_search_does_not_return_the_widely_shared_file_to_the_colleague(env: _Env) -> None:
    widely_shared, named_share = await _sync_the_two_files(env)

    reachable = await env.graph.get_accessible_virtual_record_ids(
        env.colleague["userId"], env.org_id, raise_on_error=True
    )

    assert reachable.get(f"vr-{named_share.id}") == named_share.id, (
        f"Search does not return the file shared with the colleague by name; got {reachable}. "
        "Without that the denial below would prove nothing."
    )
    assert f"vr-{widely_shared.id}" not in reachable, (
        "Search returns the domain / anyone / anyone-with-link file to a colleague who was never named on it."
    )


async def test_an_anyone_document_grants_no_search_access(env: _Env) -> None:
    """An "anyone" document left by older data makes nothing searchable."""
    widely_shared, named_share = await _sync_the_two_files(env)
    # As the old permission writer stored it: {file_key, organization, active}.
    anyone_id = f"anyone_{widely_shared.id}"
    env.anyone_ids.append(anyone_id)
    assert await env.graph.batch_upsert_nodes(
        [{"id": anyone_id, "type": "anyone", "file_key": widely_shared.id,
          "organization": env.org_id, "role": "READER", "active": True}],
        collection=CollectionNames.ANYONE.value,
    )
    assert await _anyone_documents_for(env.graph, widely_shared.id) == 1, "the anyone document was not written"

    reachable = await env.graph.get_accessible_virtual_record_ids(
        env.colleague["userId"], env.org_id, raise_on_error=True
    )

    assert reachable.get(f"vr-{named_share.id}") == named_share.id, f"control not visible: {reachable}"
    assert f"vr-{widely_shared.id}" not in reachable, (
        "Search returns a file to a colleague only because an \"anyone\" document exists for it."
    )
