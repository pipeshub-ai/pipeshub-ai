"""Fakes for behaviour tests of the Lumos agent tools.

Only the HTTP layer is faked. The tool runs through the real ``LumosDataSource``
and ``HTTPClient`` on a real ``httpx.AsyncClient`` whose transport is
``FakeLumosApi``, so URLs, query strings, JSON bodies and the Authorization
header are exactly what would go to api.lumos.com.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.agents.actions.lumos.lumos import Lumos
from app.sources.client.lumos.lumos import LumosClient, LumosRESTClientViaApiKey

API_KEY = "lumos_fake-api-key-must-never-leak"


@dataclass
class LumosCall:
    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    headers: dict[str, str]


def lumos_page(items: list[dict[str, Any]], *, page: int = 1, size: int = 25, total: int | None = None) -> dict[str, Any]:
    """The ``Page_*`` envelope every Lumos list endpoint returns."""
    total = len(items) if total is None else total
    pages = max(1, -(-total // size))
    return {"items": items, "total": total, "page": page, "size": size, "pages": pages}


def lumos_error(status: int, detail: object = "error", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail}, headers=headers or {})


def validation_error(field: str, message: str) -> httpx.Response:
    """FastAPI's 422 shape, which the Lumos API uses for bad arguments."""
    return lumos_error(422, [{"loc": ["query", field], "msg": message, "type": "value_error", "input": "x"}])


class FakeLumosApi:
    """Routes by method and path; records every call.

    A route's responses are consumed one per call and the last one repeats. A
    response may be a JSON payload, an ``httpx.Response``, an exception to raise,
    or a callable taking the request. Unrouted requests get a 404.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[object]] = {}
        self.calls: list[LumosCall] = []
        self.unrouted: list[str] = []

    def on(self, method: str, path: str, *responses: object) -> "FakeLumosApi":
        self.routes[(method.upper(), path)] = list(responses)
        return self

    def called(self, method: str, path: str) -> list[LumosCall]:
        return [c for c in self.calls if c.method == method.upper() and c.path == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except json.JSONDecodeError:
                body = request.content.decode()
        # The raw path keeps percent-escapes, so an encoded "/" inside an id stays visible.
        path = request.url.raw_path.decode().split("?", 1)[0]
        self.calls.append(LumosCall(
            method=request.method,
            path=path,
            query=parse_qs(request.url.query.decode(), keep_blank_values=True),
            body=body,
            headers={k.lower(): v for k, v in request.headers.items()},
        ))
        responses = self.routes.get((request.method, path))
        if responses is None:
            self.unrouted.append(f"{request.method} {path}")
            return lumos_error(404, "Not Found")
        item = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(request)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)


def build_lumos_tool(api: FakeLumosApi) -> Lumos:
    """The tool as the agent factory builds it for an API-key toolset, on a transport that is ``api``."""
    rest = LumosRESTClientViaApiKey(API_KEY)
    rest.client = httpx.AsyncClient(
        transport=httpx.MockTransport(api.handler), headers=rest.headers,
        timeout=rest.timeout, follow_redirects=rest.follow_redirects,
    )
    return Lumos(LumosClient(rest))


def result(outcome: tuple[bool, str]) -> tuple[bool, dict[str, Any]]:
    success, text = outcome
    return success, json.loads(text)
