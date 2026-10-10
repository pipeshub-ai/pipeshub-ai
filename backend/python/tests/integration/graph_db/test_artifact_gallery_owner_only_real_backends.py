"""The artifacts gallery lists only the caller's own artifacts, on a real Neo4j and a real ArangoDB.

Since PH-07 a chat participant reads another participant's artifact through the chat-content
PDP, never through a graph edge. A READER edge left by the removed chat grant path (or any
other non-owner USER edge) must not surface the artifact in the gallery list or detail.

  PCC_GATE=1 PCC_NEO4J_URI=... PCC_NEO4J_PASSWORD=... PCC_ARANGO_URL=... PCC_ARANGO_PASSWORD=... \\
    pytest tests/integration/graph_db/test_artifact_gallery_owner_only_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import arango_env as load_arango_env
from ._backends import neo4j_env as load_neo4j_env
from ._backends import unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "artifact_gallery_it"

logger = logging.getLogger("artifact-gallery-it")


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, str]]:
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    if request.param == "neo4j":
        env = load_neo4j_env()
        monkeypatch.setenv("NEO4J_URI", env.uri)
        monkeypatch.setenv("NEO4J_USERNAME", env.user)
        monkeypatch.setenv("NEO4J_PASSWORD", env.password)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider = Neo4jProvider(logger, MagicMock())
        try:
            if not await asyncio.wait_for(provider.connect(), timeout=60):
                raise ConnectionError("connect returned False")
        except Exception as exc:
            unavailable(f"Neo4j not available at {env.uri}: {exc}")
        await provider.ensure_schema()
        try:
            yield provider, org_id
        finally:
            await provider.client.execute_query(
                "MATCH (n) WHERE n.orgId = $org OR n.id STARTS WITH $org DETACH DELETE n", parameters={"org": org_id}
            )
            await provider.disconnect()
        return

    env = load_arango_env()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": env.url, "username": env.user, "password": env.password, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        unavailable(f"ArangoDB not available at {env.url}: {exc}")
    try:
        yield provider, org_id
    finally:
        for edges in ("permission", "isOfType"):
            await provider.http_client.execute_aql(
                f"FOR e IN {edges} FILTER CONTAINS(e._from, @k) OR CONTAINS(e._to, @k) REMOVE e IN {edges}",
                {"k": f"{org_id}-"},
            )
        for collection in ("users", "records", "artifacts"):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id}
            )


async def _seed(provider, org: str) -> dict[str, str]:
    key = lambda name: f"{org}-{name}"  # noqa: E731
    ids = {"creator": key("creator"), "reader": key("reader"), "artifact": key("artifact")}
    await provider.batch_upsert_nodes(
        [
            {"id": ids[who], "orgId": org, "userId": ids[who], "email": f"{ids[who]}@example.com", "isActive": True}
            for who in ("creator", "reader")
        ],
        CollectionNames.USERS.value,
    )
    await provider.batch_upsert_nodes(
        [{
            "id": ids["artifact"], "orgId": org, "recordName": "chart.png", "recordType": "ARTIFACT",
            "origin": "UPLOAD", "connectorName": "CODING_SANDBOX", "connectorId": key("conn"),
            "externalRecordId": ids["artifact"], "isDeleted": False, "mimeType": "image/png",
            "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }],
        CollectionNames.RECORDS.value,
    )
    await provider.batch_upsert_nodes(
        [{
            "id": ids["artifact"], "orgId": org, "name": "chart.png", "artifactType": "CHART",
            "visibility": "VISIBLE", "isTemporary": False, "conversationId": key("chat"), "runId": "r1",
        }],
        CollectionNames.ARTIFACTS.value,
    )
    await provider.batch_create_edges(
        [{
            "from_id": ids["artifact"], "from_collection": CollectionNames.RECORDS.value,
            "to_id": ids["artifact"], "to_collection": CollectionNames.ARTIFACTS.value,
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }],
        CollectionNames.IS_OF_TYPE.value,
    )
    await provider.batch_create_edges(
        [
            {
                "from_id": ids[who], "from_collection": CollectionNames.USERS.value,
                "to_id": ids["artifact"], "to_collection": CollectionNames.RECORDS.value,
                "type": "USER", "role": role, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }
            for who, role in (("creator", "OWNER"), ("reader", "READER"))
        ],
        CollectionNames.PERMISSION.value,
    )
    return ids


async def _list(provider, user_key: str, org: str) -> tuple[list[dict], int]:
    return await provider.list_accessible_artifacts(
        user_id=user_key, org_id=org, skip=0, limit=50, search=None, artifact_types=None,
        conversation_id=None, date_from=None, date_to=None, sort_by="createdAtTimestamp", sort_order="desc",
    )


async def test_the_creator_sees_the_artifact_in_the_gallery(backend) -> None:
    provider, org = backend
    ids = await _seed(provider, org)

    rows, total = await _list(provider, ids["creator"], org)
    assert (total, [r["id"] for r in rows]) == (1, [ids["artifact"]])
    detail = await provider.get_artifact_detail(ids["creator"], org, ids["artifact"])
    assert detail is not None and detail["artifactDoc"]["conversationId"] == f"{org}-chat"


async def test_a_reader_edge_from_a_chat_grant_does_not_list_the_artifact(backend) -> None:
    provider, org = backend
    ids = await _seed(provider, org)

    assert await _list(provider, ids["reader"], org) == ([], 0)
    assert await provider.get_artifact_detail(ids["reader"], org, ids["artifact"]) is None
