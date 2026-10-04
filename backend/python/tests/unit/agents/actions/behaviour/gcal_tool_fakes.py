"""Fakes for behaviour tests of the Google Calendar agent tools.

Only the HTTP layer is faked. The tool runs through the real
``GoogleCalendarDataSource`` and a real ``googleapiclient`` Resource built from
the bundled Calendar v3 discovery document, wrapped in real ``AuthorizedHttp``
credentials, so request URLs, query strings, JSON bodies, the Authorization
header and ``HttpError`` parsing are all the library's own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httplib2
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.agents.actions.google.calendar.calendar import GoogleCalendar

ACCESS_TOKEN = "ya29.fake-access-token-must-never-leak"
REFRESH_TOKEN = "1//fake-refresh-token-must-never-leak"
CLIENT_SECRET = "fake-client-secret-must-never-leak"
TOKEN_PATH = "/token"
BASE_PATH = "/calendar/v3"


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, str]
    body: Any
    headers: dict[str, str]


@dataclass
class GoogleResponse:
    status: int
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)


def google_error(status: int, message: str, reason: str = "", headers: dict[str, str] | None = None) -> GoogleResponse:
    """The error envelope Google's JSON APIs return for a 4xx/5xx."""
    errors = [{"reason": reason, "message": message, "domain": "global"}] if reason else []
    return GoogleResponse(status, {"error": {"code": status, "message": message, "errors": errors}}, headers or {})


def _decode_body(raw: str | None) -> object:
    """API calls send JSON; the OAuth token endpoint gets a form body."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {k: v[0] for k, v in parse_qs(raw).items()}


class FakeGoogleHttp:
    """Stands in for httplib2.Http: routes by method and path, records every request.

    A route's responses are consumed one per call and the last one repeats, which
    is how "fail, then succeed" and pagination are staged. Unrouted requests get
    a Google-shaped 404 and are kept in ``unrouted``.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern, list[Any]]] = []
        self.requests: list[RecordedRequest] = []
        self.unrouted: list[str] = []

    def on(self, method: str, path_regex: str, *responses: object, base: str = BASE_PATH) -> "FakeGoogleHttp":
        self.routes.insert(0, (method.upper(), re.compile(rf"^{base}{path_regex}$"), list(responses)))
        return self

    def calls(self, method: str | None = None, path_regex: str | None = None, base: str = BASE_PATH) -> list[RecordedRequest]:
        return [
            r for r in self.requests
            if (method is None or r.method == method.upper())
            and (path_regex is None or re.fullmatch(f"{base}{path_regex}", r.path))
        ]

    def request(self, uri: str, method: str = "GET", body: object = None, headers: dict | None = None,
                redirections: int = 5, connection_type: object = None) -> tuple[httplib2.Response, bytes]:
        parsed = urlparse(uri)
        raw_body = body.decode() if isinstance(body, bytes) else body
        recorded = RecordedRequest(
            method=method.upper(),
            path=unquote(parsed.path),
            query={k: v[0] for k, v in parse_qs(parsed.query).items()},
            body=_decode_body(raw_body),
            headers={str(k).lower(): str(v) for k, v in (headers or {}).items()},
        )
        self.requests.append(recorded)
        for route_method, pattern, responses in self.routes:
            if route_method == recorded.method and pattern.match(recorded.path):
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                return self._render(item)
        self.unrouted.append(f"{recorded.method} {recorded.path}")
        return self._render(google_error(404, "Not Found", "notFound"))

    @staticmethod
    def _render(item: object) -> tuple[httplib2.Response, bytes]:
        if not isinstance(item, GoogleResponse):
            item = GoogleResponse(200, item)
        content = b"" if item.payload is None else json.dumps(item.payload).encode()
        headers = {"status": str(item.status), "content-type": "application/json", **item.headers}
        return httplib2.Response(headers), content


def build_calendar_tool(http: FakeGoogleHttp, *, refreshable: bool = False) -> GoogleCalendar:
    """The tool as the agent factory builds it: a Calendar v3 Resource on authorized HTTP.

    ``refreshable`` gives the credentials a refresh token, so a 401 makes the auth
    layer POST to the (faked) token endpoint and retry once, as it does in production.
    """
    credentials = Credentials(token=ACCESS_TOKEN)
    if refreshable:
        credentials = Credentials(
            token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN, client_id="client-id",
            client_secret=CLIENT_SECRET, token_uri=f"https://oauth2.googleapis.com{TOKEN_PATH}",
        )
    authed = AuthorizedHttp(credentials, http=http)
    service = build("calendar", "v3", http=authed, static_discovery=True, cache_discovery=False)
    return GoogleCalendar(service)


def result(outcome: tuple[bool, str]) -> tuple[bool, dict[str, Any]]:
    success, text = outcome
    return success, json.loads(text)
