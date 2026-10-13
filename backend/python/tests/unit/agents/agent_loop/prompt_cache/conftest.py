"""A local HTTP endpoint that records request bodies and answers as Anthropic,
OpenAI (Chat Completions and Responses) or Azure OpenAI would, so the tests
drive the real LangChain clients and SDKs: an argument an SDK refuses fails
here, as it would in production."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

_ANTHROPIC = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
}
_CHAT = {
    "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-test",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}
_RESPONSES = {
    "id": "r1", "object": "response", "created_at": 0, "status": "completed", "model": "gpt-test",
    "output": [{
        "type": "message", "id": "m1", "status": "completed", "role": "assistant",
        "content": [{"type": "output_text", "text": "ok", "annotations": []}],
    }],
    "usage": {
        "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
        "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0},
    },
    "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
}


def anthropic_reply(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {**_ANTHROPIC, "content": content, "stop_reason": stop_reason}


class RecordingEndpoint:
    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.paths: list[str] = []
        # Replies served in order before falling back to the canned ones.
        self.replies: list[dict] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                endpoint.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
                endpoint.paths.append(self.path)
                if endpoint.replies:
                    reply = endpoint.replies.pop(0)
                elif "/messages" in self.path:
                    reply = _ANTHROPIC
                elif "/responses" in self.path:
                    reply = _RESPONSES
                else:
                    reply = _CHAT
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> RecordingEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def last(self) -> dict:
        return self.bodies[-1]


@pytest.fixture
def endpoint() -> Iterator[RecordingEndpoint]:
    with RecordingEndpoint() as recording:
        yield recording
