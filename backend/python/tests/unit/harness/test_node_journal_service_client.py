"""WP3-4 and WP3-5: ``NodeServiceClient`` sends one attempt per call, and is the only client of Node's internal routes."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from app.harness.journal_node.http import NodeServiceClient
from app.utils.request_context import reset_context, set_context

APP = Path(__file__).resolve().parents[3] / "app"
HOME = APP / "harness" / "journal_node" / "http.py"
INTERNAL_ROUTES = ("INTERNAL_PREFIX", "/agent-harness/internal")


class _Node:
    def __init__(self, *replies: httpx.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self) -> NodeServiceClient:
        http = httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(self.handle))
        return NodeServiceClient(base_url="http://node.test", timeout_s=10.0, http_client=http)


class _Unreachable(Exception):
    pass


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(401, json={"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}}),
    ],
    ids=["unavailable", "backpressure", "refused"],
)
async def test_wp3_4_every_answer_is_returned_after_one_attempt(answer: httpx.Response) -> None:
    node = _Node(answer)
    response = await node.client().send("POST", "/x", bearer="t", json={"a": 1})
    assert response.status_code == answer.status_code
    assert len(node.requests) == 1


async def test_wp3_4_an_unreachable_node_is_tried_once_and_mapped_by_the_caller() -> None:
    node = _Node(httpx.ConnectError("refused"))
    with pytest.raises(_Unreachable) as raised:
        _ = await node.client().send("GET", "/x", bearer="t", unreachable=lambda _: _Unreachable())
    assert isinstance(raised.value.__cause__, httpx.ConnectError)
    assert len(node.requests) == 1


async def test_wp3_4_without_a_mapping_the_transport_error_propagates() -> None:
    node = _Node(httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        _ = await node.client().send("GET", "/x", bearer="t")


async def test_wp3_4_repeated_failures_never_open_a_breaker() -> None:
    node = _Node(*(httpx.Response(503) for _ in range(12)))
    client = node.client()
    for _ in range(12):
        assert (await client.send("GET", "/x", bearer="t")).status_code == 503
    assert len(node.requests) == 12


@pytest.mark.parametrize(
    ("bearer", "authorization"),
    [("tok", "Bearer tok"), ("", "Bearer "), (None, None)],
    ids=["token", "empty", "none"],
)
async def test_wp3_4_only_the_bearer_and_content_type_are_added(bearer: str | None, authorization: str | None) -> None:
    node = _Node(httpx.Response(200))
    scope = set_context("root-request-1")
    try:
        _ = await node.client().send(
            "POST", "/x", bearer=bearer, content=b"raw", content_type="application/octet-stream", params={"fence": 2}
        )
    finally:
        reset_context(scope)
    (request,) = node.requests
    added = {k for k in request.headers if k not in {"host", "accept", "accept-encoding", "connection", "user-agent"}}
    expected = {"content-length", "content-type"} | ({"authorization"} if authorization is not None else set[str]())
    assert added == expected
    assert request.headers.get("authorization") == authorization
    assert request.url.params["fence"] == "2"


async def test_wp3_4_aclose_closes_the_client() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    await NodeServiceClient(base_url="http://node.test", timeout_s=1.0, http_client=http).aclose()
    assert http.is_closed


def _builds_its_own_client(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in {"AsyncClient", "Client", "ClientSession"}:
                return True
    return False


def test_wp3_5_only_node_service_client_builds_a_client_for_nodes_internal_routes() -> None:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path == HOME or not any(marker in source for marker in INTERNAL_ROUTES):
            continue
        if _builds_its_own_client(ast.parse(source)):
            offenders.append(str(path.relative_to(APP)))
    assert offenders == [], f"use NodeServiceClient (app/harness/journal_node/http.py): {offenders}"
