"""A website and feeds for the Web and RSS connectors to sync from.

Runs as the ``web-fixtures`` service of the integration stack. It serves the
files under ``--seed`` from memory, and the tests change what it serves through
a control API on the same port. A changed or removed page then needs no
filesystem shared between the test runner and this container.

    PUT    /__fixtures__/files/<path>   serve the request body at /<path>
    DELETE /__fixtures__/files/<path>   serve 404 at /<path>
    POST   /__fixtures__/reset          serve the seed files again, with no faults
    GET    /__fixtures__/health         200 once serving
    POST   /__fixtures__/openai/v1/chat/completions
                                        a stand-in OpenAI-compatible model
    PUT    /__fixtures__/faults/<path>  misbehave at /<path>; JSON body, see Fault
    GET    /__fixtures__/faults/<path>  that fault, with how many requests it hit
    DELETE /__fixtures__/faults/<path>  behave again at /<path>

A fault stands in for a real site having a bad moment: rate limiting, a server
error, a slow answer, or a page cut off mid-transfer. Each is set on one path
and for a number of requests, so tests sharing the service cannot trip over
each other's, and ``reset`` clears them all. Faults answer GET only.

The stand-in model lets a test configure an AI model through the product's
own form, health check included, without a paid provider account. It answers
every prompt with the same short reply, streamed or not, and ignores tools.

``{{BASE_URL}}`` in a served text file becomes ``--base-url``, so feeds can
carry absolute links that work from wherever the connector runs.

Standard library only, so the service runs on a stock Python image.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

CONTROL = "/__fixtures__"
STAND_IN_REPLY = "OK"
BASE_URL_TOKEN = b"{{BASE_URL}}"
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/rss+xml; charset=utf-8",
    ".atom": "application/atom+xml; charset=utf-8",
}


def content_type_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _TYPES.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class Site:
    """The files being served, keyed by URL path without the leading slash."""

    def __init__(self, seed: Path) -> None:
        self._seed = seed
        self._lock = threading.Lock()
        self._files: dict[str, tuple[bytes, str]] = {}
        self.reset()

    def reset(self) -> None:
        files = {
            p.relative_to(self._seed).as_posix(): (p.read_bytes(), content_type_for(p.name))
            for p in self._seed.rglob("*")
            if p.is_file()
        }
        with self._lock:
            self._files = files

    def put(self, path: str, body: bytes, content_type: str | None) -> None:
        with self._lock:
            self._files[path] = (body, content_type or content_type_for(path))

    def delete(self, path: str) -> bool:
        with self._lock:
            return self._files.pop(path, None) is not None

    def get(self, path: str) -> tuple[bytes, str] | None:
        with self._lock:
            return self._files.get(path)

    def directory(self, path: str) -> str | None:
        """The served folder ``path`` names, spelled as stored, or None."""
        prefix = path.rstrip("/") + "/"
        with self._lock:
            for name in self._files:
                if name.startswith(prefix):
                    return name[: len(prefix)]
        return None


def page_key(path: str) -> str:
    """The stored file a URL path names: ``docs/`` and ``docs/index.html`` are one page."""
    key = path.lstrip("/")
    return key + "index.html" if key == "" or key.endswith("/") else key


@dataclass
class Fault:
    """How one path misbehaves.

    ``delay`` holds the answer back first; then ``status`` replaces the page
    with an error, ``truncate`` promises the whole page in Content-Length but
    drops the connection halfway, and ``partial`` sends half the page as if it
    were all of it. ``times`` is how many requests it applies to (None: until
    removed), and ``served`` counts the requests it has hit so far.
    """

    status: int | None = None
    retry_after: int | None = None
    delay: float = 0.0
    truncate: bool = False
    partial: bool = False
    times: int | None = None
    served: int = 0

    @classmethod
    def from_json(cls, raw: object) -> "Fault":
        if not isinstance(raw, dict):
            raise ValueError("a fault is a JSON object")
        unknown = set(raw) - {"status", "retry_after", "delay", "truncate", "partial", "times"}
        if unknown:
            raise ValueError(f"unknown fault fields: {sorted(unknown)}")
        fault = cls(**raw)
        if fault.delay < 0:
            raise ValueError("delay cannot be negative")
        if fault.status is not None and not 400 <= fault.status <= 599:
            raise ValueError("status must be an HTTP error, 400-599")
        if sum([fault.status is not None, fault.truncate, fault.partial]) > 1:
            raise ValueError("status, truncate and partial are separate faults; set one")
        if not (fault.status or fault.truncate or fault.partial or fault.delay > 0):
            raise ValueError("a fault needs status, truncate, partial or a delay")
        if fault.times is not None and fault.times < 1:
            raise ValueError("times must be at least 1, or null for every request")
        return fault


class Faults:
    """Faults by page key. A spent fault stays listed so its ``served`` count can be read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faults: dict[str, Fault] = {}

    def clear(self) -> None:
        with self._lock:
            self._faults = {}

    def put(self, key: str, fault: Fault) -> None:
        with self._lock:
            self._faults[key] = fault

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._faults.pop(key, None) is not None

    def get(self, key: str) -> Fault | None:
        with self._lock:
            fault = self._faults.get(key)
            return Fault(**asdict(fault)) if fault else None

    def take(self, key: str) -> Fault | None:
        """The fault for this request, if one still applies, counting the hit."""
        with self._lock:
            fault = self._faults.get(key)
            if fault is None or (fault.times is not None and fault.served >= fault.times):
                return None
            fault.served += 1
            return Fault(**asdict(fault))


