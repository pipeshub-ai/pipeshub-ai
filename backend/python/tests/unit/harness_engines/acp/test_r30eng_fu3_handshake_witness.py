"""R30ENG-FU-3: an agent that connects inside ``session/new`` but lists the gateway's tools only once prompted is
witnessed by its MCP handshake.

Goose 1.57.0 over ACP opens its MCP session with the tool gateway inside ``session/new`` (``server/discover``, then
the 2025 handshake) and sends ``tools/list`` only when a prompt arrives, before each model call (on the real binary:
nothing listed in the 15 s after ``session/new``, the list came 0.16 s after ``session/prompt``). The witness counted
tool lists only, so every durable Goose job ended ``engine_unhealthy`` while its conformance lane, which handed the
session no witness, passed. A profile says ``mcp.status.witness: handshake``: the driver then reads the gateway's count
of handshakes it answered the attempt, which the Tool Gateway's MCP transport reports to an endpoint that takes it
(``NotesConnects``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import harness_engines
from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask
from harness_core.gateway.ports import NotesConnects
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.engines.contract import collect, open_rig
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    McpStatus,
    parse_acp_agents,
    parse_profile,
)
from harness_engines.acp.testing.profile import fake_entry
from tests.support.loopback_client import loopback_client
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness_engines.acp.test_e2e3_f6_gateway_probe import (
    RUNTIME,
    STATUS,
    ProbedHarness,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.gateway import RunToolEndpoint
    from harness_core.gateway.types import GatewayClaims, McpResult, McpTool
    from tests.unit.harness_engines.acp.conftest import AcpHarness

RUN = "run-r30eng-fu3"
SHIPPED = Path(harness_engines.__file__).parent / "acp_agents.json"
HANDSHAKE: dict[str, JsonValue] = {**STATUS, "status": {"api": "gateway", "runtime": RUNTIME, "witness": "handshake"}}


def _sent(harness: AcpHarness, method: str) -> list[Any]:
    return [m for f in harness.fakes.values() for c in f.connections for m in c.received if m.get("method") == method]


class LazyLister:
    """The gateway's counts for one attempt as Goose leaves them: the probe's handshake and list, then the agent's own
    handshake while it opens the session (unless ``connects`` is false), and a list only once it is prompted."""

    def __init__(self, harness: AcpHarness, *, connects: bool) -> None:
        self._harness = harness
        self._connects = connects

    def _opened(self) -> bool:
        return bool(_sent(self._harness, "session/new") or _sent(self._harness, "session/load"))

    def listed(self) -> int:
        return 1 + (1 if _sent(self._harness, "session/prompt") else 0)

    def connected(self) -> int:
        return 1 + (1 if self._connects and self._opened() else 0)


class TestProfile:
    def test_fu3_p1_a_gateway_status_may_say_a_handshake_witnesses_the_agent(self) -> None:
        profile = parse_profile(fake_entry(mcp=HANDSHAKE))
        assert profile.mcp_status == McpStatus(api="gateway", runtime=RUNTIME, witness="handshake")

    def test_fu3_p2_the_default_witness_is_a_tool_list(self) -> None:
        status = parse_profile(fake_entry(mcp=STATUS)).mcp_status
        assert status is not None and status.witness == "list"

    @pytest.mark.parametrize("witness", ["initialize", 1, None])
    def test_fu3_p3_any_other_witness_is_refused(self, witness: JsonValue) -> None:
        status: dict[str, JsonValue] = {"api": "gateway", "runtime": RUNTIME, "witness": witness}
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp={**STATUS, "status": status}))

    def test_fu3_p4_the_shipped_goose_profile_is_witnessed_by_its_handshake_and_the_others_by_a_list(self) -> None:
        agents = parse_acp_agents(SHIPPED.read_bytes())
        witness = {p.id: p.mcp_status.witness for p in agents if p.mcp_status is not None and p.mcp_status.api == "gateway"}
        assert witness == {"dsh": "list", "gemini": "list", "goose": "handshake", "qwen": "list"}


class TestSession:
    async def test_fu3_s1_an_agent_that_shook_hands_while_opening_gets_its_prompt_before_it_lists(self) -> None:
        harness = ProbedHarness(profile=parse_profile(fake_entry(mcp=HANDSHAKE)))
        rig = await open_rig(harness)
        rig.session.witness_tool_gateway(LazyLister(harness, connects=True))  # pyright: ignore[reportAttributeAccessIssue]
        assert isinstance((await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok")))[-1], TurnCompleted)
        assert len(_sent(harness, "session/prompt")) == 1
        await rig.session.close()

    async def test_fu3_s2_an_agent_whose_own_handshake_never_came_gets_no_prompt_and_is_unhealthy(self) -> None:
        harness = ProbedHarness(profile=parse_profile(fake_entry(mcp=HANDSHAKE)))
        rig = await open_rig(harness)
        rig.session.witness_tool_gateway(LazyLister(harness, connects=False))  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(EngineUnhealthy):
            _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
        assert _sent(harness, "session/new") and not _sent(harness, "session/prompt")
        await rig.session.close()

    async def test_fu3_s3_control_the_same_agent_under_a_list_witness_is_unhealthy(self) -> None:
        """What every durable Goose job met before the fix: it lists nothing until prompted."""
        harness = ProbedHarness(profile=parse_profile(fake_entry(mcp=STATUS)))
        rig = await open_rig(harness)
        rig.session.witness_tool_gateway(LazyLister(harness, connects=True))  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(EngineUnhealthy):
            _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
        await rig.session.close()


class Noting:
    """A run's endpoint that takes the transport's handshakes (``NotesConnects``), as the controller's does."""

    def __init__(self, inner: RunToolEndpoint) -> None:
        self._inner = inner
        self.binding = inner.binding
        self.connects = 0
        self.lists = 0

    def note_connect(self) -> None:
        self.connects += 1

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        self.lists += 1
        return await self._inner.list_tools(claims)

    async def call_tool(self, claims: GatewayClaims, name: str, arguments: Mapping[str, JsonValue], **kw: Any) -> McpResult:  # noqa: ANN401
        return await self._inner.call_tool(claims, name, arguments, **kw)


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


