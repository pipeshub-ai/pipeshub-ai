"""A fake Anthropic login and API server for SIGNIN-1: no real account, no network beyond it.

Runs inside a container on an internal Docker network; the Claude Code container resolves Anthropic's hosts to it
(``--add-host``) and trusts its throwaway CA through ``NODE_EXTRA_CA_CERTS``, so the unmodified binary runs its own
login against it. Every request is appended to ``/log/requests.jsonl``. ``GET /control?key=value`` switches what it
answers: ``plan`` (the organization type), ``messages`` (``ok``, ``expired``, ``limit``), ``refresh`` and
``revoke`` (``fail``). On plain HTTP port 8097 it is also the tool gateway (``tool-gateway`` resolves to it): an MCP
server with no tools, which the adapter waits for before any turn (E2E3-F6).
Standard library only: the container is the Claude Code tools image, which has CPython.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = {
    "plan": os.environ.get("FAKE_PLAN", "claude_max"),
    "messages": "ok",
    "refresh": "ok",
}
LOG = "/log/requests.jsonl"
# Shaped as the binary's own prefixes (it carries the string ``sk-ant-oat``); FAKE marks them as this server's.
ACCESS = "sk-ant-oat01-FAKEACCESS" + "0" * 40
REFRESH = "sk-ant-ort01-FAKEREFRESH" + "0" * 40
GOOD_CODE = "FAKECODE123"
ACCOUNT = "00000000-0000-4000-8000-0000000000a1"
ORG = "00000000-0000-4000-8000-0000000000b2"
PLAN_NAMES = {
    "claude_max": "Max",
    "claude_pro": "Pro",
    "claude_team": "Team",
    "claude_enterprise": "Enterprise",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log(self, body: object) -> None:
        with open(LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "host": self.headers.get("Host"),
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "body": body,
                    }
                )
                + "\n"
            )

    def _send(
        self, code: int, obj: object, extra: dict[str, str] | None = None
    ) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> object:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode(errors="replace") if n else ""
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path == "/control":
            MODE.update({k: v[0] for k, v in urllib.parse.parse_qs(query).items()})
            return self._send(200, MODE)
        self._log(None)
        if path == "/api/oauth/profile":
            if self.headers.get("Authorization") != f"Bearer {ACCESS}":
                return self._send(
                    401,
                    {
                        "type": "error",
                        "error": {
                            "type": "authentication_error",
                            "message": "Invalid bearer token",
                        },
                    },
                )
            plan = MODE["plan"]
            return self._send(
                200,
                {
                    "account": {
                        "uuid": ACCOUNT,
                        "email": "ada@example.com",
                        "email_address": "ada@example.com",
                        "full_name": "Ada Example",
                        "display_name": "Ada",
                        "has_claude_max": plan == "claude_max",
                        "has_claude_pro": plan == "claude_pro",
                    },
                    "organization": {
                        "uuid": ORG,
                        "name": "Example Org",
                        "organization_type": plan,
                        "rate_limit_tier": "default_claude_max_20x",
                        "billing_type": "stripe_subscription",
                        "plan_display_name": PLAN_NAMES.get(plan, "Max"),
                    },
                },
            )
        if path == "/api/oauth/claude_cli/roles":
            return self._send(
                200,
                {
                    "organization_role": "admin",
                    "workspace_role": None,
                    "organization_name": "Example Org",
                },
            )
        return self._send(
            404,
            {
                "type": "error",
                "error": {"type": "not_found_error", "message": "fake: " + path},
            },
        )

    def do_POST(self) -> None:
        body = self._body()
        self._log(body)
        path = self.path.partition("?")[0]
        grant = body.get("grant_type") if isinstance(body, dict) else None
        if path == "/v1/oauth/token":
            if grant == "authorization_code" and body.get("code") == GOOD_CODE:
                return self._send(200, self._tokens())
            if grant == "refresh_token" and MODE["refresh"] != "fail":
                return self._send(200, self._tokens())
            return self._send(
                400 if grant == "refresh_token" else 401,
                {
                    "error": "invalid_grant",
                    "error_description": "Invalid or expired code",
                },
            )
        if path == "/v1/oauth/token/revoke":
            if MODE.get("revoke") == "fail":
                return self._send(503, {"error": "temporarily_unavailable"})
            return self._send(200, {})
        if path == "/v1/messages":
            if MODE["messages"] == "expired":
                return self._send(
                    401,
                    {
                        "type": "error",
                        "error": {
                            "type": "authentication_error",
                            "message": "OAuth token has expired. Please obtain a new token or refresh your existing token.",
                        },
                    },
                )
            if MODE["messages"] == "limit":
                return self._send(
                    429,
                    {
                        "type": "error",
                        "error": {
                            "type": "rate_limit_error",
                            "message": "This request would exceed your account's rate limit. Please try again later.",
                        },
                    },
                    {
                        "anthropic-ratelimit-unified-status": "rejected",
                        "anthropic-ratelimit-unified-reset": "1792360800",
                        "anthropic-ratelimit-unified-representative-claim": "five_hour",
                        "retry-after": "3600",
                    },
                )
            return self._send(
                200,
                {
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-fake",
                    "content": [{"type": "text", "text": "hi"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        return self._send(
            404,
            {
                "type": "error",
                "error": {"type": "not_found_error", "message": "fake: " + path},
            },
        )

    def _tokens(self) -> dict[str, object]:
        return {
            "token_type": "Bearer",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "expires_in": 28800,
            "scope": "user:inference user:profile user:sessions:claude_code user:mcp_servers user:file_upload",
            "account": {"uuid": ACCOUNT, "email_address": "ada@example.com"},
            "organization": {"uuid": ORG, "name": "Example Org"},
        }

    def log_message(self, *args: object) -> None:
        sys.stderr.write("fake: " + (str(args[0]) % args[1:]) + "\n")


class ToolGateway(BaseHTTPRequestHandler):
    """MCP Streamable HTTP with no tools: what the run's tool gateway answers for an empty grant."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        result: dict[str, object] = {}
        if message.get("method") == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pipeshub", "version": "1"},
            }
        elif message.get("method") == "tools/list":
            result = {"tools": []}
        data = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return


if __name__ == "__main__":
    tools = ThreadingHTTPServer(("0.0.0.0", 8097), ToolGateway)
    threading.Thread(target=tools.serve_forever, daemon=True).start()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain("/certs/server.pem", "/certs/server.key")
    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
