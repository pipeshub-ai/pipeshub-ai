"""The e2e lanes' SaaS: an MCP server (streamable HTTP) and a Jira Data Center REST API, each counting its effects.

* **MCP** (E2E2): the org attaches it as an MCP server instance with no auth (``authMode: none``); ``lookup_note`` and
  ``search_documents`` (the documents a test adds, ANSWER-CONTRACT-SOURCES) read,
  ``create_ticket`` writes. An engine reaches it only as opencode → egress proxy → the worker's tool gateway → the
  worker's MCP client → here. It runs on the ``pipeshub`` network only: no sandbox can reach it.
* **Jira Data Center** (QA-6, the ``toolset`` family): the product's own ``Jira Data Center`` toolset with a personal
  access token, ``baseUrl`` pointing here. ``GET /rest/api/2/issue/{key}`` serves one private issue and
  ``POST /rest/api/2/issue`` creates one. Every request must carry the configured token as a Bearer, as Jira refuses
  a missing or wrong one (401); each effect records the digest of the credential it was made with, never the value.

* **GitHub** (WP-26, the repository picker): the GitHub REST reads the picker makes with a person's GitHub toolset
  token, at ``/api/v3`` (the connectors service's ``GITHUB_API_URL``). ``GET /user/repos`` lists three repositories,
  ``GET /repos/{owner}/{name}`` and its ``/branches`` describe them. Every request must carry
  ``FAKE_SAAS_GITHUB_TOKEN`` as a Bearer (or ``token``), as GitHub refuses a missing or wrong one (401). Reads only.

* **Slack** (R35-SLACK): the Web API the Slack bot and Node's Slack notices call, and signed event deliveries to
  the bot (``fake_slack.py``).

* **SearXNG** (R31-SECKEY): a self-hosted web search an admin points PipesHub at, ``GET /searxng/search?format=json``,
  answering two results for any query. The engine reaches it only as engine → egress proxy → the worker's tool gateway
  → the web search tool → here.

Each write records the digest of its arguments and the caller's address, so a test reads "the effect ran once" at the
effect itself. Control routes (behind a token) list and reset the record.

A test may name values that must reach this server only from PipesHub's own services (``/__control/canaries``, as the
scripted model's): every request is searched for them (its headers, query and body) as it arrives, and only the hit
is kept, by digest, with the path, the ``server`` parameter and the caller's address. A hit proves an upstream really
received a planted credential, so a scan that finds none of them in a sandbox cannot pass because none was used.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_slack

CONTROL_TOKEN = os.environ["FAKE_SAAS_CONTROL_TOKEN"]
# The token the lane configures on the toolset instance (a canary: it must never reach a frame, log or journal).
JIRA_TOKEN = os.environ.get("FAKE_SAAS_JIRA_TOKEN", "")
# The token the human stack seeds on its GitHub toolset sign-in (deployment/e2e/seed_github.py).
GITHUB_TOKEN = os.environ.get("FAKE_SAAS_GITHUB_TOKEN", "")
GITHUB_REPOSITORIES: dict[str, dict[str, Any]] = {
    "acme/widgets": {"private": True, "pushed_at": "2026-10-30T08:00:00Z", "branches": ["main", "release/1.x"]},
    "acme/widgets-docs": {"private": False, "pushed_at": "2026-10-29T09:00:00Z", "branches": ["main"]},
    "octo-dev/sandbox": {"private": False, "pushed_at": "2026-10-22T12:00:00Z", "branches": ["main", "spike"]},
}
EFFECTS: list[dict[str, Any]] = []
READS: list[dict[str, Any]] = []
# When each tools/list request was served, by the ``server`` query parameter a client's URL names (none: e2esaas). One process
# serves the lane's MCP servers, so a journey tells them apart by the URL each instance was registered with (R31-JOURNEY:
# a job that inherits its parent's first-use pin lists the server no second time).
LISTINGS: dict[str, list[float]] = {}
# Values a test watches for (``/__control/canaries``): kept in memory only, reported by digest.
CANARIES: list[bytes] = []
CANARY_HITS: list[dict[str, Any]] = []
SEARCH_RESULTS = (
    {"url": "https://handbook.example/travel", "title": "Travel policy", "content": "Book travel 14 days ahead."},
    {"url": "https://handbook.example/expenses", "title": "Expenses", "content": "File expenses within 30 days."},
)
PRIVATE_ISSUE = {
    "key": "SEC-7",
    "summary": "Quarterly access review (restricted)",
    "description": "Restricted: the access review findings for the finance systems.",
}

mcp = MCPServer("e2e-saas")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _record(tool: str, args: object, peer: str | None, credential: str | None = None) -> str:
    ticket = f"TCK-{len(EFFECTS) + 1}"
    effect = {"tool": tool, "argsDigest": _digest(args), "ticket": ticket, "peer": peer, "at": time.time()}
    if credential is not None:
        effect["credentialDigest"] = _digest(credential)
    EFFECTS.append(effect)
    print(json.dumps({"fake_saas": {k: v for k, v in effect.items() if k != "at"}}), flush=True)
    return ticket


def _peer(ctx: Context) -> str | None:
    request = ctx.request_context.request
    return request.client.host if request is not None and request.client is not None else None


# The team's own notes and documents stay inside its space: declared closed-world, as an internal server would
# (SEC-B-02: an MCP tool without openWorldHint: false is an outside destination).
@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def lookup_note(topic: str, ctx: Context) -> str:
    """Look up the team's private note on a topic."""
    READS.append({"tool": "lookup_note", "peer": _peer(ctx), "at": time.time()})
    return f"Private note on {topic}: the finance review is due on Friday."


