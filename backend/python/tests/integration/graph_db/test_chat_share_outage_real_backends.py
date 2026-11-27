"""Unsharing a chat attachment while the store cannot be read, against a real Neo4j and a real ArangoDB (API1-S2).

The fault is injected at the driver call the provider's ``get_edge`` makes, so the provider's own error handling is what
runs: without ``raise_on_error`` both answer None, which read as "no READER edge" and answered 200 ``{"revoked": 0}``.
The injected error is a connection error, as a stopped store raises (Neo4j's ServiceUnavailable, aiohttp's
ClientConnectorError), so ``error_class.retry_later`` counts it as an outage.
The unshare must answer 503 instead, write nothing, and revoke once the store answers again.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import chatbot
from app.config.constants.arangodb import CollectionNames, Connectors
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

USERS, RECORDS, PERM = CollectionNames.USERS.value, CollectionNames.RECORDS.value, CollectionNames.PERMISSION.value
logger = logging.getLogger(__name__)
# Text each provider's get_edge query carries, so only that read fails.
GET_EDGE_QUERY = {"neo4j": "properties(r) AS r", "arango": "FILTER edge._from == @from_node"}


class Outage(ConnectionError):
    pass


def _driver(graph, backend: str) -> tuple[object, str]:
    return (graph.client, "execute_query") if backend == "neo4j" else (graph.http_client, "execute_aql")


@contextlib.contextmanager
def _edge_reads_fail(graph: Any, backend: str) -> Iterator[None]:  # noqa: ANN401 - a provider
    holder, name = _driver(graph, backend)
    real = getattr(holder, name)

    async def flaky(query: str, *a: Any, **k: Any) -> Any:  # noqa: ANN401 - the driver's answer
        if GET_EDGE_QUERY[backend] in query:
            raise Outage("store unreachable")
        return await real(query, *a, **k)

    setattr(holder, name, flaky)
    try:
        yield
    finally:
        setattr(holder, name, real)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    monkeypatch.setattr(chatbot, "notify_principals_changed", AsyncMock())
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, "chat_share_outage_it"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        w = {"graph": graph, "backend": request.param, "org": f"org-s2-{run}", "owner": f"owner-{run}",
             "bob": f"bob-{run}", "owner_key": f"uk-owner-{run}", "bob_key": f"uk-bob-{run}", "rec": f"rec-{run}"}

        async def _remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges([w["rec"]], RECORDS)
                await graph.delete_nodes_and_edges([w["owner_key"], w["bob_key"]], USERS)

        cleanup.push_async_callback(_remove)
        assert await graph.batch_upsert_nodes([
            {"_key": w[f"{who}_key"], "userId": w[who], "orgId": w["org"], "email": f"{w[who]}@example.com",
             "isActive": True}
            for who in ("owner", "bob")
        ], USERS)
        assert await graph.batch_upsert_nodes([{
            "_key": w["rec"], "orgId": w["org"], "recordName": "notes.txt", "recordType": "FILE",
            "externalRecordId": w["rec"], "version": 1, "origin": "UPLOAD", "connectorId": f"attachments_{w['org']}",
            "connectorName": Connectors.ATTACHMENTS.value, "mimeType": "text/plain",
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }], RECORDS)
        assert await graph.batch_create_edges([
            {"from_id": w["owner_key"], "from_collection": USERS, "to_id": w["rec"], "to_collection": RECORDS,
             "type": "USER", "role": "OWNER"},
            {"from_id": w["bob_key"], "from_collection": USERS, "to_id": w["rec"], "to_collection": RECORDS,
             "type": "USER", "role": "READER"},
        ], PERM)
        yield w


async def _bobs_edge(w) -> dict | None:
    return await w["graph"].get_edge(from_id=w["bob_key"], from_collection=USERS, to_id=w["rec"],
                                     to_collection=RECORDS, collection=PERM, raise_on_error=True)


def _unshare_request(w) -> MagicMock:
    r = MagicMock()
    r.json = AsyncMock(return_value={"userIds": [w["bob"]], "recordIds": [w["rec"]]})
    return r


@pytest.mark.asyncio
async def test_an_unshare_whose_edge_read_fails_is_a_503_and_the_retry_revokes(world) -> None:
    claims = {"orgId": world["org"], "userId": world["owner"]}
    assert (await _bobs_edge(world) or {}).get("role") == "READER"

    with _edge_reads_fail(world["graph"], world["backend"]), pytest.raises(HTTPException) as exc:
        await chatbot.revoke_attachment_permissions(_unshare_request(world), world["graph"], claims)
    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "5"}
    assert (await _bobs_edge(world) or {}).get("role") == "READER"  # nothing written on a failed read

    assert await chatbot.revoke_attachment_permissions(_unshare_request(world), world["graph"], claims) == {"revoked": 1}
    assert await _bobs_edge(world) is None
