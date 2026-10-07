"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/connectors."""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, TypedDict

import requests
from strict_openapi import (
    _query_parameters,
    _schema_errors,
    _spec,
    find_operation,
)

from helper.clients.connectors_client import ConnectorsClient
from helper.second_user import SecondUser

CONNECTORS_BASE = "/api/v1/connectors"

MISSING_CONNECTOR_ID = "00000000-0000-4000-8000-000000000000"
# Express decodes this to "bad/id", which guardPathParams refuses on every
# :connectorId / :connectorType / :recordId route before auth runs.
UNSAFE_CONNECTOR_ID = "bad%2Fid"
# Passes guardPathParams but not the connectorIdParamSchema regex, which only
# some routes use (the others accept any non-empty string and ask Python).
MALFORMED_CONNECTOR_ID = "bad.id"
MISSING_CONNECTOR_TYPE = "NoSuchConnectorType"

# Needs no credentials and contacts nothing external; team scope only.
SEED_CONNECTOR_TYPE = "Demo"
SEED_CONNECTOR_SCOPE = "team"
SEED_CONNECTOR_AUTH_TYPE = "NONE"

# A personal-scope type offering OAUTH and API_TOKEN, so a member can exercise
# both the OAuth App rules and a plain create.
PERSONAL_CONNECTOR_TYPE = "Jira Cloud Personal"
PERSONAL_TOKEN_AUTH_TYPE = "API_TOKEN"
PERSONAL_OAUTH_AUTH_TYPE = "OAUTH"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# Query strings the shared connectorListSchema refuses on every route that uses it
# (GET /, /registry, /configured, /agents/active).
INVALID_LIST_QUERIES: list[Any] = [
    {"scope": "organisation"},
    {"scope": ""},
    {"page": 0},
    {"page": "abc"},
    {"page": "1.5"},
    # An empty value is not "absent": it reaches the number check as undefined and fails it.
    {"page": ""},
    {"limit": 0},
    {"limit": 201},
    {"limit": "ten"},
    {"limit": ""},
    {"isAuthenticated": "yes"},
    {"isAuthenticated": ""},
    {"isActive": "1"},
    {"connectorType": ""},
    # Values are trimmed before they are checked.
    {"connectorType": "  "},
    [("page", 1), ("page", 2)],
    [("search", "a"), ("search", "b")],
]
INVALID_LIST_IDS = [
    "scope-not-in-enum",
    "scope-empty",
    "page-zero",
    "page-not-a-number",
    "page-fraction",
    "page-empty",
    "limit-zero",
    "limit-above-200",
    "limit-not-a-number",
    "limit-empty",
    "is-authenticated-not-boolean",
    "is-authenticated-empty",
    "is-active-not-boolean",
    "connector-type-empty",
    "connector-type-blank",
    "page-repeated",
    "search-repeated",
]

DELETE_POLL_TIMEOUT_SEC = 30.0
DELETE_POLL_INTERVAL_SEC = 0.5

SeedConnector = Callable[..., str]


class KbRecords(TypedDict):
    kb_id: str
    text_record_id: str
    text_record_status: str
    text_record_reason: str
    text_sentinel: str
    unsupported_record_id: str


