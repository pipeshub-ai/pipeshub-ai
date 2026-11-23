"""A fake OpenAI-compatible provider for the indexing benchmarks. Standard library only.

* ``POST /v1/chat/completions``: deterministic, schema-valid answers for text and vision requests
  (structured output by ``response_format`` or by a forced tool call), after a latency model of
  ``--base-ms`` plus ``--per-kb-ms`` per KB of prompt.
* ``GET /v1/models``.
* ``POST /v1/embeddings`` only with ``--fake-embeddings`` (the sensitivity variant). Without it the
  route answers 404, so a stack that is meant to embed locally cannot use this server by mistake.
* ``--quota-concurrency N`` and ``--quota-rpm R``: above the quota the server answers 429 with
  ``Retry-After`` instead of serving, as a provider with a rate limit does.
* ``GET /stats``: request, throttled and in-flight counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class QuotaGate:
    """Admits a request, or says how many seconds to wait. The clock is injectable for tests."""

    def __init__(self, concurrency: int = 0, rpm: int = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self.concurrency = concurrency
        self.rpm = rpm
        self.clock = clock
        self.in_flight = 0
        self.starts: deque[float] = deque()
        self.lock = threading.Lock()
        self.throttled = 0

    def try_enter(self) -> float | None:
        """None when admitted (call ``leave`` after), else the Retry-After seconds."""
        with self.lock:
            now = self.clock()
            while self.starts and now - self.starts[0] >= 60.0:
                self.starts.popleft()
            if self.concurrency and self.in_flight >= self.concurrency:
                self.throttled += 1
                return 1.0
            if self.rpm and len(self.starts) >= self.rpm:
                self.throttled += 1
                return max(1.0, 60.0 - (now - self.starts[0]))
            self.in_flight += 1
            self.starts.append(now)
            return None

    def leave(self) -> None:
        with self.lock:
            self.in_flight = max(0, self.in_flight - 1)


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8", "replace")).digest()[:8], "big")


_COUNT_PATTERNS = (
    re.compile(r"(?i)\bexactly\s+(\d+)\b"),  # "exactly 6 descriptions are required", "EXACTLY 6 description"
    re.compile(r"(?i)\btotal:\s*(\d+)\s+rows?\b"),  # "Rows Data (Total: 6 rows)"
)
# The list fields whose length a prompt can constrain by naming a count.
_COUNTED_FIELDS = frozenset({"descriptions"})
# Lists of names the product looks up in the organization's own data: a made-up name sends it down a "not
# found" path and a warning per record, so the fake names none (an empty list is valid for these fields).
_EMPTY_LIST_FIELDS = frozenset({"departments"})


def requested_count(prompt: str) -> int | None:
    """The item count a prompt asks for, or None. The last message states the final request (a
    reflection round repeats the count), so the last match of the first pattern that matches wins; the
    highest ``Row N:`` index is the fallback."""
    for pattern in _COUNT_PATTERNS:
        found = pattern.findall(prompt)
        if found:
            return int(found[-1])
    rows = [int(n) for n in re.findall(r"(?m)^Row (\d+):", prompt)]
    return max(rows) if rows else None


def example_from_schema(
    schema: dict[str, Any], key: str = "value", defs: dict[str, Any] | None = None, count: int | None = None
) -> Any:
    """A value that satisfies *schema*: required and optional properties are all filled, enums take the first member.

    A list field the prompt constrains (``_COUNTED_FIELDS``) gets *count* items; any list honours its own
    ``minItems`` and ``maxItems``.
    """
    defs = defs if defs is not None else {**schema.get("$defs", {}), **schema.get("definitions", {})}
    if "$ref" in schema:
        return example_from_schema(defs.get(schema["$ref"].split("/")[-1], {}), key, defs, count)
    for combo in ("anyOf", "oneOf"):
        if schema.get(combo):
            options = [o for o in schema[combo] if o.get("type") != "null"] or schema[combo]
            return example_from_schema(options[0], key, defs, count)
    if schema.get("allOf"):
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            merged.update(example_from_schema(part, key, defs, count) if part.get("type") == "object" else {})
        return merged
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        return {k: example_from_schema(v, k, defs, count) for k, v in (schema.get("properties") or {}).items()}
    if kind == "array":
        n = int(schema.get("minItems") or 1)
        if count and key in _COUNTED_FIELDS:
            n = count
        if key in _EMPTY_LIST_FIELDS and not schema.get("minItems"):
            return []
        if schema.get("maxItems") is not None:
            n = min(n, int(schema["maxItems"]))
        item = example_from_schema(schema.get("items") or {}, key, defs, count)
        return [f"{item} {i + 1}" if isinstance(item, str) else item for i in range(n)]
    if kind == "integer":
        return max(int(schema.get("minimum", 1)), 1)
    if kind == "number":
        return float(max(schema.get("minimum", 1), 1))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return f"fake {key}"


def _content_text(messages: list[dict[str, Any]]) -> tuple[str, int]:
    """The prompt's text, and the number of images in it."""
    parts: list[str] = []
    images = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") in ("image_url", "image"):
                    images += 1
                    parts.append(json.dumps(block.get("image_url") or block.get("source") or "")[:4096])
    return "\n".join(parts), images


