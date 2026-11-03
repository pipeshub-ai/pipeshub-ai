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
    GET    /announcements              helpdesk announcements; needs no credentials
    GET    /oauth/authorize            OAuth2 sign-in page (authorization code, PKCE)
    POST   /oauth/token                OAuth2 tokens: client credentials, authorization code, refresh
    GET    /openapi.yaml               the spec for this API
    GET    /                           a page listing all of the above, for a person in a browser

    GET    /__fixture__/health         200 once serving
    GET    /__fixture__/calls          every API call received, oldest first
    POST   /__fixture__/reset          restore the seed data and forget the calls
    POST   /__fixture__/expire-tokens  revoke every issued OAuth token (tests refresh)

Every auth scheme in the spec is accepted at once, so one running server covers
them all; the call log records which one a request used. With ``--public-url``,
the served spec's sign-in URL is that address, so a browser can open it while
PipesHub calls the API by its internal name. The seed data holds facts
no model could guess (ticket ids, assignees), so a test can tell from the call log
that the agent really asked this server. Standard library only, so it runs on a
stock Python image.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

SPEC_PATH = Path(__file__).resolve().parent / "openapi.yaml"
TOKEN_TTL_SECONDS = 3600
AUTH_CODE_TTL_SECONDS = 300

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
    "ACME-7305": {
        "id": "ACME-7305",
        "title": "Laptop refresh for the Madrid design team arrives without docking stations",
        "status": "pending",
        "priority": "normal",
        "assignee": "priya.nair",
        "tags": ["hardware", "procurement"],
        "comments": [
            {"id": "C-2", "author": "priya.nair", "body": "Vendor confirmed 14 docks ship on Friday."},
        ],
    },
    "ACME-7306": {
        "id": "ACME-7306",
        "title": "SSO login loops back to the sign-in page for contractors",
        "status": "open",
        "priority": "urgent",
        "assignee": "lucas.ferreira",
        "tags": ["sso", "access"],
        "comments": [
            {"id": "C-3", "author": "lucas.ferreira", "body": "Only accounts in the Contractors group are affected."},
            {"id": "C-4", "author": "noor.rahimi", "body": "IdP shows a clock skew of 6 minutes on two servers."},
        ],
    },
    "ACME-7307": {
        "id": "ACME-7307",
        "title": "Weekly payroll export to the bank fails on public holidays",
        "status": "open",
        "priority": "high",
        "assignee": "priya.nair",
        "tags": ["billing", "payroll"],
        "comments": [],
    },
    "ACME-7308": {
        "id": "ACME-7308",
        "title": "Request: shared mailbox for the Lisbon support team",
        "status": "closed",
        "priority": "low",
        "assignee": "lucas.ferreira",
        "tags": ["onboarding", "email"],
        "comments": [{"id": "C-5", "author": "lucas.ferreira", "body": "Created support-lisbon@ and added 6 members."}],
    },
    "ACME-7309": {
        "id": "ACME-7309",
        "title": "Printers on floor 3 print blank pages after the driver update",
        "status": "pending",
        "priority": "normal",
        "assignee": None,
        "tags": ["hardware"],
        "comments": [],
    },
    "ACME-7310": {
        "id": "ACME-7310",
        "title": "Wi-Fi in the Pune office drops for guests in meeting rooms",
        "status": "open",
        "priority": "normal",
        "assignee": "noor.rahimi",
        "tags": ["network", "wifi"],
        "comments": [],
    },
}

ANNOUNCEMENTS: list[dict[str, Any]] = [
    {"id": "AN-41", "date": "2026-10-20", "title": "Helpdesk hours over the Diwali weekend",
     "body": "The Pune desk is closed on 1 and 2 November; Lisbon covers urgent tickets."},
    {"id": "AN-40", "date": "2026-10-14", "title": "New SSO provider for contractors",
     "body": "Contractors sign in through the new identity provider from 28 October."},
    {"id": "AN-39", "date": "2026-10-02", "title": "Laptop refresh, phase two",
     "body": "Madrid and Lisbon design teams get new laptops this month."},
]


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
    def __init__(self, status: int, message: str, details: object = None) -> None:
        super().__init__(message)
        self.status = status
        self.body: dict[str, Any] = {"error": message}
        if details is not None:
            self.body["details"] = details


