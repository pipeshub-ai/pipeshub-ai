"""CPD6-42: ``NodeLiveLayerSource`` on ``NodeServiceClient``: the request it sends and what each answer becomes."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.control_plane.runtime.live_layer import (
    LiveLayerUnavailable,
    ReloadingLiveLayerCache,
)
from app.harness.journal_node.live_layer import NodeLiveLayerSource
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import Callable

ORG = "org-1"
CREDENTIAL = "worker-credential-7f3a9c1e5b2d"
LAYER: dict[str, Any] = {
    "schemaVersion": 1,
    "orgId": ORG,
    "revision": 7,
    "layers": [{"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "approvals": {"writes": "deny"}}}],
    "revokedBindings": [],
}


class _Credential:
    async def worker_credential(self) -> str:
        return CREDENTIAL


class _Node:
    """Node's live route as a transport: answers ``reply`` and keeps the requests it got."""

    def __init__(self, reply: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._reply = reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._reply(request)

    def source(self) -> NodeLiveLayerSource:
        client = httpx.AsyncClient(base_url="http://node.internal:3000", transport=httpx.MockTransport(self))
        return NodeLiveLayerSource(base_url="http://node.internal:3000/", credentials=_Credential(), http_client=client)


def _json(body: object, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


async def test_cpd6_42_the_request_is_one_get_with_the_worker_credential() -> None:
    node = _Node(_json(LAYER))

    document = await node.source().load(ORG, None)

    [request] = node.requests
    assert (request.method, str(request.url)) == (
        "GET", "http://node.internal:3000/api/v1/agent-harness/internal/control-plane/orgs/org-1/live",
    )
    assert request.headers["authorization"] == f"Bearer {CREDENTIAL}"
    assert request.content == b""
    assert document is not None
    assert (document.org_id.root, document.revision.root, len(document.layers)) == (ORG, 7, 1)


async def test_cpd6_42_since_is_sent_and_an_unchanged_answer_is_none() -> None:
    node = _Node(lambda _request: httpx.Response(204))

    assert await node.source().load(ORG, 7) is None
    assert node.requests[0].url.params["since"] == "7"


async def test_cpd6_42_an_org_id_is_one_path_segment() -> None:
    node = _Node(_json({**LAYER, "orgId": "a.b:c"}))

    _ = await node.source().load("a.b:c", None)
    with pytest.raises(LiveLayerUnavailable):
        _ = await node.source().load("../runs", None)

    assert node.requests[0].url.raw_path.endswith(b"/orgs/a.b%3Ac/live")
    assert node.requests[1].url.raw_path.endswith(b"/orgs/..%2Fruns/live")


def _unreachable(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


@pytest.mark.parametrize(
    "reply",
    [
        _json({"error": "nope"}, 401),
        _json({"error": "nope"}, 404),
        _json({"error": "down"}, 503),
        lambda _request: httpx.Response(204),
        _json({**LAYER, "orgId": "org-2"}),
        _json({**LAYER, "layers": [{"layer": {"approvals": {"writes": "sometimes"}}}]}),
        _json({**LAYER, "apiKey": "sk-live-layer-canary-000000000000"}),
        lambda _request: httpx.Response(200, content=b"<html>"),
        _unreachable,
    ],
    ids=["401", "404", "503", "204_unasked", "another_org", "invalid_layer", "unknown_member", "not_json", "transport"],
)
async def test_cpd6_42_any_other_answer_is_unavailable_after_one_attempt(
    reply: Callable[[httpx.Request], httpx.Response], caplog: pytest.LogCaptureFixture
) -> None:
    node = _Node(reply)

    with caplog.at_level(logging.DEBUG), pytest.raises(LiveLayerUnavailable) as refused:
        _ = await node.source().load(ORG, None)

    assert len(node.requests) == 1
    assert refused.value.reason_code == "live_layer_unavailable"
    for text in (str(refused.value), caplog.text):
        assert CREDENTIAL not in text
        assert "canary" not in text
        assert "sometimes" not in text


async def test_cpd6_42_the_cache_over_the_node_source_goes_stale_when_node_stops_answering() -> None:
    answers = [_json(LAYER), _json({"error": "down"}, 503)]
    node = _Node(lambda request: answers[min(len(node.requests) - 1, 1)](request))
    clock = ManualClock()
    cache = ReloadingLiveLayerCache(node.source(), clock)

    fresh = await cache.current(ORG)
    _ = clock.advance(30.0)
    stale = await cache.current(ORG)

    assert (fresh.revision, fresh.stale, stale.revision, stale.stale) == (7, False, 7, True)
    assert node.requests[1].url.params["since"] == "7"
