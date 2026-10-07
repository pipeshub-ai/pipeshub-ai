"""A service account's chat upload is readable by the people it is meant for, on a real Neo4j and a real ArangoDB.

A service account has no user node, so its chat upload is granted to the org
with the edges ``service_account_upload_permission_edges`` builds (the same
call the upload route makes). ArangoDB validates edges against a schema, and
that schema once refused the edge's permission type, so every such upload
failed there while working on Neo4j. These write the edges for real and then
ask who can read the attachment:

* the service account itself, through the attachment check;
* a member of the org, through the ordinary record check;
* not a user of another org.

Needs Docker services, and skips cleanly when they are not reachable:

  docker run -d --name neo4j-it -p 17687:7687 -e NEO4J_AUTH=neo4j/ensure-it-pass neo4j:5.26.0
  docker run -d --name arango-it -p 18529:8529 -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12.4

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.record_access import (
    caller_can_read_virtual_record,
    service_account_upload_permission_edges,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "service_account_upload_it"

logger = logging.getLogger("service-account-upload-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    suffix: str
    org_id: str
    other_org_id: str
    member_key: str
    outsider_key: str
    record_id: str
    virtual_id: str

    @property
    def node_ids(self) -> list[str]:
        return [self.org_id, self.other_org_id, self.member_key, self.outsider_key, self.record_id]


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    await provider.ensure_schema()
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


async def _remove_test_data(env: _Env) -> None:
    graph = env.graph
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": env.node_ids},
        )
        return
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @s) OR CONTAINS(e._to, @s) REMOVE e IN {edges}",
            {"s": env.suffix},
        )
    for collection, keys in (
        (CollectionNames.RECORDS.value, [env.record_id]),
        (CollectionNames.USERS.value, [env.member_key, env.outsider_key]),
        (CollectionNames.ORGS.value, [env.org_id, env.other_org_id]),
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @keys REMOVE d IN {collection}", {"keys": keys},
        )


async def _add_member(graph: IGraphDBProvider, key: str, org_id: str) -> None:
    now = get_epoch_timestamp_in_ms()
    await graph.batch_upsert_nodes(
        [{
            "id": key, "userId": f"uid-{key}", "orgId": org_id, "email": f"{key}@example.com",
            "fullName": key, "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.USERS.value,
    )
    await graph.batch_create_edges(
        [{
            "from_id": key, "from_collection": CollectionNames.USERS.value,
            "to_id": org_id, "to_collection": CollectionNames.ORGS.value,
            "entityType": "ORGANIZATION", "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.BELONGS_TO.value,
    )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        suffix = uuid.uuid4().hex[:10]
        environment = _Env(
            graph=graph, suffix=suffix, org_id=f"org-sau-{suffix}", other_org_id=f"org2-sau-{suffix}",
            member_key=f"member-sau-{suffix}", outsider_key=f"outsider-sau-{suffix}",
            record_id=f"rec-sau-{suffix}", virtual_id=f"vrec-sau-{suffix}",
        )
        cleanup.push_async_callback(_remove_test_data, environment)

        await graph.batch_upsert_nodes(
            [{"id": org, "name": org, "accountType": "enterprise", "isActive": True}
             for org in (environment.org_id, environment.other_org_id)],
            collection=CollectionNames.ORGS.value,
        )
        await _add_member(graph, environment.member_key, environment.org_id)
        await _add_member(graph, environment.outsider_key, environment.other_org_id)

        now = get_epoch_timestamp_in_ms()
        # The record the upload route writes for a chat attachment.
        await graph.batch_upsert_nodes(
            [{
                "id": environment.record_id, "orgId": environment.org_id, "recordName": "brief.pdf",
                "externalRecordId": f"ext-{environment.record_id}", "recordType": "FILE", "origin": "UPLOAD",
                "connectorId": f"attachments_{environment.org_id}",
                "connectorName": Connectors.ATTACHMENTS.value, "isDeleted": False, "isArchived": False,
                "indexingStatus": "NOT_STARTED", "version": 1, "virtualRecordId": environment.virtual_id,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.RECORDS.value,
        )
        yield environment


async def test_a_service_account_upload_is_granted_and_readable(env: _Env) -> None:
    edges = service_account_upload_permission_edges(
        env.org_id, [env.record_id], get_epoch_timestamp_in_ms(),
    )
    # Raises when the store refuses the edge, which is how the upload failed on ArangoDB.
    await env.graph.batch_create_edges(edges, collection=CollectionNames.PERMISSION.value)

    assert await caller_can_read_virtual_record(
        env.graph, user_id=None, org_id=env.org_id, virtual_record_id=env.virtual_id,
        logger=logger, is_service_account=True,
    ), "the service account cannot read its own upload"

    member = await env.graph.check_record_access_with_details(f"uid-{env.member_key}", env.org_id, env.record_id)
    assert member is not None, "a member of the org cannot open the service account's upload"

    outsider = await env.graph.check_record_access_with_details(
        f"uid-{env.outsider_key}", env.other_org_id, env.record_id,
    )
    assert outsider is None, f"a user of another org opened the upload: {outsider}"