class State:
    """Tickets, issued OAuth tokens and the call log, behind one lock."""

    def __init__(self, credentials: Credentials, *, token_ttl: int = TOKEN_TTL_SECONDS, public_url: str | None = None) -> None:
        self.credentials = credentials
        self.token_ttl = token_ttl
        self.public_url = public_url.rstrip("/") if public_url else None
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.tickets = copy.deepcopy(SEED_TICKETS)
        self.next_ticket = 7314
        self.next_comment = 6
        # access token -> (expires at, the grant that issued it)
        self.tokens: dict[str, tuple[float, str]] = {}
        self.refresh_tokens: set[str] = set()
        self.auth_codes: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def authenticate(headers: Mapping[str, str], query: dict[str, list[str]], state: State) -> str:
    """Return the name of the scheme that accepted the request, or raise 401."""
    creds = state.credentials
    if headers.get("X-API-Key") == creds.api_key or query.get("api_key") == [creds.api_key]:
        return "apiKey"
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    if scheme.lower() == "bearer":
        if value == creds.bearer_token:
            return "bearer"
        expires_at, grant = state.tokens.get(value, (0.0, ""))
        if expires_at > time.time():
            return "oauth2" if grant == "client_credentials" else "oauth2-signin"
    if scheme.lower() == "basic" and _basic_pair(value) == (creds.username, creds.password):
        return "basic"
    raise ApiError(401, "Missing or invalid credentials")


def _basic_pair(encoded: str) -> tuple[str, str] | None:
    try:
        user, _, password = base64.b64decode(encoded).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return user, password


def issue_token(form: dict[str, list[str]], headers: Mapping[str, str], state: State) -> dict[str, Any]:
    """The token endpoint (RFC 6749): client credentials, authorization code with PKCE, refresh."""
    grant = _first(form, "grant_type")
    if grant not in ("client_credentials", "authorization_code", "refresh_token"):
        raise ApiError(400, "unsupported_grant_type")
    client = (_first(form, "client_id"), _first(form, "client_secret"))
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    if scheme.lower() == "basic":
        client = _basic_pair(value) or ("", "")
    if client != (state.credentials.client_id, state.credentials.client_secret):
        raise ApiError(401, "invalid_client")
    if grant == "client_credentials":
        return _new_tokens(state, grant)
    if grant == "refresh_token":
        if _first(form, "refresh_token") not in state.refresh_tokens:
            raise ApiError(400, "invalid_grant")
        return _new_tokens(state, "authorization_code", refresh_token=_first(form, "refresh_token"))
    code = state.auth_codes.pop(_first(form, "code") or "", None)
    if (code is None or code["expires_at"] < time.time() or code["client_id"] != client[0]
            or code["redirect_uri"] != _first(form, "redirect_uri")
            or (code["challenge"] and _pkce_challenge(_first(form, "code_verifier") or "") != code["challenge"])):
        raise ApiError(400, "invalid_grant")
    return _new_tokens(state, grant, refresh_token=secrets.token_urlsafe(24))


def _new_tokens(state: State, grant: str, *, refresh_token: str | None = None) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    state.tokens[token] = (time.time() + state.token_ttl, grant)
    issued: dict[str, Any] = {"access_token": token, "token_type": "Bearer", "expires_in": state.token_ttl}
    if refresh_token:
        state.refresh_tokens.add(refresh_token)
        issued["refresh_token"] = refresh_token
    return issued


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


_SIGN_IN_FIELDS = ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope")


def sign_in_page(params: dict[str, str], state: State, *, error: str = "") -> tuple[int, bytes]:
    """The page a person sees when an app asks to act for them (authorization code grant)."""
    if params.get("response_type", "code") != "code" or params.get("client_id") != state.credentials.client_id:
        return 400, _page("Unknown app", "<p>This sign-in link is not for an app Acme Helpdesk knows.</p>")
    if not (params.get("redirect_uri") or "").startswith(("http://", "https://")):
        return 400, _page("Unknown app", "<p>The sign-in link has no valid return address.</p>")
    hidden = "".join(
        f'<input type="hidden" name="{name}" value="{html.escape(params.get(name, ""))}">' for name in _SIGN_IN_FIELDS
    )
    scope = html.escape(params.get("scope") or "tickets:read tickets:write")
    message = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return (401 if error else 200), _page("Sign in to Acme Helpdesk", f"""
<p>An app wants to use Acme Helpdesk as you, with access to <code>{scope}</code>.</p>
{message}
<form method="post" action="/oauth/authorize">{hidden}
  <label>Username <input name="username" value="{html.escape(state.credentials.username)}"></label>
  <label>Password <input name="password" type="password" placeholder="acme-demo-password"></label>
  <button name="decision" value="allow">Allow</button>
  <button name="decision" value="deny">Deny</button>
</form>""")