async def _post(
    server: GatewayServer, key: str, body: dict[str, Any], *, token: str | None, headers: Mapping[str, str] | None = None
) -> int:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **(headers or {})}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with loopback_client() as http:
        response = await http.post(server.url(key), headers=headers, content=json.dumps(body))
    return response.status_code


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "goose", "version": "1.57.0"}},
}
MODERN = "2026-07-28"
# What Goose's client (rmcp) probes with first: the 2026-07-28 discovery, stamped as the MCP SDK's own client stamps it.
DISCOVER = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "goose", "version": "1.57.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}
DISCOVER_HEADERS = {"mcp-protocol-version": MODERN, "mcp-method": "server/discover"}
UNVERSIONED_DISCOVER = {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


class TestTransport:
    async def test_fu3_t1_each_handshake_the_gateway_answers_is_noted_and_a_list_is_not_one(self, server: GatewayServer) -> None:
        endpoint = Noting(build_endpoint().endpoint)
        assert isinstance(endpoint, NotesConnects)
        token = server.mint.mint(BINDING)
        async with served(server, endpoint) as client:  # type: ignore[arg-type]
            assert await _post(server, client.key, INITIALIZE, token=token) == 200
            assert await _post(server, client.key, INITIALIZED, token=token) == 202
            assert (endpoint.connects, endpoint.lists) == (1, 0)
            assert await _post(server, client.key, LIST, token=token) == 200
            assert (endpoint.connects, endpoint.lists) == (1, 1)
            # A discovery the server could not answer (no protocol version) opened nothing.
            _ = await _post(server, client.key, UNVERSIONED_DISCOVER, token=token)
            assert endpoint.connects == 1
            assert await _post(server, client.key, DISCOVER, token=token, headers=DISCOVER_HEADERS) == 200
            assert endpoint.connects == 2

    async def test_fu3_t2_a_refused_handshake_is_not_noted(self, server: GatewayServer) -> None:
        endpoint = Noting(build_endpoint().endpoint)
        async with served(server, endpoint) as client:  # type: ignore[arg-type]
            assert await _post(server, client.key, INITIALIZE, token=None) == 401
        assert endpoint.connects == 0

    async def test_fu3_t3_the_sdk_clients_session_is_one_handshake(self, server: GatewayServer) -> None:
        endpoint = Noting(build_endpoint().endpoint)
        async with served(server, endpoint) as client:  # type: ignore[arg-type]
            tools = await client.list_tools(claims_for(BINDING))
        assert tools and (endpoint.connects, endpoint.lists) == (1, 1)

    async def test_fu3_t4_an_endpoint_that_takes_no_handshakes_is_served_as_before(self, server: GatewayServer) -> None:
        world = build_endpoint()
        assert not isinstance(world.endpoint, NotesConnects)
        async with served(server, world.endpoint) as client:
            assert "search" in {t.name for t in await client.list_tools(claims_for(BINDING))}
