"""TG-18 (C3.39): MCP protocol conformance of the tool gateway, offline, for every revision it negotiates.

Two independent checks against the real transport (``ToolGatewayApp`` under uvicorn on 127.0.0.1):

- raw JSON-RPC over Streamable HTTP, one spec rule per test: the handshake and version negotiation, the
  ``MCP-Protocol-Version`` header, JSON-RPC 2.0 shapes and error codes, notifications, ``tools/list`` and
  ``tools/call`` result shapes, the HTTP methods and headers a stateless server answers;
- the official TypeScript SDK's client (the Node app's locked ``@modelcontextprotocol/sdk``), which validates every
  answer against the SDK's own schemas. The Python SDK's client is ``test_mcp_transport``.

No MCP inspector or conformance package is vendored, and CI makes no network calls, so the raw rules are ours,
taken from the 2025-06-18 and 2025-11-25 specifications.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from mcp.server.streamable_http_manager import HANDSHAKE_PROTOCOL_VERSIONS

from harness_core.testing.contracts.tool_endpoint import BINDING, build_endpoint
from tests.unit.harness.gateway.support import GatewayServer, served

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The revisions the gateway negotiates; engines and ph speak 2025-06-18.
SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
SPOKEN = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
# 2025-11-25 "Tool names": 1-128 characters of A-Z, a-z, 0-9, _, - and .
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
NODE_APP = Path(__file__).resolve().parents[5] / "nodejs" / "apps"
TS_SDK = NODE_APP / "node_modules" / "@modelcontextprotocol" / "sdk"
TS_CLIENT = Path(__file__).with_name("ts_sdk_client.mjs")


class Wire:
    """One mounted binding, spoken to as raw HTTP."""

    def __init__(self, server: GatewayServer, key: str, http: httpx.AsyncClient) -> None:
        self.url = server.url(key)
        self.token = server.mint.mint(BINDING)
        self.http = http

    def headers(self, version: str | None = SPOKEN, **extra: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": ACCEPT, "Content-Type": "application/json"}
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        return {**headers, **extra}

    async def post(self, body: object, headers: dict[str, str] | None = None) -> httpx.Response:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return await self.http.post(self.url, headers=headers or self.headers(), content=raw)

    async def rpc(self, method: str, params: dict[str, Any] | None = None, *, rid: object = 1) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        response = await self.post(body)
        assert response.status_code == 200, response.text
        return message(response)


def message(response: httpx.Response) -> dict[str, Any]:
    """The one JSON-RPC message of a response, sent as JSON or as an SSE stream."""
    kind = response.headers.get("content-type", "")
    if kind.startswith("application/json"):
        return response.json()
    assert kind.startswith("text/event-stream"), kind
    data = [line[len("data:") :].strip() for line in response.text.splitlines() if line.startswith("data:")]
    payloads = [json.loads(d) for d in data if d]
    responses = [p for p in payloads if "result" in p or "error" in p]
    assert len(responses) == 1, payloads
    return responses[0]


def raw_result(response: httpx.Response) -> bytes:
    """The bytes the server sent for one SSE response: what a prompt cache keyed on ``tools/list`` sees."""
    lines = [line for line in response.content.splitlines() if line.startswith(b"data:")]
    assert len(lines) == 1
    return lines[0]


def initialize(version: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "tg18", "version": "1"}},
    }


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


@pytest.fixture
async def wire(server: GatewayServer) -> AsyncIterator[Wire]:
    """The mount is held by a task of its own: the MCP session manager's task group must be exited by the task that
    entered it, and fixture teardown runs in another."""
    mounted: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()

    async def hold() -> None:
        async with served(server, build_endpoint().endpoint) as client:
            mounted.set_result(client.key)
            await stop.wait()

    holder = asyncio.ensure_future(hold())
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            yield Wire(server, await mounted, http)
    finally:
        stop.set()
        await holder


def test_tg18_the_sdk_still_negotiates_every_supported_revision() -> None:
    """An SDK upgrade that drops a revision engines speak fails here, not in a customer's engine."""
    assert set(SUPPORTED) <= set(HANDSHAKE_PROTOCOL_VERSIONS)


@pytest.mark.parametrize("version", SUPPORTED)
async def test_tg18_initialize_answers_the_requested_supported_revision(wire: Wire, version: str) -> None:
    response = await wire.post(initialize(version), wire.headers(version=None))
    assert response.status_code == 200
    answer = message(response)
    assert (answer["jsonrpc"], answer["id"]) == ("2.0", 1)
    result = answer["result"]
    assert result["protocolVersion"] == version
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "pipeshub-tools"
    assert isinstance(result["serverInfo"]["version"], str)


