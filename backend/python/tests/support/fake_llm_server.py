"""A fake model provider on a local socket (no external network), answering OpenAI-style and Anthropic-style requests.

``behavior(request_body) -> Reply`` decides each response. ``Reply.status`` 200 answers a completion; another status answers
that error; ``hang`` never answers (the client's own ceiling ends it); ``reset`` closes the connection after reading the request.
Every request is counted by the document named in its prompt (``DOC:<name>``).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Reply:
    status: int = 200
    hang: bool = False
    reset: bool = False


OK = Reply()
HANG = Reply(hang=True)


def completion(model: str = "m", text: str = "ok") -> bytes:
    return json.dumps({
        "id": "c1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }).encode()


class FakeLLMServer:
    def __init__(self, behavior: Callable[[str], Reply] = lambda body: OK) -> None:
        self.behavior = behavior
        self.requests: Counter[str] = Counter()
        self.total = 0
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self._hung: list[asyncio.Event] = []

    async def start(self) -> FakeLLMServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def stop(self) -> None:
        for event in self._hung:
            event.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = int(re.search(rb"(?i)content-length:\s*(\d+)", head).group(1)) if re.search(rb"(?i)content-length:", head) else 0
            body = (await reader.readexactly(length)).decode("utf-8", "replace") if length else ""
            match = re.search(r"DOC:([\w.\-]+)", body)
            doc = match.group(1) if match else "?"
            self.requests[doc] += 1
            self.total += 1
            reply = self.behavior(body)
            if reply.reset:
                writer.transport.abort()
                return
            if reply.hang:
                event = asyncio.Event()
                self._hung.append(event)
                await event.wait()
                return
            payload = completion() if reply.status == 200 else json.dumps({"error": {"message": "x", "type": "server_error"}}).encode()
            writer.write(
                f"HTTP/1.1 {reply.status} X\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n".encode() + payload
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
