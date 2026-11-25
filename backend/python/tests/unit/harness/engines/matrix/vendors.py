"""TG-16: local Slack, Google, Microsoft and image-model services for the TG-16 cells: the shapes the product's toolsets
read, nothing more, each refusing a request without its own credential (as the vendor does) and keeping every
request it saw (vendor, method, path, authorization).

``routed`` points the vendors' own SDKs at it: Slack's ``WebClient`` and Google's discovery client by the endpoint
option each SDK has; MSAL's sign-in reads and Microsoft Graph's calls by rewriting the request's origin after the
SDK built it, so Graph's allowed-hosts check and the bearer it attaches are what they are in production. The
toolset code, the client factories and the credential resolution between them all run as they ship.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

SLACK_TOKEN = "xoxp-tg16-not-a-real-token-0123456789"
GOOGLE_TOKEN = "ya29.tg16-not-a-real-token-0123456789"
IMAGE_KEY = "sk-tg16-image-not-a-real-key-0123456789"
GRAPH_OID = "0f1e2d3c-tg16-4b5a-9687-a1b2c3d4e5f6"


def _jwt(claims: dict[str, str]) -> str:
    def part(doc: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(doc).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


# Graph's toolset reads the signed-in user's object id from the access token (it addresses ``/users/{oid}``).
GRAPH_TOKEN = _jwt({"oid": GRAPH_OID, "tid": "tenant-tg16", "upn": "alice@tg16.example"})

SLACK_CHANNEL = "C0TG16RELEASE"
SLACK_USER = "U0TG16ALICE"
SLACK_MARKER = "SIERRA4106"
GMAIL_ID = "18f0tg16gmail01"
GMAIL_MARKER = "GOLF5523"
DRIVE_FILE = "1TG16driveFileId"
DRIVE_MARKER = "DELTA7781"
OUTLOOK_ID = "AAMkTG16outlook01"
OUTLOOK_MARKER = "MIKE6631"
ONEDRIVE_ID = "b!TG16onedrive"
ONEDRIVE_MARKER = "OSCAR2290"
# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# A vendor's host -> the path prefix its requests take on the fake.
HOSTS = {
    "gmail.googleapis.com": "/google/gmail",
    "www.googleapis.com": "/google/www",
    "login.microsoftonline.com": "/microsoft/login",
    "graph.microsoft.com": "/microsoft/graph",
}


@dataclass
class FakeVendors:
    base: str = ""
    # (vendor, method, path, authorization) of every request.
    seen: list[tuple[str, str, str, str]] = field(default_factory=list[tuple[str, str, str, str]])
    prompts: list[str] = field(default_factory=list[str])

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/slack/api/{method}", self._slack)
        app.router.add_get("/google/gmail/gmail/v1/users/me/messages", self._gmail_list)
        app.router.add_get("/google/gmail/gmail/v1/users/me/messages/{id}", self._gmail_get)
        app.router.add_get("/google/www/drive/v3/files", self._drive_list)
        app.router.add_get("/microsoft/login/{tenant}/v2.0/.well-known/openid-configuration", self._openid)
        app.router.add_get("/microsoft/graph/v1.0/users/{oid}/messages/{id}", self._outlook_message)
        app.router.add_get("/microsoft/graph/v1.0/users/{oid}/drives", self._onedrive_drives)
        app.router.add_post("/images/v1/images/generations", self._image)
        app.router.add_route("*", "/{tail:.*}", self._other)
        return app

    def requests(self, vendor: str) -> list[tuple[str, str, str]]:
        return [(method, path, auth) for v, method, path, auth in self.seen if v == vendor]

    def _saw(self, vendor: str, request: web.Request, token: str | None) -> bool:
        authorization = request.headers.get("Authorization", "")
        self.seen.append((vendor, request.method, request.path, authorization))
        return token is None or authorization == f"Bearer {token}"

    async def _slack(self, request: web.Request) -> web.Response:
        if not self._saw("slack", request, SLACK_TOKEN):
            return web.json_response({"ok": False, "error": "invalid_auth"})
        method = request.match_info["method"]
        form = dict(await request.post())
        if method == "conversations.history" and form.get("channel") == SLACK_CHANNEL:
            message = {
                "type": "message", "user": SLACK_USER, "ts": "1790000000.000100",
                "text": f"Marker {SLACK_MARKER}: the release train leaves on Thursday.",
            }
            return web.json_response({"ok": True, "messages": [message], "has_more": False})
        if method == "users.info" and form.get("user") == SLACK_USER:
            profile = {"display_name": "alice", "real_name": "Alice Example", "email": "alice@tg16.example"}
            return web.json_response({"ok": True, "user": {"id": SLACK_USER, "name": "alice", "profile": profile}})
        return web.json_response({"ok": False, "error": "channel_not_found"})

    async def _gmail_list(self, request: web.Request) -> web.Response:
        if not self._saw("google", request, GOOGLE_TOKEN):
            return _google_unauthorized()
        return web.json_response({"messages": [{"id": GMAIL_ID, "threadId": GMAIL_ID}], "resultSizeEstimate": 1})

    async def _gmail_get(self, request: web.Request) -> web.Response:
        if not self._saw("google", request, GOOGLE_TOKEN):
            return _google_unauthorized()
        if request.match_info["id"] != GMAIL_ID:
            return web.json_response({"error": {"code": 404, "message": "Not Found"}}, status=404)
        headers = [
            {"name": "Subject", "value": f"Marker {GMAIL_MARKER}: the quarterly invoice"},
            {"name": "From", "value": "billing@vendor.example"},
            {"name": "To", "value": "alice@tg16.example"},
            {"name": "Date", "value": "Mon, 2 Nov 2026 09:00:00 +0000"},
        ]
        return web.json_response({
            "id": GMAIL_ID, "threadId": GMAIL_ID, "labelIds": ["INBOX"],
            "snippet": "The invoice is attached.", "payload": {"headers": headers},
        })

    async def _drive_list(self, request: web.Request) -> web.Response:
        if not self._saw("google", request, GOOGLE_TOKEN):
            return _google_unauthorized()
        file = {"id": DRIVE_FILE, "name": f"Marker {DRIVE_MARKER} roadmap.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        return web.json_response({"kind": "drive#fileList", "files": [file]})

    async def _openid(self, request: web.Request) -> web.Response:
        _ = self._saw("microsoft", request, None)
        tenant = request.match_info["tenant"]
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        return web.json_response({
            "authorization_endpoint": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            "token_endpoint": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            "issuer": issuer,
        })

    async def _outlook_message(self, request: web.Request) -> web.Response:
        if not self._saw("microsoft", request, GRAPH_TOKEN):
            return _graph_unauthorized()
        if request.match_info["oid"] != GRAPH_OID or request.match_info["id"] != OUTLOOK_ID:
            return web.json_response({"error": {"code": "ErrorItemNotFound", "message": "Not found"}}, status=404)
        return web.json_response({
            "id": OUTLOOK_ID,
            "subject": f"Marker {OUTLOOK_MARKER}: the board pack",
            "bodyPreview": "The board pack is ready.",
            "body": {"contentType": "text", "content": "The board pack is ready."},
            "from": {"emailAddress": {"name": "Board office", "address": "board@tg16.example"}},
            "toRecipients": [{"emailAddress": {"name": "Alice", "address": "alice@tg16.example"}}],
        })

    async def _onedrive_drives(self, request: web.Request) -> web.Response:
        if not self._saw("microsoft", request, GRAPH_TOKEN):
            return _graph_unauthorized()
        if request.match_info["oid"] != GRAPH_OID:
            return web.json_response({"error": {"code": "ErrorInvalidUser", "message": "Invalid user"}}, status=404)
        drive = {"id": ONEDRIVE_ID, "name": f"Marker {ONEDRIVE_MARKER} OneDrive", "driveType": "business"}
        return web.json_response({"value": [drive]})

    async def _image(self, request: web.Request) -> web.Response:
        if not self._saw("images", request, IMAGE_KEY):
            return web.json_response({"error": {"message": "Incorrect API key provided"}}, status=401)
        body = await request.json()
        self.prompts.append(str(body.get("prompt")))
        count = int(body.get("n") or 1)
        return web.json_response({
            "created": 1790000000, "data": [{"b64_json": base64.b64encode(PNG).decode()} for _ in range(count)],
        })

    async def _other(self, request: web.Request) -> web.Response:
        _ = self._saw("other", request, None)
        return web.json_response({"error": f"not served: {request.method} {request.path}"}, status=404)


def _google_unauthorized() -> web.Response:
    return web.json_response({"error": {"code": 401, "message": "Request had invalid authentication credentials."}}, status=401)


def _graph_unauthorized() -> web.Response:
    return web.json_response({"error": {"code": "InvalidAuthenticationToken", "message": "Access token is empty."}}, status=401)


def rerouted(url: str, base: str) -> str:
    """``url`` on the fake when its host is a vendor's, else unchanged."""
    parts = urlsplit(url)
    prefix = HOSTS.get(parts.hostname or "")
    if prefix is None:
        return url
    fake = urlsplit(base)
    return urlunsplit((fake.scheme, fake.netloc, prefix + parts.path, parts.query, parts.fragment))


