"""The worker's dependencies in the degraded scenarios (QA-17): each reached by its production client.

``scenario.json``'s ``dependencies`` names each endpoint (a toxiproxy proxy in
front of the real or scripted server). A tool here is the thinnest call through
the client a PipesHub run's tool makes: whatever the client raises or returns
when its dependency is down, slow or gone is what the run's kernel gets.
``model_port`` is the run's model as ``app/harness/assembly/model.py`` builds
it, over the OpenAI chat-completions transport.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from app.harness.assembly.model import ClassifiedModelPort
from harness_core.deadline import call_deadline
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.models.stream_watch import StreamTimeouts
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.openai import OpenAITransport
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.types import LeaseRef
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeContractEnv

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.effects.types import ToolContext
    from harness_core.testing.durable import FunctionToolPort
    from pydantic import JsonValue

    Tool = Callable[[Mapping[str, JsonValue], ToolContext], Awaitable[ToolExecution]]

LOG = logging.getLogger("fault.dependencies")
# The rig's tools run on FunctionToolPort, not the product's executor, so each opens the call deadline the executor
# would (its wall bound); the value is the rig's, so a black-holed store ends a scenario in seconds (ERR-UX, IX-DD-*-bh).
RIG_CALL_S = 8.0
# The product's per-read client timeout (app/utils/aimodels.py DEFAULT_LLM_TIMEOUT_S).
LLM_READ_TIMEOUT_S = 360.0


def model_port(config: dict[str, Any]) -> ClassifiedModelPort:
    """The run's model over the wire. The SDK's own retries are off, so every 429 or 5xx reaches the kernel."""
    transport = OpenAITransport(
        api_key="fault-suite-key", model="fault-model", base_url=config["url"],
        timeout=LLM_READ_TIMEOUT_S, max_retries=0,
    )
    timeouts = StreamTimeouts(**config["streamTimeouts"]) if "streamTimeouts" in config else StreamTimeouts()
    return ClassifiedModelPort(
        StreamingModelPort(TransportModel(transport, timeouts=timeouts), provider="openAI", transport="direct")
    )


class Clients:
    """One client per dependency for the worker's life, opened on first use as a run's tool would."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._graph: Any = None
        self._vector: Any = None
        self._embeddings: Any = None
        self._mcp: Any = None
        self._broker: RemoteSandboxBroker | None = None

    async def graph(self) -> Any:  # noqa: ANN401 - the product client, imported on first use
        if self._graph is None:
            from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

            os.environ.update(NEO4J_URI=self.config["graph"]["uri"], NEO4J_PASSWORD=self.config["graph"]["password"])
            provider = Neo4jProvider(LOG, None)  # pyright: ignore[reportArgumentType] - connect() reads env only
            if not await provider.connect():
                raise ConnectionError("graph store did not connect")
            self._graph = provider
        return self._graph

    async def vector(self) -> Any:  # noqa: ANN401 - the product client, imported on first use
        if self._vector is None:
            from app.services.vector_db.qdrant.config import QdrantConfig
            from app.services.vector_db.qdrant.qdrant import QdrantService

            host, port = self.config["vector"]["host"], int(self.config["vector"]["port"])
            self._vector = await QdrantService.create(QdrantConfig(host=host, port=port, prefer_grpc=False))
        return self._vector

    def embeddings(self) -> Any:  # noqa: ANN401 - the product client, imported on first use
        if self._embeddings is None:
            from app.utils.embedding_server_client import EmbeddingServerEmbeddings

            os.environ["EMBEDDING_SERVER_URL"] = self.config["embedding"]["url"]
            self._embeddings = EmbeddingServerEmbeddings()
        return self._embeddings

    async def mcp(self) -> Any:  # noqa: ANN401 - the product client, imported on first use
        if self._mcp is None:
            from app.agents.mcp.client import MCPClientManager
            from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

            config = MCPServerConfig.model_validate({
                "_id": "fault-mcp", "orgId": "fault", "createdBy": "fault", "name": "fault-mcp",
                "transport": MCPTransport.STREAMABLE_HTTP, "authMode": MCPAuthMode.NONE,
                "url": self.config["mcp"]["url"], "createdAt": 0, "updatedAt": 0,
            })
            manager = MCPClientManager(config)
            await manager.open()
            self._mcp = manager
        return self._mcp

    async def broker(self) -> RemoteSandboxBroker:
        if self._broker is None:
            sandbox = self.config["sandbox"]
            self._broker = await RemoteSandboxBroker.open(sandbox["url"], StaticBearerAuth(sandbox["token"]))
        return self._broker


def add_dependency_tools(port: FunctionToolPort, clients: Clients) -> None:
    """Reads, one per dependency, each answering what it read."""

    async def graph_lookup(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        graph = await clients.graph()
        rows = await graph.client.execute_query("RETURN 1 AS one")
        return ToolExecution(status="ok", content={"rows": len(rows)})

    async def vector_search(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        exists = await (await clients.vector()).collection_exists("fault-records")
        return ToolExecution(status="ok", content={"collection": exists})

    async def embed_query(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        vector = await clients.embeddings().aembed_query(str(args.get("q", "status")))
        return ToolExecution(status="ok", content={"dimensions": len(vector)})

    async def mcp_echo(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        result = await (await clients.mcp()).call_tool_in_session("echo", {"text": str(args.get("q", "status"))})
        return ToolExecution(status="ok", content={"echoed": bool(result)})

    async def sandbox_exec(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        broker = await clients.broker()
        env = FakeContractEnv(ManualClock())
        handle = await broker.acquire(env.spec(), lease=LeaseRef(ctx.run_id, 1), idempotency_key=ctx.idempotency_key)
        try:
            process = await handle.exec(env.argv_print("ran", "", 0), env={})
            code = await process.wait(timeout_s=60)
        finally:
            await broker.release(handle)
        return ToolExecution(status="ok", content={"exitCode": code})

    for name, fn in (("graph_lookup", graph_lookup), ("vector_search", vector_search), ("embed_query", embed_query),
                     ("mcp_echo", mcp_echo), ("sandbox_exec", sandbox_exec)):
        port.add(name, _within_call_deadline(fn), tags=[("category", "read")])


def _within_call_deadline(fn: Tool) -> Tool:
    async def bounded(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        with call_deadline(RIG_CALL_S):
            return await fn(args, ctx)

    return bounded


__all__ = ["Clients", "add_dependency_tools", "model_port"]
