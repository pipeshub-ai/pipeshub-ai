"""Fakes for behaviour tests of the Jira agent tools.

Only the HTTP layer is faked. The tool runs through the real ``JiraDataSource``
and the real ``HTTPClient`` holding an API-token (Basic) credential; the fake is
an ``httpx.MockTransport`` under that client, so URLs, query strings, JSON
bodies, the Authorization header and response parsing are all the real ones.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.agents.actions.jira.jira import Jira
from app.sources.client.jira.jira import JiraClient, JiraRESTClientViaApiKey

SITE = "https://acme.atlassian.net"
EMAIL = "me@acme.test"
API_TOKEN = "ATATT-fake-jira-api-token-must-never-leak"
BASIC = base64.b64encode(f"{EMAIL}:{API_TOKEN}".encode()).decode()
API = "/rest/api/3"


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, str]
    body: Any
    headers: httpx.Headers


@dataclass
class FakeJiraApi:
    """Routes by method and path regex; records every request.

    A response is a JSON payload, ``(status, payload)``, ``(status, payload, headers)``,
    an exception to raise, or a callable taking the recorded request. A route's
    responses are consumed one per call and the last repeats, which is how paging
    and "fail, then succeed" are staged. Unrouted requests get Jira's 404.
    """

    requests: list[RecordedRequest] = field(default_factory=list)
    routes: list[tuple[str, re.Pattern[str], list[object]]] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)

    def on(self, method: str, path_regex: str, *responses: object) -> FakeJiraApi:
        self.routes.insert(0, (method.upper(), re.compile(rf"^{API}{path_regex}$"), list(responses)))
        return self

    def calls(self, method: str | None = None, path_regex: str | None = None) -> list[RecordedRequest]:
        return [
            r for r in self.requests
            if (method is None or r.method == method.upper())
            and (path_regex is None or re.fullmatch(f"{API}{path_regex}", r.path))
        ]

    def writes(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method in {"POST", "PUT", "DELETE"} and not r.path.endswith("/search/jql")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        recorded = RecordedRequest(
            method=request.method,
            path=request.url.path,
            query={k: v[0] for k, v in parse_qs(request.url.query.decode()).items()},
            body=json.loads(request.content) if request.content else None,
            headers=request.headers,
        )
        self.requests.append(recorded)
        for method, pattern, responses in self.routes:
            if method == recorded.method and pattern.match(recorded.path):
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                return self._render(item, recorded)
        self.unrouted.append(f"{recorded.method} {recorded.path}")
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist or you do not have permission to see it."], "errors": {}})

    def _render(self, item: object, recorded: RecordedRequest) -> httpx.Response:
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(recorded)
        if isinstance(item, tuple):
            status, payload, *rest = item
            headers = rest[0] if rest else {}
            if status == 204:
                return httpx.Response(204, headers=headers)
            return httpx.Response(status, json=payload, headers=headers)
        return httpx.Response(200, json=item)


def build_jira_tool(api: FakeJiraApi) -> Jira:
    """The tool as the agent factory builds it for an API-token connection, on ``api``."""
    http = JiraRESTClientViaApiKey(SITE, EMAIL, API_TOKEN)
    http.client = httpx.AsyncClient(transport=httpx.MockTransport(api), headers=http.headers)
    return Jira(JiraClient(http))


def result(outcome: tuple[bool, str]) -> tuple[bool, dict[str, Any]]:
    success, text = outcome
    return success, json.loads(text)


def user(account_id: str, name: str, email: str | None = None) -> dict[str, Any]:
    return {"accountId": account_id, "displayName": name, **({"emailAddress": email} if email else {}), "active": True}


def issue(key: str, summary: str = "Login fails", **fields: object) -> dict[str, Any]:
    return {"id": key.split("-")[-1], "key": key, "fields": {"summary": summary, "project": {"key": key.split("-")[0]}, **fields}}
