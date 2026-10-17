"""Acme Helpdesk: a small REST API for API Toolsets tests and for users to copy.

Runs as the ``openapi-fixture`` service of the integration stack. An admin uploads
``openapi.yaml`` (served at ``/openapi.yaml``) to PipesHub as an API Toolset, and an
agent that has it attached should call these endpoints when a question needs them.

    GET    /tickets                    list tickets (status, assignee, tag filters)
    POST   /tickets                    open a ticket (optional Idempotency-Key header)
    GET    /tickets/{ticketId}         one ticket with its comments
    PATCH  /tickets/{ticketId}         change status, assignee or priority
    POST   /tickets/{ticketId}/comments add a comment
    GET    /users/me                   who the credentials belong to
    POST   /oauth/token                OAuth2 client-credentials token endpoint
    GET    /openapi.yaml               the spec for this API

    GET    /__fixture__/health         200 once serving
    GET    /__fixture__/calls          every API call received, oldest first
    POST   /__fixture__/reset          restore the seed data and forget the calls
    POST   /__fixture__/expire-tokens  revoke every issued OAuth token (tests refresh)

Every auth scheme in the spec is accepted at once, so one running server covers
them all; the call log records which one a request used. The seed data holds facts
no model could guess (ticket ids, assignees), so a test can tell from the call log
that the agent really asked this server. Standard library only, so it runs on a
stock Python image.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

SPEC_PATH = Path(__file__).resolve().parent / "openapi.yaml"
TOKEN_TTL_SECONDS = 3600

STATUSES = ("open", "pending", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")

SEED_TICKETS: dict[str, dict[str, Any]] = {
    "ACME-7311": {
        "id": "ACME-7311",
        "title": "VPN drops every 20 minutes on the Pune office network",
        "status": "open",
        "priority": "high",
        "assignee": "noor.rahimi",
        "tags": ["network", "vpn"],
        "comments": [
            {"id": "C-1", "author": "noor.rahimi", "body": "Reproduced. Firewall idle timeout is 1200s."},
        ],
    },
    "ACME-7312": {
        "id": "ACME-7312",
        "title": "Invoice PDF export shows the wrong currency for EUR accounts",
        "status": "pending",
        "priority": "normal",
        "assignee": "lucas.ferreira",
        "tags": ["billing"],
        "comments": [],
    },
    "ACME-7313": {
        "id": "ACME-7313",
        "title": "Onboard the new Lisbon support team to the helpdesk",
        "status": "closed",
        "priority": "low",
        "assignee": "noor.rahimi",
        "tags": ["onboarding"],
        "comments": [],
    },
}


@dataclass(frozen=True)
class Credentials:
    """The demo credentials each auth scheme accepts. Override them with flags."""

    api_key: str = "acme-demo-key"
    bearer_token: str = "acme-demo-token"
    username: str = "acme"
    password: str = "acme-demo-password"
    client_id: str = "acme-client"
    client_secret: str = "acme-client-secret"


class ApiError(Exception):
    def __init__(self, status: int, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body: dict[str, Any] = {"error": message}
        if details is not None:
            self.body["details"] = details


class State:
    """Tickets, issued OAuth tokens and the call log, behind one lock."""

    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.tickets = copy.deepcopy(SEED_TICKETS)
        self.next_ticket = 7314
        self.next_comment = 2
        self.tokens: dict[str, float] = {}
        self.idempotency: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def authenticate(headers: Any, query: dict[str, list[str]], state: State) -> str:
    """Return the name of the scheme that accepted the request, or raise 401."""
    creds = state.credentials
    if headers.get("X-API-Key") == creds.api_key or query.get("api_key") == [creds.api_key]:
        return "apiKey"
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    if scheme.lower() == "bearer":
        if value == creds.bearer_token:
            return "bearer"
        expires_at = state.tokens.get(value)
        if expires_at is not None and expires_at > time.time():
            return "oauth2"
    if scheme.lower() == "basic" and _basic_pair(value) == (creds.username, creds.password):
        return "basic"
    raise ApiError(401, "Missing or invalid credentials")


def _basic_pair(encoded: str) -> tuple[str, str] | None:
    try:
        user, _, password = base64.b64decode(encoded).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return user, password


def issue_token(form: dict[str, list[str]], headers: Any, state: State) -> dict[str, Any]:
    """OAuth2 client-credentials grant (RFC 6749 section 4.4)."""
    if form.get("grant_type") != ["client_credentials"]:
        raise ApiError(400, "unsupported_grant_type")
    client = (_first(form, "client_id"), _first(form, "client_secret"))
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    if scheme.lower() == "basic":
        client = _basic_pair(value) or ("", "")
    if client != (state.credentials.client_id, state.credentials.client_secret):
        raise ApiError(401, "invalid_client")
    token = secrets.token_urlsafe(24)
    state.tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL_SECONDS}


# ---------------------------------------------------------------------------
# API handlers: each takes (state, path params, query, json body) and returns
# (status, body). They run with the state lock held.
# ---------------------------------------------------------------------------


def list_tickets(state: State, _p: dict[str, str], query: dict[str, list[str]], _b: Any) -> tuple[int, Any]:
    status = _first(query, "status")
    if status is not None and status not in STATUSES:
        raise ApiError(422, "Invalid query parameter", {"status": f"must be one of {list(STATUSES)}"})
    assignee = _first(query, "assignee")
    tags = set(query.get("tag", []))
    limit = _int_param(query, "limit", default=20, low=1, high=100)
    found = [
        _summary(t)
        for t in state.tickets.values()
        if (status is None or t["status"] == status)
        and (assignee is None or t["assignee"] == assignee)
        and tags <= set(t["tags"])
    ]
    return 200, {"tickets": found[:limit], "total": len(found)}


def create_ticket(state: State, _p: dict[str, str], _q: dict[str, list[str]], body: Any, *, idempotency_key: str | None = None) -> tuple[int, Any]:
    if idempotency_key and idempotency_key in state.idempotency:
        return 200, state.tickets[state.idempotency[idempotency_key]]
    data = _require_object(body)
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ApiError(422, "Invalid body", {"title": "required, non-empty string"})
    priority = data.get("priority", "normal")
    _check_enum("priority", priority, PRIORITIES)
    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ApiError(422, "Invalid body", {"tags": "must be a list of strings"})
    ticket_id = f"ACME-{state.next_ticket}"
    state.next_ticket += 1
    ticket = {
        "id": ticket_id,
        "title": title.strip(),
        "status": "open",
        "priority": priority,
        "assignee": None,
        "tags": tags,
        "comments": [],
    }
    if isinstance(data.get("description"), str):
        ticket["description"] = data["description"]
    state.tickets[ticket_id] = ticket
    if idempotency_key:
        state.idempotency[idempotency_key] = ticket_id
    return 201, ticket


def get_ticket(state: State, params: dict[str, str], _q: dict[str, list[str]], _b: Any) -> tuple[int, Any]:
    return 200, _ticket(state, params["ticketId"])


def update_ticket(state: State, params: dict[str, str], _q: dict[str, list[str]], body: Any) -> tuple[int, Any]:
    ticket = _ticket(state, params["ticketId"])
    data = _require_object(body)
    allowed = {"status": STATUSES, "priority": PRIORITIES}
    unknown = set(data) - {"status", "priority", "assignee"}
    if unknown or not data:
        raise ApiError(422, "Invalid body", {"fields": "send one or more of status, priority, assignee"})
    for field, values in allowed.items():
        if field in data:
            _check_enum(field, data[field], values)
    if "assignee" in data and data["assignee"] is not None and not isinstance(data["assignee"], str):
        raise ApiError(422, "Invalid body", {"assignee": "must be a string or null"})
    ticket.update(data)
    return 200, ticket


def add_comment(state: State, params: dict[str, str], _q: dict[str, list[str]], body: Any) -> tuple[int, Any]:
    ticket = _ticket(state, params["ticketId"])
    text = _require_object(body).get("body")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(422, "Invalid body", {"body": "required, non-empty string"})
    comment = {"id": f"C-{state.next_comment}", "author": "api", "body": text.strip()}
    state.next_comment += 1
    ticket["comments"].append(comment)
    return 201, comment


def current_user(_s: State, _p: dict[str, str], _q: dict[str, list[str]], _b: Any) -> tuple[int, Any]:
    return 200, {"username": "api", "displayName": "Acme API user", "team": "Support Platform"}


Handler = Callable[..., tuple[int, Any]]

# (method, path template, handler). The contract test checks this table against openapi.yaml.
ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/tickets", list_tickets),
    ("POST", "/tickets", create_ticket),
    ("GET", "/tickets/{ticketId}", get_ticket),
    ("PATCH", "/tickets/{ticketId}", update_ticket),
    ("POST", "/tickets/{ticketId}/comments", add_comment),
    ("GET", "/users/me", current_user),
]

_COMPILED = [
    (method, re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template) + "$"), handler)
    for method, template, handler in ROUTES
]


def match_route(method: str, path: str) -> tuple[Handler, dict[str, str]]:
    path_found = False
    for route_method, pattern, handler in _COMPILED:
        found = pattern.match(path)
        if found:
            path_found = True
            if route_method == method:
                return handler, found.groupdict()
    raise ApiError(405 if path_found else 404, "Method not allowed" if path_found else "Not found")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _first(values: dict[str, list[str]], key: str) -> str | None:
    found = values.get(key)
    return found[0] if found else None


def _int_param(query: dict[str, list[str]], key: str, default: int, low: int, high: int) -> int:
    raw = _first(query, key)
    if raw is None:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ApiError(422, "Invalid query parameter", {key: f"integer between {low} and {high}"})
    return int(raw)


def _check_enum(field: str, value: Any, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ApiError(422, "Invalid body", {field: f"must be one of {list(allowed)}"})


def _require_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ApiError(422, "Invalid body", {"body": "must be a JSON object"})
    return body


def _ticket(state: State, ticket_id: str) -> dict[str, Any]:
    ticket = state.tickets.get(ticket_id)
    if ticket is None:
        raise ApiError(404, f"Ticket {ticket_id} not found")
    return ticket


def _summary(ticket: dict[str, Any]) -> dict[str, Any]:
    return {k: ticket[k] for k in ("id", "title", "status", "priority", "assignee", "tags")}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def make_handler(state: State) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            pass

        def _send(self, status: int, body: Any = None, content_type: str = "application/json") -> None:
            if body is None:
                data = b""
            elif isinstance(body, bytes):
                data = body
            else:
                data = json.dumps(body).encode()
            self.send_response(status)
            if data:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _fixture(self, method: str, path: str) -> bool:
            if not path.startswith("/__fixture__/"):
                return False
            with state.lock:
                if (method, path) == ("GET", "/__fixture__/health"):
                    self._send(200, {"status": "ok"})
                elif (method, path) == ("GET", "/__fixture__/calls"):
                    self._send(200, {"calls": list(state.calls)})
                elif (method, path) == ("POST", "/__fixture__/reset"):
                    state.reset()
                    self._send(200, {"status": "reset"})
                elif (method, path) == ("POST", "/__fixture__/expire-tokens"):
                    state.tokens.clear()
                    self._send(200, {"status": "expired"})
                else:
                    self._send(404, {"error": "Not found"})
            return True

        def _dispatch(self, method: str) -> None:
            url = urlsplit(self.path)
            raw = self._read_body()
            if self._fixture(method, url.path):
                return
            if (method, url.path) == ("GET", "/openapi.yaml"):
                self._send(200, SPEC_PATH.read_bytes(), "application/yaml")
                return
            query = parse_qs(url.query, keep_blank_values=True)
            try:
                with state.lock:
                    if (method, url.path) == ("POST", "/oauth/token"):
                        form = parse_qs(raw.decode(errors="replace"))
                        self._send(200, issue_token(form, self.headers, state))
                        return
                    handler, params = match_route(method, url.path)
                    scheme = authenticate(self.headers, query, state)
                    body = _parse_json(raw)
                    state.calls.append({
                        "method": method,
                        "path": url.path,
                        "query": query,
                        "body": body,
                        "auth": scheme,
                        "at": time.time(),
                    })
                    extra = {"idempotency_key": self.headers.get("Idempotency-Key")} if handler is create_ticket else {}
                    status, payload = handler(state, params, query, body, **extra)
            except ApiError as err:
                self._send(err.status, err.body)
                return
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - stdlib name
            self._dispatch("POST")

        def do_PATCH(self) -> None:  # noqa: N802 - stdlib name
            self._dispatch("PATCH")

        def do_PUT(self) -> None:  # noqa: N802 - stdlib name
            self._dispatch("PUT")

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib name
            self._dispatch("DELETE")

    return RequestHandler


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as err:
        raise ApiError(400, "Body is not valid JSON") from err


def serve(host: str, port: int, credentials: Credentials | None = None) -> ThreadingHTTPServer:
    state = State(credentials or Credentials())
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]  # tests reach the state through the server
    return server


def main() -> None:
    defaults = Credentials()
    parser = argparse.ArgumentParser(description="Acme Helpdesk reference API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    for field, value in vars(defaults).items():
        parser.add_argument(f"--{field.replace('_', '-')}", default=value)
    args = parser.parse_args()
    credentials = Credentials(**{field: getattr(args, field) for field in vars(defaults)})
    serve(args.host, args.port, credentials).serve_forever()


if __name__ == "__main__":
    main()