# The team's documents ``search_documents`` searches, as a test adds them (``/__control/documents``).
DOCUMENTS: list[dict[str, str]] = []


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def search_documents(query: str, ctx: Context) -> str:
    """Search the team's documents. Returns the matching documents as JSON."""
    READS.append({"tool": "search_documents", "peer": _peer(ctx), "at": time.time()})
    words = [w for w in query.lower().split() if w]
    matches = [d for d in DOCUMENTS if words and all(w in (d["title"] + " " + d["body"]).lower() for w in words)]
    return json.dumps({"results": matches})


# A public lookup service's tool as most third-party servers declare one: read-only, no openWorldHint. What it is asked
# reaches the server's operator, so after a private read the flow guard asks first (SEC-B-02); a journey reads the
# digest of each query it was sent.
@mcp.tool(annotations={"readOnlyHint": True})
async def web_lookup(query: str, ctx: Context) -> str:
    """Look a phrase up on the public web. Returns what the web says about it."""
    READS.append({"tool": "web_lookup", "argsDigest": _digest({"query": query}), "peer": _peer(ctx), "at": time.time()})
    return f"The web knows nothing about {query!r}."


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def create_ticket(title: str, body: str, ctx: Context) -> str:
    """Create a ticket in the team's tracker. Returns the ticket id."""
    return f"created {_record('create_ticket', {'title': title, 'body': body}, _peer(ctx))}"


def _authorized(request: Request) -> bool:
    return request.headers.get("X-E2E-Control") == CONTROL_TOKEN


def _jira_refusal(request: Request) -> JSONResponse | None:
    if not JIRA_TOKEN or request.headers.get("Authorization") != f"Bearer {JIRA_TOKEN}":
        return JSONResponse({"errorMessages": ["You are not authenticated."], "errors": {}}, status_code=401)
    return None


def _host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


@mcp.custom_route("/rest/api/2/myself", methods=["GET"])
async def jira_myself(request: Request) -> JSONResponse:
    return _jira_refusal(request) or JSONResponse({"name": "e2e-bot", "displayName": "E2E Bot", "active": True})


