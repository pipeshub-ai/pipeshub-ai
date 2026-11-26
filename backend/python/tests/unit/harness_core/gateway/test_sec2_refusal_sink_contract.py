"""SEC2 RS-1..RS-4: ``RefusalSinkContract`` against the in-memory sink and ``NodeRefusalSink``.

``NodeRefusalSink`` talks to a stand-in for Node's ``POST /internal/runs/{runId}/refusals`` (and ``…/egress``) that refuses what the
route refuses (``gateway-refusals.test.ts`` runs the real route): a token that is not the path's run is 401, a body
outside the route's schema 400; the outcome is the in-memory sink's.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import httpx
import pytest

from app.harness.access.refusals import NodeRefusalSink
from app.harness.journal_node.client import INTERNAL_PREFIX
from harness_core.gateway.refusal_reports import (
    EgressAllowance,
    EgressVolumeAlert,
    InMemoryRefusalSink,
    RefusalNotAccepted,
    RefusalReport,
    SecretHidden,
)
from harness_core.testing.contracts.refusal_sink import TOKENS, RefusalSinkContract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PATH = re.compile(rf"^{re.escape(INTERNAL_PREFIX)}/runs/([^/]+)/(refusals|egress|egress-volume|secret-hidden)$")
_BODY_KEYS = {"gateway", "reason", "status", "host", "param"}
_ALLOWED_KEYS = {"host", "via"}
_VOLUME_KEYS = {"kind", "direction", "bytes", "thresholdBytes", "alias"}


async def _authorize(token: str) -> str:
    return TOKENS.get(token, "")


class _NodeRoute:
    """The route's refusals: 401 for a token not this run's, 400 for a body outside its schema."""

    def __init__(self) -> None:
        self.records = InMemoryRefusalSink(authorize=_authorize)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        match = _PATH.match(request.url.path)
        if request.method != "POST" or match is None:
            return httpx.Response(404)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if TOKENS.get(token) != match.group(1):
            return httpx.Response(401)
        body = json.loads(request.content)
        if match.group(2) == "egress-volume":
            if not isinstance(body, dict) or not _VOLUME_KEYS <= set(body) <= {*_VOLUME_KEYS, "baselineBytes"}:
                return httpx.Response(400)
            alert = EgressVolumeAlert.of(
                body["kind"], body["direction"], body["bytes"], body["thresholdBytes"], body["alias"],
                baseline_bytes=body.get("baselineBytes"),
            )
            if alert is None:
                return httpx.Response(400)
            return httpx.Response(200, json={"outcome": await self.records.volume(match.group(1), token, alert)})
        if match.group(2) == "secret-hidden":
            if not isinstance(body, dict) or set(body) != {"gateway", "kind"}:
                return httpx.Response(400)
            hidden = SecretHidden.of(body["gateway"], body["kind"])
            if hidden is None:
                return httpx.Response(400)
            return httpx.Response(200, json={"outcome": await self.records.hidden(match.group(1), token, hidden)})
        if match.group(2) == "egress":
            if not isinstance(body, dict) or set(body) != _ALLOWED_KEYS:
                return httpx.Response(400)
            allowance = EgressAllowance(body["host"], body["via"])
            if EgressAllowance.of(allowance.host, allowance.via) != allowance:
                return httpx.Response(400)
            return httpx.Response(200, json={"outcome": await self.records.allowed(match.group(1), token, allowance)})
        if not isinstance(body, dict) or set(body) - _BODY_KEYS or body.get("gateway") not in ("egress", "model"):
            return httpx.Response(400)
        report = RefusalReport(body["gateway"], body.get("reason", ""), body.get("status", 0), body.get("host"), body.get("param"))
        if RefusalReport.of(report.gateway, report.reason, report.status, host=report.host, param=report.param) != report:
            return httpx.Response(400)
        return httpx.Response(200, json={"outcome": await self.records.record(match.group(1), token, report)})


class TestInMemoryRefusalSink(RefusalSinkContract):
    @pytest.fixture
    def sink(self) -> InMemoryRefusalSink:
        return InMemoryRefusalSink(authorize=_authorize)


class TestNodeRefusalSink(RefusalSinkContract):
    @pytest.fixture
    async def sink(self) -> AsyncIterator[NodeRefusalSink]:
        client = httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(_NodeRoute()))
        sink = NodeRefusalSink(base_url="http://node", http_client=client)
        yield sink
        await sink.aclose()


async def test_sec_c_11_a_report_node_refuses_for_a_stale_attempt_is_not_accepted() -> None:
    """409 ``RUN_NOT_LEASED``: the token's attempt no longer holds the run; resending cannot change that."""

    async def stale(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_RUN_NOT_LEASED"}})

    client = httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(stale))
    sink = NodeRefusalSink(base_url="http://node", http_client=client)
    try:
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.record("run-a", "t", RefusalReport("model", "unsupported_parameter", 400))
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.hidden("run-a", "t", SecretHidden("egress", "pipeshub_pat"))
    finally:
        await sink.aclose()