def finish_sign_in(form: dict[str, str], state: State) -> tuple[int, Any]:
    """Allow: a one-time code back to the app's redirect URI. Deny: access_denied."""
    status, page = sign_in_page(form, state)
    if status != 200:
        return status, page
    redirect, app_state = form["redirect_uri"], form.get("state", "")
    if form.get("decision") != "allow":
        return 302, _with_query(redirect, {"error": "access_denied", "state": app_state})
    if (form.get("username"), form.get("password")) != (state.credentials.username, state.credentials.password):
        return sign_in_page(form, state, error="Wrong username or password.")
    code = secrets.token_urlsafe(24)
    state.auth_codes[code] = {
        "client_id": form["client_id"], "redirect_uri": redirect, "challenge": form.get("code_challenge", ""),
        "expires_at": time.time() + AUTH_CODE_TTL_SECONDS,
    }
    return 302, _with_query(redirect, {"code": code, "state": app_state})


def home_page() -> bytes:
    rows = "".join(
        f"<tr><td><code>{m}</code></td><td><code>{html.escape(t)}</code></td></tr>" for m, t, _ in ROUTES
    )
    return _page("Acme Helpdesk API", f"""
<p>A test API for PipesHub API Toolsets. It is running.</p>
<ul>
  <li><a href="/openapi.yaml">The OpenAPI spec</a></li>
  <li><a href="/announcements">Announcements</a> (needs no credentials)</li>
  <li><a href="/__fixture__/calls">Every call received so far</a></li>
</ul>
<table>{rows}</table>
<p>Credentials: API key <code>acme-demo-key</code>, bearer token <code>acme-demo-token</code>, basic
<code>acme</code> / <code>acme-demo-password</code>, OAuth client <code>acme-client</code> /
<code>acme-client-secret</code>.</p>""")


