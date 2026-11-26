"""SG-1, SG-2, SG-6: with the hub on (P2.8), an org-configured MCP URL is resolved once and checked against the
deployment's rule. Pinning, a request for another host and the environment proxy are main's URL guard
(``tests/unit/agents/mcp/test_url_guard.py``: ``TestGuardedTransport``, ``TestBehindAnEgressProxy``)."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import assert_mcp_url_allowed

PUBLIC = "93.184.216.34"


class Resolver:
    """``socket.getaddrinfo`` answering from a script, one answer per call (the last repeats)."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, host: str, *_: Any, **__: Any) -> list[Any]:  # noqa: ANN401 - getaddrinfo's own shape
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in answer
        ]


@pytest.fixture(autouse=True)
def shared_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    # A deployment shared by organisations: private addresses are refused there (MCPFIX F2; a self-hosted
    # deployment allows them, test_mcpfix_addresses.py).
    monkeypatch.setenv("MCP_HUB", "true")
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    monkeypatch.delenv("MCP_PRIVATE_NETWORKS", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/mcp",
        "http://172.16.0.1/mcp",
        "https://192.168.1.1/mcp",
        "http://127.0.0.1:8080/mcp",
        "http://[::1]/mcp",
        "http://[fd00::1]/mcp",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://168.63.129.16/mcp",
        "http://localhost:3000/mcp",
        "file:///etc/passwd",
    ],
)
async def test_sg1_private_loopback_and_metadata_addresses_are_refused(url: str) -> None:
    with pytest.raises(MCPUrlBlockedError):
        _ = await assert_mcp_url_allowed(url, allow_private=True)


@pytest.mark.parametrize("answer", [["10.0.0.5"], [PUBLIC, "192.168.0.10"], ["169.254.169.254"]])
async def test_sg2_a_name_resolving_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch, answer: list[str]
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", Resolver(answer))
    with pytest.raises(MCPUrlBlockedError):
        _ = await assert_mcp_url_allowed("https://mcp.example.test/mcp", allow_private=True)


@pytest.mark.parametrize("url", ["http://100.64.0.1/mcp", f"https://{PUBLIC}/mcp"])
async def test_sg6_cgnat_and_public_addresses_are_pinned(url: str) -> None:
    target = await assert_mcp_url_allowed(url, allow_private=True)
    assert str(target.pinned_address) == httpx.URL(url).host