class ConnectorsAuditClient(ConnectorsClient):
    """Client for /api/v1/connectors, acting as the shared org admin."""

    def create_instance(
        self, *, auth: bool = True, **body: Any
    ) -> requests.Response:
        return self.post("/", auth=auth, json=body)

    def delete_instance(self, connector_id: str) -> requests.Response:
        return self.delete(f"/{connector_id}")

    def send(
        self, method: str, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self._client.request(method, self._path(path), auth=auth, **kwargs)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def unique_name(label: str = "spec-audit") -> str:
    return f"{label} {uuid.uuid4().hex[:10]}"


def seed_body(**overrides: Any) -> dict[str, Any]:
    """The smallest documented create body: an unconfigured, never-synced Demo instance."""
    return {
        "connectorType": SEED_CONNECTOR_TYPE,
        "instanceName": unique_name(),
        "scope": SEED_CONNECTOR_SCOPE,
        "authType": SEED_CONNECTOR_AUTH_TYPE,
        **overrides,
    }


def personal_body(**overrides: Any) -> dict[str, Any]:
    """A create body any member may send: personal scope, token auth, no credentials."""
    return {
        "connectorType": PERSONAL_CONNECTOR_TYPE,
        "instanceName": unique_name(),
        "scope": "personal",
        "authType": PERSONAL_TOKEN_AUTH_TYPE,
        **overrides,
    }


def created_connector_id(resp: requests.Response) -> str:
    assert resp.status_code < 300, (
        f"could not create a connector: {resp.status_code} {resp.text[:300]}"
    )
    return str(resp.json()["connector"]["connectorId"])


def create_seed_connector(client: ConnectorsAuditClient, **overrides: Any) -> str:
    """Create one unconfigured, never-synced Demo instance and return its connectorId."""
    return created_connector_id(client.create_instance(**seed_body(**overrides)))


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a connectors route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{CONNECTORS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def wait_until_deleted(client: ConnectorsAuditClient, connector_id: str) -> None:
    """Deletion is accepted with 202 and finishes in the background."""
    deadline = time.monotonic() + DELETE_POLL_TIMEOUT_SEC
    while True:
        resp = client.get(f"/{connector_id}")
        if resp.status_code == 404:
            return
        assert time.monotonic() < deadline, (
            f"connector {connector_id} still answers {resp.status_code} "
            f"{DELETE_POLL_TIMEOUT_SEC:.0f}s after its deletion was accepted"
        )
        time.sleep(DELETE_POLL_INTERVAL_SEC)


def spec_query_value_errors(method: str, route: str, name: str, value: Any) -> list[str]:
    """What the spec's schema for one query parameter says about ``value``.

    The gate does not evaluate array-typed query parameters, so a test that needs to
    show the spec refuses (or allows) such a value checks the parameter schema itself.
    """
    doc, registry = _spec()
    found = find_operation(doc, method, route)
    assert found is not None, f"{method} {route} is not in the OpenAPI spec"
    spec_path, operation = found
    documented = _query_parameters(doc, spec_path, method.lower(), operation)
    assert name in documented, f"{method} {route} documents no query parameter {name!r}"
    _, pointer = documented[name]
    return _schema_errors(registry, pointer, value)


# Origin of the frontend the OAuth redirect URI and callback redirectUrl are built on.
OAUTH_BASE_URL = "http://localhost:3001"

SYNC_RUNNING_STATUSES = ("SYNCING", "FULL_SYNCING", "QUEUED")
STATUS_POLL_TIMEOUT_SEC = 60.0
STATUS_POLL_INTERVAL_SEC = 0.5

# A personal type whose API_TOKEN form needs no reachable source to be saved,
# so a member can own a configured instance.
MEMBER_TOKEN_AUTH: dict[str, Any] = {
    "baseUrl": "https://spec-audit.invalid",
    "email": "spec-audit@example.com",
    "apiToken": "spec-audit-token",
}


def instance_state(client: ConnectorsAuditClient, connector_id: str) -> dict[str, Any]:
    resp = client.get(f"/{connector_id}")
    assert resp.status_code == 200, resp.text[:300]
    return dict(resp.json()["connector"])


def wait_for_state(
    client: ConnectorsAuditClient,
    connector_id: str,
    ready: Callable[[dict[str, Any]], bool],
    what: str,
    timeout: float = STATUS_POLL_TIMEOUT_SEC,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        state = instance_state(client, connector_id)
        if ready(state):
            return state
        assert time.monotonic() < deadline, (
            f"connector {connector_id} did not reach {what} in {timeout:.0f}s: "
            f"status={state.get('status')} isActive={state.get('isActive')} isLocked={state.get('isLocked')}"
        )
        time.sleep(STATUS_POLL_INTERVAL_SEC)


def is_syncing(state: dict[str, Any]) -> bool:
    return state.get("status") in SYNC_RUNNING_STATUSES


def is_settled(state: dict[str, Any]) -> bool:
    return state.get("status") not in SYNC_RUNNING_STATUSES and not state.get("isLocked")


class StubSource:
    """A local HTTP server standing in for a connector's source or OAuth provider.

    The connector service runs on this host, so it reaches the stub on 127.0.0.1.
    GET answers ``get_body``; POST answers ``post_body`` (a token response by default).
    """

    def __init__(self, get_body: Any, post_body: Any | None = None) -> None:
        self.get_body = get_body
        self.post_body = post_body if post_body is not None else {
            "access_token": "spec-audit-access-token",
            "refresh_token": "spec-audit-refresh-token",
            "token_type": "Bearer",
            "expires_in": 7200,
        }
        self.requests: list[tuple[str, str]] = []
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
                return

            def _answer(self, body: Any) -> None:
                stub.requests.append((self.command, self.path))
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 - stdlib name
                self._answer(stub.get_body)

            def do_POST(self) -> None:  # noqa: N802 - stdlib name
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self._answer(stub.post_body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> StubSource:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