def chat_answer(body: dict[str, Any]) -> dict[str, Any]:
    """The response body for one chat request. Deterministic in the request."""
    text, images = _content_text(body.get("messages") or [])
    digest = _seed(text)
    count = requested_count(text)
    message: dict[str, Any] = {"role": "assistant", "content": None}
    finish = "stop"
    fmt = body.get("response_format") or {}
    tools = body.get("tools") or []
    forced = body.get("tool_choice")
    if tools and (forced not in (None, "none", "auto") or not fmt):
        wanted = forced.get("function", {}).get("name") if isinstance(forced, dict) else None
        tool = next((t for t in tools if t.get("function", {}).get("name") == wanted), tools[0])["function"]
        args = example_from_schema(tool.get("parameters") or {"type": "object"}, count=count)
        message["tool_calls"] = [{
            "id": f"call_{digest:x}"[:20], "type": "function",
            "function": {"name": tool["name"], "arguments": json.dumps(args)},
        }]
        finish = "tool_calls"
    elif fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema") or {"type": "object"}
        message["content"] = json.dumps(example_from_schema(schema, count=count))
    elif fmt.get("type") == "json_object":
        answer: dict[str, Any] = {"summary": f"Fake summary {digest % 10_000}", "title": "Fake title"}
        if count:
            answer["descriptions"] = [f"fake description {i + 1}" for i in range(count)]
        message["content"] = json.dumps(answer)
    elif images:
        message["content"] = f"Page text recognised by the fake vision model {digest:x}. " * 12
    else:
        message["content"] = f"Fake answer {digest % 100_000}. This is deterministic text from the benchmark's fake provider."
    return {
        "id": f"chatcmpl-{digest:x}"[:24], "object": "chat.completion", "created": 0,
        "model": body.get("model", "fake-chat"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": max(1, len(text) // 4), "completion_tokens": 16, "total_tokens": len(text) // 4 + 16},
    }


def embedding_vector(text: str, dim: int) -> list[float]:
    seed = hashlib.sha256(text.encode("utf-8", "replace")).digest()
    raw = [(seed[i % len(seed)] / 255.0) - 0.5 for i in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


def latency_seconds(prompt_chars: int, base_ms: float, per_kb_ms: float) -> float:
    return (base_ms + per_kb_ms * prompt_chars / 1024) / 1000.0


class Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.base_ms = args.base_ms
        self.per_kb_ms = args.per_kb_ms
        self.fake_embeddings = args.fake_embeddings
        self.dim = args.embedding_dim
        self.gate = QuotaGate(args.quota_concurrency, args.quota_rpm)
        self.counts = {"chat": 0, "embeddings": 0, "throttled": 0}


def make_handler(cfg: Config) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a: Any) -> None:  # quiet
            return

        def _send(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models"):
                self._send(200, {"object": "list", "data": [{"id": "fake-chat", "object": "model"},
                                                             {"id": "fake-embedding", "object": "model"}]})
            elif self.path.startswith("/stats"):
                self._send(200, {**cfg.counts, "throttled": cfg.gate.throttled, "in_flight": cfg.gate.in_flight,
                                 "config": {"base_ms": cfg.base_ms, "per_kb_ms": cfg.per_kb_ms,
                                            "fake_embeddings": cfg.fake_embeddings}})
            elif self.path.startswith("/health"):
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                self._send(400, {"error": {"message": "invalid JSON"}})
                return
            path = self.path.rstrip("/")
            if path.endswith("/embeddings") and not cfg.fake_embeddings:
                self._send(404, {"error": {"message": "embeddings are not served (start with --fake-embeddings)"}})
                return
            if not path.endswith(("/chat/completions", "/embeddings")):
                self._send(404, {"error": {"message": "not found"}})
                return
            retry = cfg.gate.try_enter()
            if retry is not None:
                self._send(429, {"error": {"message": "rate limit", "type": "rate_limit_exceeded"}},
                           {"Retry-After": str(int(retry + 0.999))})
                return
            try:
                if path.endswith("/embeddings"):
                    inputs = body.get("input")
                    inputs = [inputs] if isinstance(inputs, str) else list(inputs or [])
                    time.sleep(latency_seconds(sum(len(str(i)) for i in inputs), 0, cfg.per_kb_ms))
                    cfg.counts["embeddings"] += 1
                    self._send(200, {
                        "object": "list", "model": body.get("model", "fake-embedding"),
                        "data": [{"object": "embedding", "index": n, "embedding": embedding_vector(str(t), cfg.dim)}
                                 for n, t in enumerate(inputs)],
                        "usage": {"prompt_tokens": 1, "total_tokens": 1},
                    })
                else:
                    text, _ = _content_text(body.get("messages") or [])
                    time.sleep(latency_seconds(len(text), cfg.base_ms, cfg.per_kb_ms))
                    cfg.counts["chat"] += 1
                    self._send(200, chat_answer(body))
            finally:
                cfg.gate.leave()

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--base-ms", type=float, default=300.0, help="latency of every chat call")
    ap.add_argument("--per-kb-ms", type=float, default=2.0, help="extra latency per KB of prompt")
    ap.add_argument("--fake-embeddings", action="store_true")
    ap.add_argument("--embedding-dim", type=int, default=384)
    ap.add_argument("--quota-concurrency", type=int, default=0, help="429 above this many calls in flight (0 = off)")
    ap.add_argument("--quota-rpm", type=int, default=0, help="429 above this many calls per minute (0 = off)")
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(Config(args)))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