@mcp.custom_route("/rest/api/2/issue/{key}", methods=["GET"])
async def jira_get_issue(request: Request) -> JSONResponse:
    refused = _jira_refusal(request)
    if refused is not None:
        return refused
    key = request.path_params["key"]
    if key != PRIVATE_ISSUE["key"]:
        return JSONResponse({"errorMessages": ["Issue Does Not Exist"], "errors": {}}, status_code=404)
    READS.append({"tool": "jira.get_issue", "peer": _host(request), "at": time.time()})
    return JSONResponse({"id": "10007", "key": key, "fields": {
        "summary": PRIVATE_ISSUE["summary"], "description": PRIVATE_ISSUE["description"],
        "status": {"name": "Open"}, "issuetype": {"name": "Task"}, "project": {"key": "SEC"},
    }})


@mcp.custom_route("/rest/api/2/issue", methods=["POST"])
async def jira_create_issue(request: Request) -> JSONResponse:
    refused = _jira_refusal(request)
    if refused is not None:
        return refused
    body = await request.json()
    fields = body.get("fields") if isinstance(body, dict) else None
    if not isinstance(fields, dict) or not fields.get("summary") or not (fields.get("project") or {}).get("key"):
        return JSONResponse({"errorMessages": [], "errors": {"summary": "You must specify a summary of the issue."}},
                            status_code=400)
    ticket = _record("jira.create_issue", fields, _host(request), request.headers.get("Authorization"))
    number = ticket.split("-", 1)[1]
    return JSONResponse({"id": f"2{number}", "key": f"{fields['project']['key']}-{number}"}, status_code=201)


def _github_refusal(request: Request) -> JSONResponse | None:
    sent = request.headers.get("Authorization", "")
    if not GITHUB_TOKEN or sent not in (f"Bearer {GITHUB_TOKEN}", f"token {GITHUB_TOKEN}"):
        return JSONResponse({"message": "Bad credentials", "status": "401"}, status_code=401)
    return None


def _github_repo(name: str) -> dict[str, Any]:
    known = GITHUB_REPOSITORIES[name]
    owner, repo = name.split("/")
    return {
        "name": repo, "full_name": name, "owner": {"login": owner}, "private": known["private"],
        "html_url": f"https://github.com/{name}", "clone_url": f"https://github.com/{name}.git",
        "default_branch": "main", "pushed_at": known["pushed_at"], "updated_at": known["pushed_at"],
    }


@mcp.custom_route("/api/v3/user", methods=["GET"])
async def github_user(request: Request) -> JSONResponse:
    return _github_refusal(request) or JSONResponse({"login": "octo-dev", "id": 7, "type": "User"})


@mcp.custom_route("/api/v3/user/repos", methods=["GET"])
async def github_user_repos(request: Request) -> JSONResponse:
    refused = _github_refusal(request)
    if refused is not None:
        return refused
    page = int(request.query_params.get("page", "1") or "1")
    READS.append({"tool": "github.user_repos", "peer": _host(request), "at": time.time()})
    return JSONResponse([_github_repo(name) for name in GITHUB_REPOSITORIES] if page == 1 else [])


@mcp.custom_route("/api/v3/repos/{owner}/{repo}", methods=["GET"])
async def github_repo(request: Request) -> JSONResponse:
    refused = _github_refusal(request)
    if refused is not None:
        return refused
    name = f"{request.path_params['owner']}/{request.path_params['repo']}"
    if name not in GITHUB_REPOSITORIES:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    return JSONResponse(_github_repo(name))


@mcp.custom_route("/api/v3/repos/{owner}/{repo}/branches", methods=["GET"])
async def github_branches(request: Request) -> JSONResponse:
    refused = _github_refusal(request)
    if refused is not None:
        return refused
    name = f"{request.path_params['owner']}/{request.path_params['repo']}"
    if name not in GITHUB_REPOSITORIES:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    return JSONResponse([{"name": b, "protected": b == "main"} for b in GITHUB_REPOSITORIES[name]["branches"]])


