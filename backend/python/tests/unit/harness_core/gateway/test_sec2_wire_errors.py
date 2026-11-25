"""SEC2 WE-1..WE-3: every answer of the model gateway is its wire's own error shape.

aiohttp answered an oversized body (``client_max_size``) and an unknown path or method in plain text, so
an engine's client saw no reason code and the gateway's refusal log never named the cause.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from harness_core.gateway.model_server import (
    MAX_BODY_BYTES,
    REFUSAL_HEADER,
    ModelGatewayServer,
)
from harness_core.testing.model_gateway import GatewayRig
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PATHS = {"chat": "/v1/chat/completions", "responses": "/v1/responses", "messages": "/v1/messages"}
SECRET = "workspace-secret-5c1d"


def _openai(body: dict[str, Any], code: str) -> None:
    assert body["error"]["code"] == code
    assert body["error"]["type"] == "invalid_request_error"


def _anthropic(body: dict[str, Any], kind: str) -> None:
    assert body["type"] == "error"
    assert body["error"]["type"] == kind


@pytest.fixture
def rig() -> GatewayRig:
    return GatewayRig()


@pytest.fixture
async def served(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> AsyncIterator[tuple[str, str]]:
    # The service runs with the root logger at INFO (app.utils.logger), where aiohttp's access log would
    # write every request path; the cases below must see what the shipped server logs at that level.
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    server = ModelGatewayServer(rig.gateway)
    port = await server.start(host="127.0.0.1", port=0)
    yield f"http://127.0.0.1:{port}", token
    await server.stop()


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_we1_an_oversized_body_is_refused_in_the_wire_envelope(
    rig: GatewayRig, served: tuple[str, str], wire: str, caplog: pytest.LogCaptureFixture
) -> None:
    url, token = served
    oversized = b'{"model": "engine-model", "pad": "' + b"x" * MAX_BODY_BYTES + b'"}'
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{url}{PATHS[wire]}", content=oversized,
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.headers[REFUSAL_HEADER] == "body_over_limit"
    if wire == "messages":
        _anthropic(response.json(), "request_too_large")
    else:
        _openai(response.json(), "body_over_limit")
    assert rig.transport.calls == []
    # WE-3: logged once, with the run's correlation (the token verified), and reported (GR-1).
    lines = [r for r in caplog.records if r.getMessage().startswith("model_gateway.refused")]
    assert len(lines) == 1 and "run-1" in caplog.text
    await rig.refusals.drain()
    assert [(r.run_id, r.report.reason, r.report.status) for r in rig.refusal_sink.records] == [
        ("run-1", "body_over_limit", 413)
    ]


UNKNOWN = [
    ("chat", "POST", "/v1/chat/completions/extra"),
    ("chat", "GET", "/v1/chat/completions"),
    ("chat", "POST", f"/v2/{SECRET}"),
    ("responses", "POST", "/v1/responses/resp_1/cancel/again"),
    ("messages", "POST", f"/v1/messages/batches/{SECRET}"),
    ("messages", "GET", "/v1/messages"),
]


@pytest.mark.parametrize(("wire", "method", "path"), UNKNOWN)
async def test_we2_an_unknown_path_or_method_is_refused_in_the_wire_envelope(
    rig: GatewayRig, served: tuple[str, str], wire: str, method: str, path: str, caplog: pytest.LogCaptureFixture
) -> None:
    url, token = served
    async with loopback_client() as client:
        response = await client.request(method, f"{url}{path}", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in (404, 405)
    assert response.headers[REFUSAL_HEADER] == "route_not_allowed"
    if wire == "messages":
        _anthropic(response.json(), "not_found_error" if response.status_code == 404 else "invalid_request_error")
        assert "route_not_allowed" in response.json()["error"]["message"]
    else:
        _openai(response.json(), "route_not_allowed")
    assert rig.transport.calls == []
    # WE-3: one line, the reason and status, never the path the caller chose.
    lines = [r for r in caplog.records if r.getMessage().startswith("model_gateway.refused")]
    assert len(lines) == 1 and "route_not_allowed" in caplog.text
    assert SECRET not in caplog.text


async def test_we2_served_routes_are_unchanged(served: tuple[str, str]) -> None:
    url, _ = served
    async with loopback_client() as client:
        health = await client.get(f"{url}/healthz")
        models = await client.get(f"{url}/v1/models")
    assert health.status_code == 200 and health.json() == {"ok": True}
    assert models.status_code == 401 and models.json()["error"]["code"] == "token_missing"
