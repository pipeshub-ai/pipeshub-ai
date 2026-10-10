"""Zendesk ticket access is granted and revoked on real Neo4j and ArangoDB.

The connector's own code turns Zendesk groups, memberships and tickets into graph
writes through the production processor; only the Zendesk API is faked. Search
access is read back with ``get_accessible_virtual_record_ids``, what a user's
search returns. Two revocations are checked, the ones Zendesk admins make most:
removing a CC from a ticket, and removing an agent from the ticket's group.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.zendesk.connector import ZendeskConnector
from app.models.entities import AppUser
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.sources.client.zendesk.zendesk import ZendeskResponse
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

ARANGO_DB = "zendesk_access_revoke_it"
DOMAIN = "zendesk-it.example"
GROUP_ID = 7
TICKET_ID = 101

logger = logging.getLogger("zendesk-access-revoke-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    connector: ZendeskConnector
    org_id: str
    users: dict[str, dict]


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


async def _remove(graph: IGraphDBProvider, connector_id: str, users: list[dict]) -> None:
    user_ids = [u["id"] for u in users]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, *user_ids]},
        )
        return
    for collection in (
        CollectionNames.RECORDS.value,
        CollectionNames.TICKETS.value,
        CollectionNames.RECORD_GROUPS.value,
        CollectionNames.GROUPS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId == @c REMOVE d IN {collection}",
            {"c": connector_id},
        )
    for collection, keys in (
        (CollectionNames.USERS.value, user_ids),
        (CollectionNames.APPS.value, [connector_id]),
    ):
        await graph.http_client.execute_aql(
            f"FOR k IN @keys REMOVE k IN {collection} OPTIONS {{ignoreErrors: true}}", {"keys": keys}
        )
    handles = [f"users/{i}" for i in user_ids] + [f"apps/{connector_id}"]
    for edge in (CollectionNames.PERMISSION.value, CollectionNames.USER_APP_RELATION.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edge} FILTER e._from IN @h OR e._to IN @h REMOVE e IN {edge}", {"h": handles}
        )


def _response(data: dict[str, Any]) -> ZendeskResponse:
    return ZendeskResponse(success=True, data=data, status_code=200)


def _connector(processor: DataSourceEntitiesProcessor, graph: IGraphDBProvider, connector_id: str) -> ZendeskConnector:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={
        "auth": {"authType": "OAUTH", "subdomain": "acme"},
        "credentials": {"access_token": "tok"},
    })
    with patch("app.connectors.sources.zendesk.connector.ZendeskApp"):
        connector = ZendeskConnector(
            logger=logger,
            data_entities_processor=processor,
            data_store_provider=GraphDataStore(logger, graph),
            config_service=config_service,
            connector_id=connector_id,
            scope="team",
            created_by="it",
        )
    connector.external_client = MagicMock()
    connector.external_client.get_subdomain.return_value = "acme"
    connector.external_client.get_client.return_value.access_token = "tok"
    connector.data_source = MagicMock()
    connector._rebuild_ticket_edges = False
    connector._fetch_public_comments = AsyncMock(
        return_value=[{"id": 1, "public": True, "body": "Printer on fire", "attachments": []}]
    )
    connector._sync_ticket_attachments = AsyncMock()
    return connector


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
        org_id, connector_id = f"org-zd-{run}", f"zendesk-{run}"
        users = {name: _user(run, name, org_id) for name in ("agent", "requester", "cc")}
        cleanup.push_async_callback(_remove, graph, connector_id, list(users.values()))

        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{
                "id": connector_id, "name": "Zendesk", "type": Connectors.ZENDESK.value,
                "appGroup": "Zendesk", "scope": "team", "isActive": True,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes(list(users.values()), collection=CollectionNames.USERS.value)
        assert await graph.batch_create_edges(
            [
                {
                    "from_id": user["id"], "from_collection": CollectionNames.USERS.value,
                    "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
                    "syncState": "COMPLETED", "lastSyncUpdate": now,
                    "createdAtTimestamp": now, "updatedAtTimestamp": now,
                }
                for user in users.values()
            ],
            collection=CollectionNames.USER_APP_RELATION.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)

        connector = _connector(processor, graph, connector_id)
        connector._user_id_to_data = {
            "1": {"id": 1, "role": "agent", "email": users["agent"]["email"], "name": "agent"},
            "2": {"id": 2, "role": "end-user", "email": users["requester"]["email"], "name": "requester"},
            "3": {"id": 3, "role": "end-user", "email": users["cc"]["email"], "name": "cc"},
        }
        yield _Env(graph, connector, org_id, users)


async def _sync_group(env: _Env, *, agent_is_member: bool) -> None:
    """The group stage of run_sync: the group, its folder and its members, rebuilt."""
    agent = AppUser(
        app_name=Connectors.ZENDESK, connector_id=env.connector.connector_id, source_user_id="1",
        email=env.users["agent"]["email"], full_name="agent", org_id=env.org_id,
    )
    env.connector.data_source.list_groups = AsyncMock(return_value=_response(
        {"groups": [{"id": GROUP_ID, "name": "Support"}], "meta": {"has_more": False}}
    ))
    env.connector.data_source.list_group_memberships = AsyncMock(return_value=_response({
        "group_memberships": [{"group_id": GROUP_ID, "user_id": 1}] if agent_is_member else [],
        "meta": {"has_more": False},
    }))
    record_groups, user_groups, complete = await env.connector._fetch_groups({"1": agent})
    assert complete
    processor = env.connector.data_entities_processor
    await processor.on_new_user_groups(user_groups, replace_members=True)
    await processor.on_new_record_groups(record_groups)


async def _sync_ticket(env: _Env, *, cc: bool, updated_at: str) -> str:
    ticket = {
        "id": TICKET_ID, "subject": "Printer on fire", "status": "open",
        "group_id": GROUP_ID, "requester_id": 2, "submitter_id": 2,
        "collaborator_ids": [3] if cc else [],
        "created_at": "2026-01-01T00:00:00Z", "updated_at": updated_at,
    }
    stored, _, failed = await env.connector._process_ticket_batch([ticket])
    assert (stored, failed) == (1, [])
    record = await env.connector.data_entities_processor.get_record_by_external_id(
        connector_id=env.connector.connector_id, external_record_id=str(TICKET_ID)
    )
    # Search only returns indexed records; stand in for indexing.
    await env.graph.update_node(
        record.id,
        CollectionNames.RECORDS.value,
        {"indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": f"vr-{record.id}"},
    )
    return f"vr-{record.id}"


async def _can_find(env: _Env, name: str, virtual_id: str) -> bool:
    reachable = await env.graph.get_accessible_virtual_record_ids(
        env.users[name]["userId"], env.org_id, raise_on_error=True
    )
    return virtual_id in reachable


async def test_removing_a_cc_revokes_only_their_access(env: _Env) -> None:
    await _sync_group(env, agent_is_member=True)
    ticket = await _sync_ticket(env, cc=True, updated_at="2026-01-02T00:00:00Z")
    for name in ("agent", "requester", "cc"):
        assert await _can_find(env, name, ticket), f"{name} cannot find the ticket after the first sync"

    await _sync_ticket(env, cc=False, updated_at="2026-01-03T00:00:00Z")

    assert not await _can_find(env, "cc", ticket), "The removed CC can still find the ticket."
    assert await _can_find(env, "requester", ticket), "Removing the CC also revoked the requester."
    assert await _can_find(env, "agent", ticket), "Removing the CC also revoked the group's agent."


async def test_removing_an_agent_from_the_group_revokes_their_access(env: _Env) -> None:
    await _sync_group(env, agent_is_member=True)
    ticket = await _sync_ticket(env, cc=False, updated_at="2026-01-02T00:00:00Z")
    assert await _can_find(env, "agent", ticket), "The group's agent cannot find the ticket."

    await _sync_group(env, agent_is_member=False)

    assert not await _can_find(env, "agent", ticket), "An agent removed from the group can still find its ticket."
    assert await _can_find(env, "requester", ticket), "Removing the agent also revoked the requester."