@contextlib.contextmanager
def routed(vendors: FakeVendors) -> Iterator[None]:
    """The vendors' SDKs, as the toolsets build them, talk to ``vendors``."""
    import httpx
    import msal
    import requests
    from googleapiclient import discovery, discovery_cache
    from slack_sdk import WebClient

    from app.sources.client.microsoft import microsoft

    base = vendors.base

    def slack(*args: Any, **kwargs: Any) -> WebClient:  # noqa: ANN401
        return WebClient(*args, base_url=f"{base}/slack/api/", **kwargs)

    def google(service: str, version: str, **kwargs: Any) -> Any:  # noqa: ANN401
        # The service's own endpoint (its bundled discovery document's), on the fake.
        doc = json.loads(discovery_cache.get_static_doc(service, version) or "{}")
        endpoint = rerouted(str(doc["rootUrl"]) + str(doc.get("servicePath") or ""), base)
        return discovery.build(service, version, client_options={"api_endpoint": endpoint}, **kwargs)

    class Login(requests.Session):
        def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> requests.Response:  # noqa: ANN401
            return super().request(method, rerouted(str(url), base), *args, **kwargs)

    real_msal = msal.ConfidentialClientApplication

    def confidential(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return real_msal(*args, http_client=Login(), **kwargs)

    real_adapter = microsoft.send_safe_graph_adapter

    def graph(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        adapter = real_adapter(*args, **kwargs)

        async def to_fake(request: httpx.Request) -> None:
            request.url = httpx.URL(rerouted(str(request.url), base))

        client: httpx.AsyncClient = adapter._http_client
        client.event_hooks = {**client.event_hooks, "request": [*client.event_hooks["request"], to_fake]}
        return adapter

    with (
        patch("app.sources.client.slack.slack.WebClient", slack),
        patch("app.sources.client.google.google.build", google),
        patch.object(msal, "ConfidentialClientApplication", confidential),
        patch.object(microsoft, "send_safe_graph_adapter", graph),
    ):
        yield


@contextlib.asynccontextmanager
async def serving() -> AsyncIterator[FakeVendors]:
    """The fake on an event loop of its own thread: the Slack, Google and MSAL SDKs are synchronous, and a toolset
    may call one on the worker's loop, which would then never serve an in-loop fake's answer."""
    vendors = FakeVendors()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="tg16-vendors", daemon=True)
    thread.start()

    async def start() -> web.AppRunner:
        runner = web.AppRunner(vendors.app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        return runner

    runner = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(start(), loop))
    vendors.base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        yield vendors
    finally:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(runner.cleanup(), loop))
        _ = loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()


__all__ = [
    "DRIVE_MARKER",
    "GMAIL_MARKER",
    "GOOGLE_TOKEN",
    "GRAPH_TOKEN",
    "IMAGE_KEY",
    "ONEDRIVE_MARKER",
    "OUTLOOK_ID",
    "OUTLOOK_MARKER",
    "SLACK_CHANNEL",
    "SLACK_MARKER",
    "SLACK_TOKEN",
    "FakeVendors",
    "routed",
    "serving",
]
