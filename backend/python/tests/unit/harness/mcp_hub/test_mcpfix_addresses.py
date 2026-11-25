"""MCPFIX F2: which addresses an org's MCP server may have, with the hub on and off.

An HTTP or SSE MCP server is dialled by a shared service process in every mode (the connectors service, the query
service, the worker). Main's URL guard refuses loopback, link-local, unspecified and metadata addresses in every
mode and pins each connection to the addresses it checked. With the hub on a private address also follows the
deployment's switch (``PIPESHUB_BLOCK_PRIVATE_ADDRESSES``) and its network list (``MCP_PRIVATE_NETWORKS``).
"""

from __future__ import annotations

import socket

import pytest

from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.service import instance_config_from_dict
from app.agents.mcp.url_guard import assert_mcp_url_allowed
from app.harness.mcp_hub.ssrf import address_rule
from tests.unit.harness.mcp_hub.support import http_instance
from tests.unit.harness.mcp_hub.test_ssrf import PUBLIC, Resolver

NEVER = [
    "http://169.254.169.254/latest/meta-data",
    "http://[fd00:ec2::254]/latest",
    "http://100.100.100.200/latest/meta-data",
    "http://168.63.129.16/machine",
    "http://0.0.0.0:8080/mcp",
    "http://[::]/mcp",
    "http://metadata.google.internal/computeMetadata/v1",
    "http://[fe80::1]/mcp",
]
LOOPBACK = ["http://127.0.0.1:8080/mcp", "http://[::1]/mcp", "http://localhost:3000/mcp"]
PRIVATE = ["http://10.0.0.7/mcp", "http://172.16.0.1/mcp", "https://192.168.1.5/mcp", "http://[fd12:3456::1]/mcp"]


async def allowed(url: str) -> object:
    # An admin's instance: private networks follow the deployment (main's MCP_ALLOW_PRIVATE_NETWORK_URLS, on).
    return await assert_mcp_url_allowed(url, allow_private=True)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HUB", "true")
    monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
    monkeypatch.delenv("MCP_PRIVATE_NETWORKS", raising=False)


@pytest.fixture
def hub_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HUB", raising=False)
    monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)


@pytest.mark.usefixtures("hub")
@pytest.mark.parametrize("url", PRIVATE)
async def test_f2_1_a_private_address_is_allowed_and_pinned_where_the_deployment_trusts_its_admins(url: str) -> None:
    target = await allowed(url)
    assert str(target.pinned_address) in url  # type: ignore[attr-defined]


@pytest.mark.usefixtures("hub")
async def test_f2_1_a_name_resolving_to_a_private_address_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", Resolver(["192.168.1.5"]))
    target = await allowed("https://mcp.corp.local/mcp")
    assert str(target.pinned_address) == "192.168.1.5"  # type: ignore[attr-defined]


@pytest.mark.parametrize("mode", ["hub", "hub_off"])
@pytest.mark.parametrize("url", [*NEVER, *LOOPBACK, "file:///etc/passwd"])
async def test_f2_2_loopback_link_local_and_metadata_are_refused_in_both_modes(
    request: pytest.FixtureRequest, mode: str, url: str
) -> None:
    request.getfixturevalue(mode)
    with pytest.raises(MCPUrlBlockedError):
        _ = await allowed(url)


@pytest.mark.usefixtures("hub")
@pytest.mark.parametrize("url", PRIVATE)
async def test_f2_3_a_shared_deployment_refuses_private_addresses(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    with pytest.raises(MCPUrlBlockedError):
        _ = await allowed(url)
    # Carrier-grade NAT (Tailscale) stays allowed, as before.
    assert await allowed("http://100.64.3.4/mcp") is not None


@pytest.mark.usefixtures("hub")
async def test_f2_4_a_shared_deployment_allows_the_networks_its_operator_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    monkeypatch.setenv("MCP_PRIVATE_NETWORKS", "10.20.0.0/16, fd12:3456::/32")
    assert await allowed("http://10.20.3.4/mcp") is not None
    assert await allowed("http://[fd12:3456::9]/mcp") is not None
    for refused in ("http://10.21.0.1/mcp", "http://192.168.1.5/mcp", "http://127.0.0.1/mcp", "http://169.254.169.254/"):
        with pytest.raises(MCPUrlBlockedError):
            _ = await allowed(refused)


@pytest.mark.usefixtures("hub")
@pytest.mark.parametrize("answer", [[PUBLIC, "169.254.169.254"], ["10.0.0.7", "127.0.0.1"]])
async def test_f2_5_one_refused_address_among_a_names_answers_refuses_the_url(
    monkeypatch: pytest.MonkeyPatch, answer: list[str]
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", Resolver(answer))
    with pytest.raises(MCPUrlBlockedError):
        _ = await allowed("https://mcp.example.test/mcp")


@pytest.mark.usefixtures("hub")
@pytest.mark.parametrize("value", ["10.20.0.0/33", "corp-network", "10.20.0.0/16,,nope"])
def test_f2_8_a_malformed_network_list_refuses_rather_than_guessing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    monkeypatch.setenv("MCP_PRIVATE_NETWORKS", value)
    with pytest.raises(ValueError, match="MCP_PRIVATE_NETWORKS"):
        _ = address_rule()


@pytest.mark.parametrize("hub_flag", ["true", "false"])
@pytest.mark.parametrize("url", ["http://169.254.169.254/mcp", "http://metadata.google.internal/mcp"])
async def test_f2_10_the_client_refuses_before_it_connects_in_both_modes(
    monkeypatch: pytest.MonkeyPatch, hub_flag: str, url: str
) -> None:
    monkeypatch.setenv("MCP_HUB", hub_flag)
    manager = MCPClientManager(instance_config_from_dict(http_instance(url=url)), headers={})
    with pytest.raises(MCPUrlBlockedError):
        async with manager.connect():
            pass


@pytest.mark.usefixtures("hub_off")
async def test_f2_12_with_the_hub_off_a_name_resolving_to_metadata_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", Resolver(["169.254.169.254"]))
    with pytest.raises(MCPUrlBlockedError):
        _ = await allowed("http://innocent.example.test/mcp")


@pytest.mark.usefixtures("hub_off")
@pytest.mark.parametrize("url", [*PRIVATE, f"http://{PUBLIC}/mcp"])
async def test_f2_13_with_the_hub_off_private_and_public_addresses_connect_pinned(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    # The hub's private-address switch does not apply with the hub off.
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    target = await allowed(url)
    assert target.addresses  # type: ignore[attr-defined]