@mcp.custom_route("/searxng/search", methods=["GET"])
async def searxng_search(request: Request) -> JSONResponse:
    if request.query_params.get("format") != "json":
        return JSONResponse({"error": "format=json only"}, status_code=403)
    READS.append({"tool": "searxng.search", "peer": _host(request), "at": time.time()})
    return JSONResponse({"query": request.query_params.get("q", ""), "results": list(SEARCH_RESULTS)})


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request) -> JSONResponse:
    """An MCP server's OAuth token endpoint that refuses every grant: a refresh is seen (by the canary watch) and fails,
    so the access token the lane planted stays the one in use."""
    READS.append({"tool": "oauth.token", "peer": _host(request), "at": time.time()})
    return JSONResponse({"error": "invalid_grant"}, status_code=400)


@mcp.custom_route("/__control/canaries", methods=["GET", "POST"])
async def canaries(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"error": "control token required"}, status_code=401)
    if request.method == "POST":
        values = (await request.json()).get("values") or []
        CANARIES[:] = [str(v).encode() for v in values if v]
        CANARY_HITS.clear()
    return JSONResponse({"watching": len(CANARIES), "hits": CANARY_HITS})


@mcp.custom_route("/__control/effects", methods=["GET"])
async def effects(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"error": "control token required"}, status_code=401)
    return JSONResponse({"effects": EFFECTS, "reads": READS, "listings": LISTINGS})


@mcp.custom_route("/__control/documents", methods=["POST"])
async def add_document(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"error": "control token required"}, status_code=401)
    body = await request.json()
    DOCUMENTS.append({"title": str(body.get("title") or ""), "body": str(body.get("body") or "")})
    return JSONResponse({"documents": len(DOCUMENTS)})


@mcp.custom_route("/__control/reset", methods=["POST"])
async def reset(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"error": "control token required"}, status_code=401)
    EFFECTS.clear()
    READS.clear()
    DOCUMENTS.clear()
    LISTINGS.clear()
    return JSONResponse({"ok": True})


# R35-SLACK: the scripted Slack workspace (fake_slack.py) on the same process.
fake_slack.register(mcp, _authorized)


@mcp.custom_route("/healthz", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _counting_listings(inner: Any) -> Any:
    """The MCP app, with each JSON-RPC ``tools/list`` it is sent counted under the URL's ``server`` parameter."""
    from urllib.parse import parse_qs

    async def counted(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await inner(scope, receive, send)
            return
        server = (parse_qs(scope.get("query_string", b"").decode()).get("server") or ["e2esaas"])[0]

        async def tapped() -> Any:
            message = await receive()
            if message.get("type") == "http.request" and b'"tools/list"' in message.get("body", b""):
                LISTINGS.setdefault(server, []).append(time.time())
            return message

        await inner(scope, tapped, send)

    return counted


def _watching_canaries(inner: Any) -> Any:
    """Every request but a control one searched for the watched canaries: its headers, its query and its whole body,
    read before the route runs (a route that never reads its body is searched too) and handed on unchanged."""
    from urllib.parse import parse_qs

    async def watched(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not CANARIES or scope.get("path", "").startswith("/__control/"):
            await inner(scope, receive, send)
            return
        query = scope.get("query_string", b"")
        seen = bytearray(query + b"\n" + b"\n".join(v for _, v in scope.get("headers") or []))
        messages: list[Any] = []
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            seen.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        client = scope.get("client")
        for canary in CANARIES:
            if canary in seen:
                CANARY_HITS.append({"path": scope.get("path"),
                                    "server": (parse_qs(query.decode()).get("server") or ["e2esaas"])[0],
                                    "peer": client[0] if client else None,
                                    "canaryDigest": hashlib.sha256(canary).hexdigest()[:16], "at": time.time()})

        async def replay() -> Any:
            return messages.pop(0) if messages else await receive()

        await inner(scope, replay, send)

    return watched


def app() -> Any:
    # host 0.0.0.0: the SDK guards Host headers only for a loopback bind; the lane reaches this by service name.
    return _watching_canaries(_counting_listings(mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    uvicorn.run(app(), host="0.0.0.0", port=port, log_level="warning")
