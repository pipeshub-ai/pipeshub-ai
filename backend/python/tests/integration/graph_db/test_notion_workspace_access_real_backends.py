"""A Notion team connector grants its pages to nobody, on real Neo4j and ArangoDB.

Notion's API does not say who may open a page, so being a workspace member proves
nothing about any one page. The connector's own code writes the workspace and its
pages through the production processor; search access is read back with
``get_accessible_virtual_record_ids``. A workspace stored while it still inherited
the app is rewritten on the next sync, and its pages stop being found.

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
    PermissionModel,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.notion.connector import NotionConnector
from app.models.entities import RecordGroup, RecordGroupType
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

ARANGO_DB = "notion_workspace_access_it"
WORKSPACE = "ws-notion-it"

logger = logging.getLogger("notion-workspace-access-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    connector: NotionConnector
    org_id: str
    member: dict


async def _remove(graph: IGraphDBProvider, connector_id: str, user_id: str) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, user_id]},
        )
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId == @c REMOVE d IN {collection}", {"c": connector_id}
        )
    for collection, key in ((CollectionNames.USERS.value, user_id), (CollectionNames.APPS.value, connector_id)):
        await graph.http_client.execute_aql(
            f"REMOVE @k IN {collection} OPTIONS {{ignoreErrors: true}}", {"k": key}
        )
    handles = [f"users/{user_id}", f"apps/{connector_id}"]
    for edge in (CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.USER_APP_RELATION.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edge} FILTER e._from IN @h OR e._to IN @h REMOVE e IN {edge}", {"h": handles}
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
        org_id, connector_id = f"org-notion-{run}", f"notion-{run}"
        now = get_epoch_timestamp_in_ms()
        member = {
            "id": f"{run}-member-key", "userId": f"{run}-member", "orgId": org_id,
            "email": f"member-{run}@notion-it.example", "fullName": "member", "isActive": True,
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }
        cleanup.push_async_callback(_remove, graph, connector_id, member["id"])

        assert await graph.batch_upsert_nodes(
            [{
                "id": connector_id, "orgId": org_id, "name": "Notion", "type": Connectors.NOTION.value,
                "appGroup": "Notion", "scope": "team", "isActive": True,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes([member], collection=CollectionNames.USERS.value)
        # The link the user sync writes for every workspace member /v1/users lists.
        assert await graph.batch_create_edges(
            [{
                "from_id": member["id"], "from_collection": CollectionNames.USERS.value,
                "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
                "syncState": "COMPLETED", "lastSyncUpdate": now,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.USER_APP_RELATION.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)

        connector = NotionConnector(
            logger=logger, data_entities_processor=processor,
            data_store_provider=GraphDataStore(logger, graph), config_service=AsyncMock(),
            connector_id=connector_id, scope="team", created_by="it",
        )
        connector.workspace_id, connector.workspace_name = WORKSPACE, "IT workspace"
        yield _Env(graph, connector, org_id, member)


def _page(page_id: str, parent: dict) -> dict:
    return {
        "object": "page", "id": page_id, "parent": parent, "url": f"https://www.notion.so/{page_id}",
        "created_time": "2026-01-01T00:00:00.000Z", "last_edited_time": "2026-01-02T00:00:00.000Z",
        "properties": {"title": {"type": "title", "title": [{"plain_text": page_id}]}},
    }


async def _sync_pages(env: _Env) -> list[str]:
    """A top-level page and one under it, saved as the page walk saves them; their search ids."""
    pages = [_page("page-top", {"type": "workspace", "workspace": True}),
             _page("page-child", {"type": "page_id", "page_id": "page-top"})]
    virtual_ids = []
    for page in pages:
        record = await env.connector._transform_to_webpage_record(page, "page")
        await env.connector.data_entities_processor.on_new_records([(record, [])])
        stored = await env.connector.data_entities_processor.get_record_by_external_id(
            env.connector.connector_id, page["id"]
        )
        # Search only returns indexed records; stand in for indexing.
        await env.graph.update_node(
            stored.id, CollectionNames.RECORDS.value,
            {"indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": f"vr-{stored.id}"},
        )
        virtual_ids.append(f"vr-{stored.id}")
    return virtual_ids


async def _found(env: _Env) -> set[str]:
    return set(await env.graph.get_accessible_virtual_record_ids(
        env.member["userId"], env.org_id, raise_on_error=True
    ))


async def test_a_workspace_member_finds_no_page(env: _Env) -> None:
    await env.connector._create_workspace_record_group()
    pages = await _sync_pages(env)
    assert not await _found(env) & set(pages), "A workspace member finds pages Notion never said they may open."


async def test_a_workspace_that_inherited_the_app_stops_granting_its_pages(env: _Env) -> None:
    """The state an earlier sync left: the workspace inherits the app, so every member finds every page."""
    await env.connector.data_entities_processor.on_new_record_groups([(RecordGroup(
        org_id=env.org_id, name="IT workspace", external_group_id=WORKSPACE,
        connector_name=Connectors.NOTION, connector_id=env.connector.connector_id,
        group_type=RecordGroupType.NOTION_WORKSPACE, permission_model=PermissionModel.RECORD_GROUP_LEVEL,
        inherit_permissions=True,
    ), [])])
    pages = await _sync_pages(env)
    assert set(pages) <= await _found(env), "the earlier state must be visible for this test to mean anything"

    await env.connector._create_workspace_record_group()

    assert not await _found(env) & set(pages), "The next sync left the workspace granting its pages to every member."