def _with_query(url: str, params: dict[str, str]) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}}
label{{display:block;margin:.75rem 0}}input{{display:block;width:100%;padding:.4rem}}
button{{margin:.75rem .5rem 0 0;padding:.5rem 1rem}}.error{{color:#b42318}}</style></head>
<body><h1>{title}</h1>{body}</body></html>""".encode()


# ---------------------------------------------------------------------------
# API handlers: each takes (state, path params, query, json body) and returns
# (status, body). They run with the state lock held.
# ---------------------------------------------------------------------------


def list_tickets(state: State, _p: dict[str, str], query: dict[str, list[str]], _b: object) -> tuple[int, Any]:
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


def create_ticket(state: State, _p: dict[str, str], _q: dict[str, list[str]], body: object, *, idempotency_key: str | None = None) -> tuple[int, Any]:
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


def get_ticket(state: State, params: dict[str, str], _q: dict[str, list[str]], _b: object) -> tuple[int, Any]:
    return 200, _ticket(state, params["ticketId"])


def update_ticket(state: State, params: dict[str, str], _q: dict[str, list[str]], body: object) -> tuple[int, Any]:
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


def add_comment(state: State, params: dict[str, str], _q: dict[str, list[str]], body: object) -> tuple[int, Any]:
    ticket = _ticket(state, params["ticketId"])
    text = _require_object(body).get("body")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(422, "Invalid body", {"body": "required, non-empty string"})
    comment = {"id": f"C-{state.next_comment}", "author": "api", "body": text.strip()}
    state.next_comment += 1
    ticket["comments"].append(comment)
    return 201, comment


def current_user(_s: State, _p: dict[str, str], _q: dict[str, list[str]], _b: object) -> tuple[int, Any]:
    return 200, {"username": "api", "displayName": "Acme API user", "team": "Support Platform"}


def list_announcements(_s: State, _p: dict[str, str], _q: dict[str, list[str]], _b: object) -> tuple[int, Any]:
    return 200, {"announcements": ANNOUNCEMENTS}


Handler = Callable[..., tuple[int, Any]]

# (method, path template, handler). The contract test checks this table against openapi.yaml.
ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", "/tickets", list_tickets),
    ("POST", "/tickets", create_ticket),
    ("GET", "/tickets/{ticketId}", get_ticket),
    ("PATCH", "/tickets/{ticketId}", update_ticket),
    ("POST", "/tickets/{ticketId}/comments", add_comment),
    ("GET", "/users/me", current_user),
    ("GET", "/announcements", list_announcements),
]
# Operations the spec marks `security: []`.
PUBLIC_HANDLERS: frozenset[Handler] = frozenset({list_announcements})

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


def _check_enum(field: str, value: object, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ApiError(422, "Invalid body", {field: f"must be one of {list(allowed)}"})


def _require_object(body: object) -> dict[str, Any]:
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

        def log_message(self, format: str, *args: object) -> None:
            pass

        def _send(
            self, status: int, body: object = None, content_type: str = "application/json", location: str | None = None,
        ) -> None:
            if body is None:
                data = b""
            elif isinstance(body, bytes):
                data = body
            else:
                data = json.dumps(body).encode()
            self.send_response(status)
            if data:
                self.send_header("Content-Type", content_type)
            if location:
                self.send_header("Location", location)
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
                    # Access tokens only: a refresh token still gets a new one.
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
            if (method, url.path) == ("GET", "/"):
                self._send(200, home_page(), "text/html; charset=utf-8")
                return
            if (method, url.path) == ("GET", "/openapi.yaml"):
                self._send(200, served_spec(state), "application/yaml")
                return
            query = parse_qs(url.query, keep_blank_values=True)
            if url.path == "/oauth/authorize" and method in ("GET", "POST"):
                fields = query if method == "GET" else parse_qs(raw.decode(errors="replace"), keep_blank_values=True)
                with state.lock:
                    status, result = (sign_in_page if method == "GET" else finish_sign_in)(
                        {k: v[0] for k, v in fields.items()}, state)
                if status == 302:
                    self._send(302, None, location=result)
                else:
                    self._send(status, result, "text/html; charset=utf-8")
                return
            try:
                with state.lock:
                    if (method, url.path) == ("POST", "/oauth/token"):
                        form = parse_qs(raw.decode(errors="replace"))
                        self._send(200, issue_token(form, self.headers, state))
                        return
                    handler, params = match_route(method, url.path)
                    scheme = "none" if handler in PUBLIC_HANDLERS else authenticate(self.headers, query, state)
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

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

    return RequestHandler


def _parse_json(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as err:
        raise ApiError(400, "Body is not valid JSON") from err


def served_spec(state: State) -> bytes:
    """The spec, with the sign-in page at the public address when one is set."""
    spec = SPEC_PATH.read_text()
    if state.public_url:
        spec = spec.replace("authorizationUrl: /oauth/authorize", f"authorizationUrl: {state.public_url}/oauth/authorize")
    return spec.encode()


def serve(
    host: str, port: int, credentials: Credentials | None = None, *,
    token_ttl: int = TOKEN_TTL_SECONDS, public_url: str | None = None,
) -> ThreadingHTTPServer:
    state = State(credentials or Credentials(), token_ttl=token_ttl, public_url=public_url)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]  # tests reach the state through the server
    return server


def main() -> None:
    defaults = Credentials()
    parser = argparse.ArgumentParser(description="Acme Helpdesk reference API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--token-ttl", type=int, default=TOKEN_TTL_SECONDS, help="OAuth access token lifetime, seconds")
    parser.add_argument("--public-url", help="Address a browser uses to reach this server (for the sign-in page)")
    for field, value in vars(defaults).items():
        parser.add_argument(f"--{field.replace('_', '-')}", default=value)
    args = parser.parse_args()
    credentials = Credentials(**{field: getattr(args, field) for field in vars(defaults)})
    serve(args.host, args.port, credentials, token_ttl=args.token_ttl, public_url=args.public_url).serve_forever()


if __name__ == "__main__":
    main()
