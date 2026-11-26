"""A stand-in model gateway and Node token authority (dev-only smoke).

On the gateway port it logs and echoes the Authorization it receives. On
``harness-state:3002`` (Node's internal listener) it answers Node's gateway-token route the way Node does
for a leased attempt: worker credential required, a ``pxt_`` token and its expiry.
"""

import json
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

TOKEN_ROUTE = re.compile(r"^/api/v1/agent-harness/internal/runs/[^/]+/gateway-token$")
NODE_PORT = 3002


def _reply(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _drain(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


class Echo(BaseHTTPRequestHandler):
    def _any(self) -> None:
        auth = self.headers.get("Authorization", "")
        _drain(self)
        print("AUTH " + json.dumps({"auth": auth, "path": self.path}), flush=True)
        _reply(self, 200, {"auth": auth, "path": self.path})

    do_GET = do_POST = _any

    def log_message(self, *_: object) -> None:
        pass


class NodeTokens(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        raw = _drain(self)
        if not TOKEN_ROUTE.match(self.path):
            _reply(self, 404, {"error": {"code": "NOT_FOUND"}})
            return
        # SEC-C-10: only the egress proxy's own credential mints a gateway token.
        if self.headers.get("Authorization") != "Bearer " + os.environ["SMOKE_PROXY_CREDENTIAL"]:
            print("TOKEN refused", flush=True)
            _reply(self, 401, {"error": {"code": "UNAUTHENTICATED"}})
            return
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        # Node's body schema is strict: exactly orgId and attemptId.
        if not isinstance(body, dict) or set(body) != {"orgId", "attemptId"} or not all(body.values()):
            print("TOKEN bad-request", flush=True)
            _reply(self, 400, {"error": {"code": "HTTP_BAD_REQUEST"}})
            return
        print("TOKEN issued " + self.path, flush=True)
        _reply(self, 200, {"token": "pxt_" + secrets.token_hex(16), "expiresAt": int((time.time() + 600) * 1000)})

    def log_message(self, *_: object) -> None:
        pass


threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", NODE_PORT), NodeTokens).serve_forever, daemon=True).start()
HTTPServer(("0.0.0.0", int(sys.argv[1])), Echo).serve_forever()