async def test_tg18_an_unsupported_revision_is_answered_with_one_the_server_supports(wire: Wire) -> None:
    """Lifecycle: the server answers with another version it supports; the client then decides whether to go on."""
    answer = message(await wire.post(initialize("1999-01-01"), wire.headers(version=None)))
    assert answer["result"]["protocolVersion"] in SUPPORTED


async def test_tg18_an_unsupported_protocol_version_header_is_400(wire: Wire) -> None:
    response = await wire.post(
        {"jsonrpc": "2.0", "id": 7, "method": "ping"}, wire.headers(version="1999-01-01")
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


@pytest.mark.parametrize("version", SUPPORTED)
async def test_tg18_each_revision_lists_and_calls_after_the_handshake(wire: Wire, version: str) -> None:
    headers = wire.headers(version=version)
    listed = message(await wire.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers))
    assert "search" in {t["name"] for t in listed["result"]["tools"]}
    called = message(
        await wire.post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search", "arguments": {"q": "q"}}},
            headers,
        )
    )
    assert called["result"]["isError"] is False


async def test_tg18_a_notification_is_202_with_no_body(wire: Wire) -> None:
    response = await wire.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    assert response.content in (b"", b"null")


@pytest.mark.parametrize("rid", [1, "req-a", 0], ids=["int", "string", "zero"])
async def test_tg18_ping_answers_an_empty_result_echoing_the_id(wire: Wire, rid: object) -> None:
    assert await wire.rpc("ping", rid=rid) == {"jsonrpc": "2.0", "id": rid, "result": {}}


async def test_tg18_an_unknown_method_is_32601(wire: Wire) -> None:
    answer = await wire.rpc("resources/nope", {}, rid=9)
    assert (answer["id"], answer["error"]["code"]) == (9, -32601)


async def test_tg18_a_tools_call_without_a_name_is_32602(wire: Wire) -> None:
    answer = await wire.rpc("tools/call", {"arguments": {}})
    assert answer["error"]["code"] == -32602


async def test_tg18_a_body_that_is_not_json_is_a_400_parse_error(wire: Wire) -> None:
    response = await wire.post(b"{not json")
    assert response.status_code == 400
    body = response.json()
    assert (body["jsonrpc"], body["id"], body["error"]["code"]) == ("2.0", None, -32700)


async def test_tg18_a_json_rpc_batch_is_refused(wire: Wire) -> None:
    """2025-06-18 removed JSON-RPC batching."""
    response = await wire.post(b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]')
    assert response.status_code == 400
    assert "error" in response.json()


async def test_tg18_a_client_that_does_not_accept_both_json_and_sse_is_406(wire: Wire) -> None:
    response = await wire.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, wire.headers(Accept="application/json"))
    assert response.status_code == 406


async def test_tg18_a_body_that_is_not_application_json_is_refused(wire: Wire) -> None:
    response = await wire.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, wire.headers(**{"Content-Type": "text/plain"}))
    assert response.status_code in (400, 415)


async def test_tg18_tools_list_is_byte_stable_and_well_formed(wire: Wire) -> None:
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    sent = [raw_result(await wire.post(body)) for _ in range(3)]
    assert sent[0] == sent[1] == sent[2]
    result = json.loads(sent[0][len(b"data:") :])["result"]
    assert "nextCursor" not in result, "the whole grant is one page"
    names = [t["name"] for t in result["tools"]]
    assert len(names) == len(set(names))
    for tool in result["tools"]:
        assert TOOL_NAME.match(tool["name"]), tool["name"]
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool.get("description", ""), str)


async def test_tg18_a_cursor_the_server_never_issued_is_32602(wire: Wire) -> None:
    """Pagination: an invalid cursor is an error, not the first page again."""
    answer = await wire.rpc("tools/list", {"cursor": "bogus"})
    assert answer["error"]["code"] == -32602


async def test_tg18_a_result_carries_text_content_and_equivalent_structured_content(wire: Wire) -> None:
    result = (await wire.rpc("tools/call", {"name": "search", "arguments": {"q": "q3"}}))["result"]
    assert result["isError"] is False
    texts = [c["text"] for c in result["content"] if c["type"] == "text"]
    assert texts and all(isinstance(t, str) for t in texts)
    assert isinstance(result["structuredContent"], dict)
    assert result["structuredContent"]["text"] == "".join(texts), "the text fallback says what the structure does"


