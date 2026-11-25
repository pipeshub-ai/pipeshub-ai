"""A local Jira for the connector cells: the REST v3 shapes the product's Jira toolset calls, nothing more.

It refuses any request without the toolset's credential (``Bearer`` PAT), as Jira Data Center does, keeps every
request it saw (method, path, authorization) and counts each create by its summary, so a test can say a write
reached Jira exactly once.
"""

from __future__ import annotations

import contextlib
import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ISSUES = [
    {"id": "10001", "key": "OPS-1", "fields": {"summary": "Ledger export fails", "status": {"name": "Open"},
                                                "issuetype": {"name": "Bug"}, "project": {"key": "OPS"}}},
    {"id": "10002", "key": "OPS-2", "fields": {"summary": "Rotate the partner key", "status": {"name": "Done"},
                                                "issuetype": {"name": "Task"}, "project": {"key": "OPS"}}},
]


@dataclass
class FakeJira:
    token: str
    seen: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    creates: Counter[str] = field(default_factory=Counter[str])
    labels: list[list[str]] = field(default_factory=list[list[str]])
    _keys: itertools.count[int] = field(default_factory=lambda: itertools.count(100))
    base: str = ""

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth])
        app.router.add_post("/rest/api/3/search/jql", self._search)
        app.router.add_get("/rest/api/3/search/jql", self._search)
        app.router.add_post("/rest/api/3/issue", self._create)
        app.router.add_get("/rest/api/3/field", self._fields)
        app.router.add_get("/rest/api/3/serverInfo", self._server_info)
        app.router.add_route("*", "/{tail:.*}", self._other)
        return app

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:  # noqa: ANN401
        authorization = request.headers.get("Authorization", "")
        self.seen.append((request.method, request.path, authorization))
        if authorization != f"Bearer {self.token}":
            return web.json_response({"errorMessages": ["Unauthorized"]}, status=401)
        return await handler(request)

    async def _search(self, request: web.Request) -> web.Response:
        return web.json_response({"issues": ISSUES, "isLast": True, "total": len(ISSUES)})

    async def _create(self, request: web.Request) -> web.Response:
        body = await request.json()
        fields = body.get("fields") or {}
        self.creates[str(fields.get("summary"))] += 1
        self.labels.append(list(fields.get("labels") or []))
        number = next(self._keys)
        key = f"{(fields.get('project') or {}).get('key', 'OPS')}-{number}"
        return web.json_response({"id": str(10000 + number), "key": key, "self": f"{self.base}/rest/api/3/issue/{key}"}, status=201)

    async def _fields(self, request: web.Request) -> web.Response:
        return web.json_response([{"id": "summary", "name": "Summary"}, {"id": "status", "name": "Status"}])

    async def _server_info(self, request: web.Request) -> web.Response:
        return web.json_response({"baseUrl": self.base, "version": "9.12.0", "deploymentType": "Server"})

    async def _other(self, request: web.Request) -> web.Response:
        return web.json_response({"errorMessages": [f"not served: {request.method} {request.path}"]}, status=404)


@contextlib.asynccontextmanager
async def serving(token: str) -> AsyncIterator[FakeJira]:
    jira = FakeJira(token)
    runner = web.AppRunner(jira.app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    jira.base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        yield jira
    finally:
        await runner.cleanup()


__all__ = ["ISSUES", "FakeJira", "serving"]
