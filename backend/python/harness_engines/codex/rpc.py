"""JSON-RPC 2.0 with ``codex-app-server`` over the sandbox process's stdin and stdout.

The app-server writes one JSON object per line and omits the ``jsonrpc``
field (F2 §0). A message with ``method`` and ``id`` is a server request that
needs a reply, ``method`` alone a notification, ``id`` alone a response. The
reader keeps notifications and server requests in arrival order in a queue
that never blocks it: a response must never wait behind a notification the
session has not consumed yet (the session reads the queue only during a turn).
Line framing and stderr handling are
``harness_core.engines.jsonlines``'s.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineCrashed
from harness_core.engines.jsonlines import JsonLinesChannel, StreamEnd, split_lines
from harness_core.engines.jsonview import as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ProcessHandle

REQUEST_TIMEOUT_S = 60.0
# JSON-RPC's "invalid request": Codex's answer to a turn/interrupt for a turn that has already ended.
INVALID_REQUEST = -32600
# JSON-RPC's "method not found": the reply to server requests the harness does not serve.
METHOD_NOT_FOUND = -32601


class CodexRpcError(Exception):
    """An error response; the message is Codex's and stays out of logs."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"{method} failed with JSON-RPC error {code}")
        self.method = method
        self.code = code
        self.detail = message


class CodexStreamEnded(EngineCrashed):
    """The app-server's stdout ended: the process exited or its stream broke."""


class CodexRpc:
    def __init__(self, process: ProcessHandle) -> None:
        self._channel = JsonLinesChannel(process, self._dispatch, self._ended)
        self._pending: dict[int, tuple[str, asyncio.Future[JsonValue]]] = {}
        self._next_id = 0
        self.incoming: asyncio.Queue[Mapping[str, JsonValue] | StreamEnd] = asyncio.Queue()

    @property
    def ended(self) -> bool:
        return self._channel.ended

    @property
    def unparsed_lines(self) -> int:
        return self._channel.unparsed_lines

    @property
    def stderr_tail(self) -> bytes:
        """The last bytes Codex wrote to stderr, for a caller diagnosing a failed start; never logged."""
        return self._channel.stderr_tail

    def start(self) -> None:
        self._channel.start()

    async def _ended(self, error: BaseException | None) -> None:
        for method, future in self._pending.values():
            if not future.done():
                future.set_exception(CodexStreamEnded(f"the app-server stopped before answering {method}"))
        self._pending.clear()
        await self.incoming.put(StreamEnd(error))

    async def _dispatch(self, message: Mapping[str, JsonValue]) -> None:
        if "method" in message:
            await self.incoming.put(message)
            return
        raw_id = message.get("id")
        entry = self._pending.pop(raw_id, None) if isinstance(raw_id, int) else None
        if entry is None:
            return
        method, future = entry
        if future.done():
            return
        if "error" in message:
            error = as_object(message.get("error"))
            code = error.get("code")
            future.set_exception(
                CodexRpcError(method, code if isinstance(code, int) else 0, as_str(error.get("message")))
            )
        else:
            future.set_result(message.get("result"))

    async def _write(self, message: Mapping[str, JsonValue]) -> None:
        await self._channel.write(message)

    async def request(
        self, method: str, params: Mapping[str, JsonValue] | None = None, *, timeout_s: float = REQUEST_TIMEOUT_S
    ) -> JsonValue:
        if self.ended:
            raise CodexStreamEnded(f"the app-server has stopped; {method} not sent")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        message: dict[str, JsonValue] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        try:
            await self._write(message)
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, JsonValue] | None = None) -> None:
        message: dict[str, JsonValue] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._write(message)

    async def reply(self, request_id: JsonValue, result: JsonValue) -> None:
        await self._write({"id": request_id, "result": result})

    async def reply_error(self, request_id: JsonValue, code: int, message: str) -> None:
        await self._write({"id": request_id, "error": {"code": code, "message": message}})

    async def close(self) -> None:
        """Close stdin (the app-server exits on EOF) and stop reading."""
        await self._channel.close()
        for method, future in self._pending.values():
            if not future.done():
                future.set_exception(CodexStreamEnded(f"closed before {method} was answered"))
        self._pending.clear()


__all__ = [
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "CodexRpc",
    "CodexRpcError",
    "CodexStreamEnded",
    "StreamEnd",
    "split_lines",
]