def make_handler(site: Site, base_url: str) -> type[BaseHTTPRequestHandler]:
    faults = Faults()

    class Handler(BaseHTTPRequestHandler):
        server_version = "pipeshub-web-fixtures"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _path(self) -> str:
            return self.path.split("?", 1)[0].split("#", 1)[0]

        def do_GET(self) -> None:  # noqa: N802
            path = self._path()
            if path == f"{CONTROL}/health":
                self._send(HTTPStatus.OK, b"ok")
                return
            fault_key = self._control_fault_key(path)
            if fault_key is not None:
                found_fault = faults.get(fault_key)
                if found_fault is None:
                    self._send(HTTPStatus.NOT_FOUND, b"no fault")
                else:
                    self._send(HTTPStatus.OK, json.dumps(asdict(found_fault)).encode(), "application/json")
                return
            key = page_key(path)
            # A HEAD is a size check before the real download; letting it use up
            # a fault would leave the download itself untouched.
            fault = faults.take(key) if self.command == "GET" else None
            if fault is not None:
                if fault.delay:
                    time.sleep(fault.delay)
                if fault.status is not None:
                    self._send_error_fault(fault)
                    return
            found = site.get(key)
            if found is None:
                directory = site.directory(key)
                if directory is not None:
                    self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                    # Built from the stored name and quoted, never from the raw request path.
                    self.send_header("Location", "/" + quote(directory, safe="/"))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send(HTTPStatus.NOT_FOUND, b"not found")
                return
            body, content_type = found
            if content_type.startswith(("text/", "application/rss", "application/atom")):
                body = body.replace(BASE_URL_TOKEN, base_url.encode())
            if fault is not None and fault.truncate:
                self._send_truncated(body, content_type)
                return
            if fault is not None and fault.partial:
                body = body[: len(body) // 2]
            self._send(HTTPStatus.OK, body, content_type)

        do_HEAD = do_GET  # noqa: N815

        def _send_error_fault(self, fault: Fault) -> None:
            body = f"injected fault: HTTP {fault.status}".encode()
            self.send_response(fault.status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            if fault.retry_after is not None:
                self.send_header("Retry-After", str(fault.retry_after))
            self.end_headers()
            self.wfile.write(body)

        def _send_truncated(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[: len(body) // 2])
            self.close_connection = True

        def _control_fault_key(self, path: str) -> str | None:
            prefix = f"{CONTROL}/faults/"
            return page_key(path[len(prefix):]) if path.startswith(prefix) else None

        def handle_one_request(self) -> None:
            # A delayed or truncated answer can outlast the client's patience;
            # the client hanging up is the point of the fault, not an error.
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def do_PUT(self) -> None:  # noqa: N802
            path = self._path()
            fault_key = self._control_fault_key(path)
            if fault_key is not None:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    fault = Fault.from_json(json.loads(self.rfile.read(length) or b"null"))
                except (TypeError, ValueError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, str(exc).encode())
                    return
                faults.put(fault_key, fault)
                self._send(HTTPStatus.NO_CONTENT)
                return
            if not path.startswith(f"{CONTROL}/files/"):
                self._send(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or 0)
            site.put(path[len(f"{CONTROL}/files/"):], self.rfile.read(length), self.headers.get("Content-Type"))
            self._send(HTTPStatus.NO_CONTENT)

        def do_DELETE(self) -> None:  # noqa: N802
            path = self._path()
            fault_key = self._control_fault_key(path)
            if fault_key is not None:
                self._send(HTTPStatus.NO_CONTENT if faults.delete(fault_key) else HTTPStatus.NOT_FOUND)
                return
            if not path.startswith(f"{CONTROL}/files/"):
                self._send(HTTPStatus.NOT_FOUND)
                return
            removed = site.delete(path[len(f"{CONTROL}/files/"):])
            self._send(HTTPStatus.NO_CONTENT if removed else HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            path = self._path()
            if path == f"{CONTROL}/openai/v1/chat/completions":
                self._chat_completion()
                return
            if path != f"{CONTROL}/reset":
                self._send(HTTPStatus.NOT_FOUND)
                return
            site.reset()
            faults.clear()
            self._send(HTTPStatus.NO_CONTENT)

        def _chat_completion(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, b"invalid JSON")
                return
            model = str(request.get("model") or "stand-in")
            base = {"id": "chatcmpl-fixture", "created": 0, "model": model}
            if not request.get("stream"):
                body = {
                    **base,
                    "object": "chat.completion",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": STAND_IN_REPLY},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                self._send(HTTPStatus.OK, json.dumps(body).encode(), "application/json")
                return
            chunks = [
                {"role": "assistant", "content": STAND_IN_REPLY},
                {},
            ]
            events = []
            for i, delta in enumerate(chunks):
                finish = "stop" if i == len(chunks) - 1 else None
                chunk = {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                events.append(f"data: {json.dumps(chunk)}\n\n")
            events.append("data: [DONE]\n\n")
            self._send(HTTPStatus.OK, "".join(events).encode(), "text/event-stream")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=Path, default=Path(__file__).parent / "seed")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--base-url", default="http://web-fixtures:8080")
    args = parser.parse_args()
    handler = make_handler(Site(args.seed), args.base_url.rstrip("/"))
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)  # noqa: S104
    server.serve_forever()


if __name__ == "__main__":
    main()
