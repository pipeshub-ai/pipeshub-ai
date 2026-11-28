"""An in-process OpenAI-compatible ``/v1/embeddings`` server on loopback, for the embedding decode tests."""

from __future__ import annotations

import base64
import json
import threading
import time
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import numpy as np


def make_vectors(count: int, dims: int, seed: int = 7) -> list[list[float]]:
    """Reproducible float32-exact vectors, so a base64 reply and a float-list reply carry the same numbers."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((count, dims)).astype(np.float32).tolist()


def content_vector(text: str, dims: int) -> list[float]:
    """The vector the server returns for ``text`` in ``by_content`` mode: a function of the text alone."""
    return make_vectors(1, dims, seed=zlib.crc32(text.encode()))[0]


class _Server(ThreadingHTTPServer):
    # The stdlib backlog is 5: 32 clients connecting at once are then refused, whatever client they use.
    request_queue_size = 128
    daemon_threads = True


@dataclass
class FakeEmbeddingServer:
    """``mode`` is ``float`` (lists whatever the request asks), ``base64``, or ``honour`` (follow ``encoding_format``).

    ``statuses`` is a queue of error statuses served before normal answers; ``delay`` stalls each answer.
    ``by_content`` makes each vector a function of its input text (to catch answers crossing between callers);
    ``short`` answers one item fewer than the inputs asked for (a gateway that drops an input).
    """

    mode: str = "float"
    dims: int = 8
    statuses: list[int] = field(default_factory=list)
    delay: float = 0.0
    by_content: bool = False
    short: bool = False
    custom: bytes | None = None  # answered as is, whatever the request
    requests: list[dict[str, Any]] = field(default_factory=list)
    _cache: dict[Any, bytes] = field(default_factory=dict)
    _httpd: _Server | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def vectors_for(self, count: int) -> list[list[float]]:
        return make_vectors(count, self.dims)

    def _vectors(self, inputs: object, count: int) -> list[list[float]]:
        if self.by_content and isinstance(inputs, list):
            vectors = [content_vector(str(text), self.dims) for text in inputs]
        else:
            vectors = self.vectors_for(count)
        return vectors[:-1] if self.short and len(vectors) > 1 else vectors

    def reply_bytes(self, body: dict[str, Any], as_base64: bool) -> bytes:
        """The serialised answer, cached by shape: serialising 50 x 3072 floats in this process would hold the GIL."""
        inputs = body.get("input")
        count = len(inputs) if isinstance(inputs, list) else 1
        if self.custom is not None:
            return self.custom
        key = (count, as_base64, body.get("model"), self.short, json.dumps(inputs) if self.by_content else None)
        if key not in self._cache:
            items = [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode() if as_base64 else v,
                }
                for i, v in enumerate(self._vectors(inputs, count))
            ]
            payload = {
                "object": "list",
                "data": items,
                "model": body.get("model"),
                "usage": {"prompt_tokens": 3 * count, "total_tokens": 3 * count},
            }
            self._cache[key] = json.dumps(payload).encode()
        return self._cache[key]

    def start(self) -> FakeEmbeddingServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                owner.requests.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
                if owner.delay:
                    time.sleep(owner.delay)
                if owner.statuses:
                    status = owner.statuses.pop(0)
                    self._send(status, json.dumps({"error": {"message": f"status {status}"}}).encode())
                    return
                if owner.mode == "garbage":
                    self._send(200, b'{"data": [{"embedding": [0.1, 0.2')
                    return
                as_base64 = owner.mode == "base64" or (
                    owner.mode == "honour" and body.get("encoding_format") == "base64"
                )
                self._send(200, owner.reply_bytes(body, as_base64))

            def _send(self, status: int, data: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
