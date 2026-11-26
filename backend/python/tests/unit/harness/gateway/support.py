"""A real HTTP server for the tool gateway, and a ``ToolEndpoint`` that reaches it with the MCP SDK client."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx2
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.harness.gateway.mcp import ToolGatewayApp
from harness_core.gateway import (
    BindingMismatch,
    Citation,
    GatewayTokenVerifier,
    McpResult,
    McpTool,
    TokenRefused,
)
from harness_core.gateway.arguments import FieldError
from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS
from harness_core.testing.clock import ManualClock
from harness_core.testing.gateway import TokenMint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue

    from harness_core.gateway import GatewayClaims, Progress, RunToolEndpoint
    from harness_core.telemetry.metrics import GatewayMetrics

MOUNT = "/harness/tool-gateway"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class GatewayServer:
    """The query service or worker as far as the gateway is concerned: a FastAPI app with the gateway mounted."""

    clock: ManualClock = field(default_factory=ManualClock)
    metrics: GatewayMetrics = field(default_factory=lambda: NOOP_GATEWAY_METRICS)
    mint: TokenMint = field(init=False)
    gateway: ToolGatewayApp = field(init=False)
    base: str = ""

    def __post_init__(self) -> None:
        self.mint = TokenMint(self.clock)
        self.gateway = ToolGatewayApp(GatewayTokenVerifier(self.mint.source, self.clock), metrics=self.metrics)

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[GatewayServer]:
        app = FastAPI()
        app.mount(MOUNT, self.gateway)
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        task = asyncio.ensure_future(server.serve())
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
            await asyncio.sleep(0.01)
        self.base = f"http://127.0.0.1:{port}{MOUNT}"
        try:
            yield self
        finally:
            server.should_exit = True
            await task

    def url(self, key: str) -> str:
        return f"{self.base}/{key}/mcp"


def _refusal(reason: str | None) -> Exception | None:
    """The 401 the gateway answered (its refusal header), as the refusal the core raises in process."""
    if reason is None:
        return None
    if reason == BindingMismatch.reason_code:
        return BindingMismatch(reason)
    return TokenRefused(reason, reason)


@dataclass
class McpClientEndpoint:
    """``ToolEndpoint`` over HTTP: each call opens an SDK client session with a token for the claims."""

    server: GatewayServer
    key: str
    token_for: Any = None

    def _token(self, claims: GatewayClaims) -> str:
        return self.token_for(claims) if self.token_for else self.server.mint.mint(claims.binding)

    @contextlib.asynccontextmanager
    async def session(self, claims: GatewayClaims) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self._token(claims)}", "X-Egress-Request-Id": "egr-test-1"}
        refused: list[str] = []

        async def note(response: Any) -> None:  # noqa: ANN401 - the client's response
            # mcp 2.x turns a 401 into its own error without the response, so the header is read here.
            if response.status_code == 401:
                refused.append(response.headers.get("x-gateway-refusal", ""))

        try:
            async with (
                httpx2.AsyncClient(headers=headers, timeout=30, event_hooks={"response": [note]}) as http,
                streamable_http_client(self.server.url(self.key), http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
        except BaseException:
            refusal = _refusal(refused[0] if refused else None)
            if refusal is not None:
                raise refusal from None
            raise

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        async with self.session(claims) as session:
            listed = await session.list_tools()
        return tuple(McpTool(t.name, t.description or "", t.input_schema) for t in listed.tools)

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult:
        async def on_progress(value: float, total: float | None, message: str | None) -> None:
            if progress is not None:
                await progress(value)

        async with self.session(claims) as session:
            result = await session.call_tool(
                name, dict(arguments), progress_callback=on_progress if progress else None
            )
        data: dict[str, Any] = dict(result.structured_content or {})
        text = "".join(c.text for c in result.content if c.type == "text")
        return McpResult(
            text=text,
            is_error=bool(result.is_error),
            disposition=data["disposition"],
            retryable=data["retryable"],
            invocation_id=data.get("invocationId"),
            reason_code=data.get("reasonCode"),
            interaction_id=data.get("interactionId"),
            citations=tuple(Citation(c["ref"], c["url"]) for c in data.get("citations", [])),
            field_errors=tuple(FieldError(e["field"], e["problem"], e["fix"]) for e in data.get("errors", [])),
            error_code=data.get("errorCode"),
            retry_after_s=data.get("retryAfterS"),
        )


@contextlib.asynccontextmanager
async def served(server: GatewayServer, endpoint: RunToolEndpoint) -> AsyncIterator[McpClientEndpoint]:
    async with server.gateway.serving(endpoint, endpoint.binding) as key:
        yield McpClientEndpoint(server, key)


__all__ = ["MOUNT", "GatewayServer", "McpClientEndpoint", "served"]

