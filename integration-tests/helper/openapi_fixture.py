"""Test-side handle on the ``openapi-fixture`` service (integration-tests/openapi_fixture).

Two addresses, because two different processes reach it:

    OPENAPI_FIXTURE_TEST_URL       where this test process reads the call log
                                   (default http://localhost:8098, the published port)
    OPENAPI_FIXTURE_CONNECTOR_URL  the base URL PipesHub is told to call, from inside
                                   the compose network (default http://openapi-fixture:8080)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

DEMO_API_KEY = "acme-demo-key"
DEMO_BEARER_TOKEN = "acme-demo-token"
DEMO_BASIC = ("acme", "acme-demo-password")
DEMO_CLIENT = ("acme-client", "acme-client-secret")


@dataclass(frozen=True)
class OpenApiFixture:
    test_url: str
    connector_url: str
    timeout: float = 10

    @classmethod
    def from_env(cls) -> "OpenApiFixture":
        return cls(
            test_url=os.getenv("OPENAPI_FIXTURE_TEST_URL", "http://localhost:8098").rstrip("/"),
            connector_url=os.getenv("OPENAPI_FIXTURE_CONNECTOR_URL", "http://openapi-fixture:8080").rstrip("/"),
        )

    def healthy(self) -> bool:
        try:
            return requests.get(f"{self.test_url}/__fixture__/health", timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def spec(self) -> bytes:
        resp = requests.get(f"{self.test_url}/openapi.yaml", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def calls(self) -> list[dict[str, Any]]:
        resp = requests.get(f"{self.test_url}/__fixture__/calls", timeout=self.timeout)
        resp.raise_for_status()
        return list(resp.json().get("calls") or [])

    def reset(self) -> None:
        requests.post(f"{self.test_url}/__fixture__/reset", timeout=self.timeout).raise_for_status()

    def expire_tokens(self) -> None:
        requests.post(f"{self.test_url}/__fixture__/expire-tokens", timeout=self.timeout).raise_for_status()
