"""Test-side handle on the ``mcp-fixture`` service (integration-tests/mcp_fixture).

Two addresses, because two different processes reach it:

    MCP_FIXTURE_TEST_URL       where this test process reads the call log
                               (default http://localhost:8097, the published port)
    MCP_FIXTURE_CONNECTOR_URL  the MCP endpoint PipesHub is told to connect to,
                               from inside the compose network
                               (default http://mcp-fixture:8080/mcp)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

ORDER_TOOL = "lookup_order_status"


@dataclass(frozen=True)
class McpFixture:
    test_url: str
    connector_url: str
    timeout: float = 10

    @classmethod
    def from_env(cls) -> "McpFixture":
        return cls(
            test_url=os.getenv("MCP_FIXTURE_TEST_URL", "http://localhost:8097").rstrip("/"),
            connector_url=os.getenv("MCP_FIXTURE_CONNECTOR_URL", "http://mcp-fixture:8080/mcp"),
        )

    def healthy(self) -> bool:
        try:
            return requests.get(f"{self.test_url}/__fixture__/health", timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def calls(self) -> list[dict[str, Any]]:
        resp = requests.get(f"{self.test_url}/__fixture__/calls", timeout=self.timeout)
        resp.raise_for_status()
        return list(resp.json().get("calls") or [])

    def reset(self) -> None:
        requests.post(f"{self.test_url}/__fixture__/reset", timeout=self.timeout).raise_for_status()
