"""A website and feeds for the Web and RSS connectors to sync from.

Runs as the ``web-fixtures`` service of the integration stack. It serves the
files under ``--seed`` from memory, and the tests change what it serves through
a control API on the same port. A changed or removed page then needs no
filesystem shared between the test runner and this container.

    PUT    /__fixtures__/files/<path>   serve the request body at /<path>
    DELETE /__fixtures__/files/<path>   serve 404 at /<path>
    POST   /__fixtures__/reset          serve the seed files again
    GET    /__fixtures__/health         200 once serving

``{{BASE_URL}}`` in a served text file becomes ``--base-url``, so feeds can
carry absolute links that work from wherever the connector runs.

Standard library only, so the service runs on a stock Python image.
"""

from __future__ import annotations

import argparse
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

CONTROL = "/__fixtures__"
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


def make_handler(site: Site, base_url: str) -> type[BaseHTTPRequestHandler]:
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
            key = path.lstrip("/")
            if key == "" or key.endswith("/"):
                key += "index.html"
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
            self._send(HTTPStatus.OK, body, content_type)

        do_HEAD = do_GET  # noqa: N815

        def do_PUT(self) -> None:  # noqa: N802
            path = self._path()
            if not path.startswith(f"{CONTROL}/files/"):
                self._send(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or 0)
            site.put(path[len(f"{CONTROL}/files/"):], self.rfile.read(length), self.headers.get("Content-Type"))
            self._send(HTTPStatus.NO_CONTENT)

        def do_DELETE(self) -> None:  # noqa: N802
            path = self._path()
            if not path.startswith(f"{CONTROL}/files/"):
                self._send(HTTPStatus.NOT_FOUND)
                return
            removed = site.delete(path[len(f"{CONTROL}/files/"):])
            self._send(HTTPStatus.NO_CONTENT if removed else HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if self._path() != f"{CONTROL}/reset":
                self._send(HTTPStatus.NOT_FOUND)
                return
            site.reset()
            self._send(HTTPStatus.NO_CONTENT)

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