async def test_tg18_a_tool_failure_is_an_is_error_result_not_a_protocol_error(wire: Wire) -> None:
    answer = await wire.rpc("tools/call", {"name": "reject", "arguments": {"q": "1"}})
    assert "error" not in answer
    assert answer["result"]["isError"] is True
    assert answer["result"]["content"][0]["type"] == "text"


async def test_tg18_an_unknown_tool_is_an_is_error_result_the_model_can_read(wire: Wire) -> None:
    """Deliberate (OWD-7 "mask, don't remove"): an ungranted name answers a readable error result the model can act
    on, where the spec's example uses a JSON-RPC error that many clients surface as a hard failure."""
    answer = await wire.rpc("tools/call", {"name": "no_such_tool", "arguments": {}})
    assert answer["result"]["isError"] is True
    assert answer["result"]["structuredContent"]["reasonCode"] == "not_granted"


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
async def test_tg18_a_stateless_server_answers_other_methods_405_at_once(wire: Wire, method: str) -> None:
    """Streamable HTTP: a server that offers no SSE stream answers GET with 405 (the SDK's stateless GET would hold
    the connection open forever), and one without sessions answers DELETE with 405."""
    async with asyncio.timeout(5):
        response = await wire.http.request(method, wire.url, headers=wire.headers())
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_tg18_no_session_id_is_issued_by_a_stateless_server(wire: Wire) -> None:
    response = await wire.post(initialize(SPOKEN), wire.headers(version=None))
    assert "mcp-session-id" not in response.headers


async def test_tg18_an_unauthenticated_request_is_401_with_a_bearer_challenge(wire: Wire) -> None:
    headers = {k: v for k, v in wire.headers().items() if k != "Authorization"}
    response = await wire.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer ")


async def test_tg18_an_unknown_mount_is_404_without_an_auth_challenge(wire: Wire) -> None:
    response = await wire.http.post(
        wire.url.replace("/mcp", "x/mcp"), headers=wire.headers(), content=b"{}"
    )
    assert response.status_code == 404
    assert "www-authenticate" not in response.headers


def _node_missing() -> str | None:
    if shutil.which("node") is None:
        return "node is not installed"
    if not (TS_SDK / "dist" / "esm" / "client" / "index.js").exists():
        return "backend/nodejs/apps/node_modules/@modelcontextprotocol/sdk missing (run npm ci)"
    return None


async def test_tg18_the_official_typescript_sdk_client_accepts_every_answer(wire: Wire) -> None:
    missing = _node_missing()
    if missing is not None:
        if os.environ.get("PXH_REQUIRE_NODE") == "1":
            pytest.fail(f"PXH_REQUIRE_NODE=1 but {missing}")
        pytest.skip(missing)
    node = shutil.which("node")
    assert node is not None
    process = await asyncio.create_subprocess_exec(
        node, str(TS_CLIENT), str(TS_SDK), wire.url, wire.token,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(25):
        out, err = await process.communicate()
    assert process.returncode == 0, err.decode()[-2000:]
    seen = json.loads(out)
    assert seen["protocolVersion"] in SUPPORTED
    assert seen["server"]["name"] == "pipeshub-tools"
    assert "tools" in seen["capabilities"]
    assert "search" in seen["tools"]
    assert seen["ok"]["isError"] is False and seen["ok"]["structured"]["text"] == seen["ok"]["text"]
    assert seen["failed"]["isError"] is True


def test_tg18_ci_runs_this_suite_with_node_required() -> None:
    """The TypeScript half cannot silently skip in CI, and a change to the gateway or its tests triggers the run."""
    import yaml

    workflow = yaml.safe_load((NODE_APP.parents[2] / ".github" / "workflows" / "contracts-codegen.yml").read_text())
    step = next(s for s in workflow["jobs"]["codegen"]["steps"] if "TG-18" in s.get("name", ""))
    assert step["env"]["PXH_REQUIRE_NODE"] == "1"
    assert Path(__file__).name in step["run"]
    names = [s.get("name", "") for s in workflow["jobs"]["codegen"]["steps"]]
    assert names.index("Install Node dependencies") < names.index(step["name"])
    paths = workflow[True]["pull_request"]["paths"]
    assert {"backend/python/app/harness/**", "backend/python/tests/unit/harness/gateway/**"} <= set(paths)
