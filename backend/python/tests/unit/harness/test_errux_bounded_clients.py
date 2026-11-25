"""EU-15 (QA17-O5): each client on the run path, its server accepting and never answering, ends at the call's bound
with a timeout naming its dependency; outside a call it is as before."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.redis.redis_vector import RedisVectorService
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from harness_core.deadline import CONNECT_TIMEOUT_S, DependencyTimeout, call_deadline
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote.client import RemoteSandboxBroker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

BOUND_S = 0.3
# Only a regression reaches it: an unbounded read then fails the test instead of hanging the suite.
GUARD_S = 5


@contextlib.contextmanager
def guarded() -> Iterator[None]:
    """Fails (``AssertionError``) a read still waiting after ``GUARD_S``: the regression, not a hang."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    handle = loop.call_later(GUARD_S, task.cancel)
    try:
        yield
    except asyncio.CancelledError as exc:
        raise AssertionError("the read was not bounded") from exc
    finally:
        handle.cancel()


@contextlib.asynccontextmanager
async def black_hole() -> AsyncIterator[int]:
    """A server that accepts every connection and never answers (toxiproxy's `timeout` toxic, in process)."""
    held: list[asyncio.StreamWriter] = []

    async def accept(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        for writer in held:
            writer.close()
        server.close()


async def test_eu_15_graph_neo4j() -> None:
    async with black_hole() as port:
        client = Neo4jClient(f"bolt://127.0.0.1:{port}", "neo4j", "pw", "neo4j", logging.getLogger("eu15"))
        with call_deadline(BOUND_S), pytest.raises(DependencyTimeout) as raised, guarded():
            _ = await client.execute_query("RETURN 1")
    assert raised.value.dependency == "graph"


async def test_eu_15_embedding_server_with_its_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async with black_hole() as port:
        monkeypatch.setenv("EMBEDDING_SERVER_URL", f"http://127.0.0.1:{port}")
        embeddings = EmbeddingServerEmbeddings(max_retries=5)
        with call_deadline(BOUND_S), pytest.raises(DependencyTimeout) as raised, guarded():
            _ = await embeddings.aembed_query("status")
    assert raised.value.dependency == "embedding"


async def test_eu_15_mcp_call_in_session() -> None:
    class Hangs:
        async def call_tool(self, name: str, arguments: dict[str, Any], **_: Any) -> Any:  # noqa: ANN401
            await asyncio.Event().wait()

    manager = MCPClientManager(MCPServerConfig.model_validate({
        "_id": "eu15", "orgId": "o", "createdBy": "u", "name": "eu15", "transport": MCPTransport.STREAMABLE_HTTP,
        "authMode": MCPAuthMode.NONE, "url": "http://127.0.0.1:9/mcp", "createdAt": 0, "updatedAt": 0,
    }))
    # An open session on main's mcp 2.x client whose server never answers.
    manager._session = SimpleNamespace(  # type: ignore[assignment]
        client=Hangs(), usable=True, stderr_path=None, lost=False, runner=SimpleNamespace(running=True),
    )
    with call_deadline(BOUND_S), pytest.raises(DependencyTimeout) as raised, guarded():
        _ = await manager.call_tool_in_session("echo", {})
    assert raised.value.dependency == "mcp"


async def test_eu_15_sandbox_broker_request_and_its_connect_bound() -> None:
    async with black_hole() as port:
        broker = RemoteSandboxBroker(f"http://127.0.0.1:{port}", StaticBearerAuth("t"))
        try:
            with call_deadline(BOUND_S), pytest.raises(DependencyTimeout) as raised, guarded():
                _ = await broker.call("GET", "/v1/health")
        finally:
            await broker.aclose()
    assert raised.value.dependency == "sandbox_broker"

    seen: list[aiohttp.ClientTimeout] = []

    class Session:
        def request(self, *args: Any, timeout: aiohttp.ClientTimeout, **kwargs: Any) -> Any:  # noqa: ANN401
            seen.append(timeout)
            raise aiohttp.ClientConnectionError("refused")

    recorded = RemoteSandboxBroker("http://broker", StaticBearerAuth("t"), session=Session())  # type: ignore[arg-type]
    with contextlib.suppress(Exception):
        _ = await recorded.call("GET", "/v1/health")
    assert seen[0].sock_connect == CONNECT_TIMEOUT_S


class _Hanging(IVectorDBService):  # type: ignore[abstract]
    async def query_nearest_points(self, collection_name: str, requests: list[Any]) -> list[Any]:
        await asyncio.Event().wait()
        return []


async def test_eu_15_every_vector_backend_s_run_path_reads_are_bounded() -> None:
    with call_deadline(BOUND_S), pytest.raises(DependencyTimeout) as raised, guarded():
        _ = await _Hanging.query_nearest_points(None, "records", [])  # type: ignore[arg-type]
    assert raised.value.dependency == "vector"
    for backend in (QdrantService, OpenSearchService, RedisVectorService):
        for name in ("get_collection", "scroll", "retrieve_points", "query_nearest_points"):
            assert hasattr(getattr(backend, name), "__wrapped__"), f"{backend.__name__}.{name}"
