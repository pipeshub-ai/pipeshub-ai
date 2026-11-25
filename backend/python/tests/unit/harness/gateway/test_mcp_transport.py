"""MT-1..MT-8: the MCP Streamable HTTP transport, over real HTTP, with the MCP SDK's own client."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.gateway import BindingMismatch, TokenRefused
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    EARLIER_ATTEMPT,
    SECRET,
    ScriptedHold,
    ToolEndpointContract,
    build_endpoint,
    claims_for,
)
from tests.support.loopback_client import loopback_client
from tests.unit.harness.gateway.support import GatewayServer, McpClientEndpoint, served

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

    from harness_core.gateway import RunToolEndpoint, ToolEndpoint


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


class OverMcp:
    """Each mount is held open by a task of its own: the MCP session manager's task group
    must be exited by the task that entered it, and fixture teardown runs in another."""

    def __init__(self, server: GatewayServer) -> None:
        self._server = server
        self._open: list[tuple[asyncio.Event, asyncio.Task[None]]] = []

    async def serve(self, endpoint: RunToolEndpoint) -> ToolEndpoint:
        ready: asyncio.Future[McpClientEndpoint] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def hold() -> None:
            async with served(self._server, endpoint) as client:
                ready.set_result(client)
                await stop.wait()

        self._open.append((stop, asyncio.ensure_future(hold())))
        return await ready

    async def close(self) -> None:
        for stop, task in self._open:
            stop.set()
            await task


class TestToolEndpointOverMcp(ToolEndpointContract):
    """MT-6: the endpoint contract, through the transport and the SDK client."""

    @pytest.fixture
    async def endpoint_harness(self, server: GatewayServer) -> AsyncIterator[OverMcp]:
        harness = OverMcp(server)
        yield harness
        await harness.close()


async def test_mt1_mt2_list_and_call_through_the_sdk_client(server: GatewayServer) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        tools = await client.list_tools(claims_for(BINDING))
        assert "search" in {t.name for t in tools} and "delete" not in {t.name for t in tools}
        result = await client.call_tool(claims_for(BINDING), "search", {"q": "q3"})
    assert (result.disposition, result.is_error) == ("executed", False)
    assert [c.ref for c in result.citations] == ["ref1", "ref2"]
    assert world.runs == ["search"]


async def test_mt2_structured_content_carries_the_result_text(server: GatewayServer) -> None:
    """E8 FB-7: Codex hands the model a result's structured content instead of its text."""
    world = build_endpoint()
    async with served(server, world.endpoint) as client, client.session(claims_for(BINDING)) as session:
        result = await session.call_tool("search", {"q": "q3"})
    text = "".join(c.text for c in result.content if c.type == "text")
    assert text
    assert (result.structured_content or {})["text"] == text


async def _post(server: GatewayServer, key: str, authorization: str | None) -> httpx.Response:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with loopback_client() as http:
        return await http.post(server.url(key), headers=headers, json=body)


@pytest.mark.parametrize(
    ("authorization", "reason"),
    [
        (None, "token_missing"),
        ("Basic dXNlcjpwYXNz", "token_missing"),
        ("Bearer not-a-token", "token_malformed"),
    ],
    ids=["no-token", "not-bearer", "malformed"],
)
async def test_mt3_requests_without_a_gateway_token_are_401(
    server: GatewayServer, authorization: str | None, reason: str
) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        response = await _post(server, client.key, authorization)
    assert response.status_code == 401
    assert response.json()["error"]["reason"] == reason
    assert world.runs == []


async def test_mt3_a_user_session_jwt_is_not_a_gateway_token(server: GatewayServer) -> None:
    """A user's JWT (HS256, the app's issuer) is refused like any foreign token."""
    import jwt as pyjwt

    user_jwt = pyjwt.encode({"userId": "u1", "orgId": "org-1"}, "jwt-secret", algorithm="HS256")
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        response = await _post(server, client.key, f"Bearer {user_jwt}")
    assert response.status_code == 401
    assert response.json()["error"]["reason"] == "token_algorithm"


@pytest.mark.parametrize(
    "other",
    [dataclasses.replace(BINDING, run_id="run-9"), EARLIER_ATTEMPT],
    ids=["another-run", "earlier-attempt"],
)
async def test_mt4_a_token_for_another_binding_is_401(server: GatewayServer, other: object) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        with pytest.raises(BindingMismatch):
            await client.list_tools(claims_for(other))  # type: ignore[arg-type]
        response = await _post(server, client.key, f"Bearer {server.mint.mint(other)}")  # type: ignore[arg-type]
    assert response.status_code == 401
    assert world.runs == []


async def test_mt4_an_expired_token_is_401(server: GatewayServer) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        token = server.mint.mint(BINDING)
        server.clock.advance(601)
        stale = McpClientEndpoint(server, client.key, token_for=lambda _: token)
        with pytest.raises(TokenRefused) as refused:
            await stale.list_tools(claims_for(BINDING))
    assert refused.value.reason_code == "token_expired"


async def test_mt5_a_held_call_reports_progress_to_the_client(server: GatewayServer) -> None:
    world = build_endpoint(approvals={"transfer": "ask"}, hold=ScriptedHold("approved", after_s=0.3))
    ticks: list[float] = []

    async def progress(elapsed: float) -> None:
        ticks.append(elapsed)

    async with served(server, world.endpoint) as client:
        result = await client.call_tool(claims_for(BINDING), "transfer", {"q": "1"}, progress=progress)
    assert result.disposition == "executed"
    assert ticks, "the client saw no progress notification"


async def test_mt7_each_binding_reaches_only_its_own_endpoint(server: GatewayServer) -> None:
    first, second = build_endpoint(), build_endpoint()
    other = dataclasses.replace(BINDING, run_id="run-2")
    second.endpoint._binding = other
    async with served(server, first.endpoint) as a, served(server, second.endpoint) as b:
        assert (await a.call_tool(claims_for(BINDING), "lookup", {"q": "1"})).disposition == "executed"
        assert (await b.call_tool(claims_for(other), "lookup", {"q": "2"})).disposition == "executed"
        with pytest.raises(BindingMismatch):
            await b.call_tool(claims_for(BINDING), "lookup", {"q": "3"})
        key = a.key
    assert (first.runs, second.runs) == (["lookup"], ["lookup"])
    response = await _post(server, key, f"Bearer {server.mint.mint(BINDING)}")
    assert response.status_code == 404


async def test_mt8_a_forbidden_pattern_is_an_error_result_not_an_http_error(
    server: GatewayServer, caplog: pytest.LogCaptureFixture
) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        with caplog.at_level(logging.INFO):
            result = await client.call_tool(claims_for(BINDING), "create", {"q": SECRET})
    assert (result.is_error, result.reason_code) == (True, "forbidden_arguments")
    assert world.runs == []
    assert SECRET not in caplog.text


async def test_refusals_log_the_proxy_request_id_once(server: GatewayServer, caplog: pytest.LogCaptureFixture) -> None:
    world = build_endpoint()
    async with served(server, world.endpoint) as client:
        with caplog.at_level(logging.INFO, logger="app.harness.gateway"):
            async with loopback_client() as http:
                await http.post(
                    server.url(client.key),
                    headers={"Authorization": "Bearer x.y.z", "X-Egress-Request-Id": "egr-42"},
                    json={},
                )
    refused = [r for r in caplog.records if r.__dict__.get("harness_event") == "gateway.refused"]
    assert len(refused) == 1
    assert refused[0].__dict__["harness"]["proxyRequestId"] == "egr-42"
    assert "x.y.z" not in caplog.text
