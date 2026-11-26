"""JSON-RPC 2.0 with an ACP agent over the sandbox process's stdin and stdout.

Unlike Codex's app-server, ACP agents send the ``jsonrpc: "2.0"`` member, and anything without it is not the
protocol. Every message the agent sends passes one ordered pump: notifications and agent requests go to the
session's handler, responses resolve the request that waits for them, in the order they arrived. So a
``session/prompt`` response is seen only after every ``session/update`` the agent sent before it, which is what
ACP promises and what lets a turn end after its last event. The handler must never wait on the agent (it would
wait on the pump that serves it); slow work (a workspace read) runs in a task of its own.

Hostile input ends the session rather than the worker: a line past ``MAX_LINE_BYTES`` stops the reader
(``OversizedLine``), and past ``MAX_INVALID_MESSAGES`` malformed messages the stream is closed as a crash.
An agent request that is malformed but carries an id is answered ``Invalid request``; the agent's error messages
never reach a log.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineCrashed
from harness_core.engines.jsonlines import JsonLinesChannel, StreamEnd
from harness_core.engines.jsonview import as_object, as_str
from harness_engines.acp.protocol import (
    INVALID_REQUEST,
    JSONRPC,
    MAX_INVALID_MESSAGES,
    MAX_LINE_BYTES,
)

if TYPE_CHECKING:

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ProcessHandle

REQUEST_TIMEOUT_S = 60.0


class AcpRpcError(Exception):
    """An error response; ``detail`` is the agent's message and stays out of logs."""

    def __init__(self, method: str, code: int, message: str, data: JsonValue = None) -> None:
        super().__init__(f"{method} failed with JSON-RPC error {code}")
        self.method = method
        self.code = code
        self.detail = message
        self.data = data


class AcpStreamEnded(EngineCrashed):
    """The agent's stdout ended, broke, or stopped speaking the protocol."""


@dataclass(frozen=True, slots=True)
class Incoming:
    """A notification (``id`` is ``None``) or an agent request, as the handler sees it."""

    method: str
    params: Mapping[str, JsonValue]
    id: str | int | None = None

    @property
    def is_request(self) -> bool:
        return self.id is not None


Handler = Callable[[Incoming], Awaitable[None]]


def _valid_id(value: JsonValue) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


class AcpRpc:
    def __init__(self, process: ProcessHandle, *, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._channel = JsonLinesChannel(process, self._arrived, self._ended, max_line_bytes=max_line_bytes)
        self._queue: asyncio.Queue[Mapping[str, JsonValue] | StreamEnd] = asyncio.Queue()
        self._pending: dict[int, tuple[str, asyncio.Future[JsonValue]]] = {}
        self._next_id = 0
        self._handler: Handler | None = None
        self._on_end: Callable[[BaseException | None], None] | None = None
        self._pump: asyncio.Task[None] | None = None
        self._ended_with: BaseException | None = None
        self.ended = False
        self.invalid_messages = 0
        self.unmatched_responses = 0

    @property
    def unparsed_lines(self) -> int:
        return self._channel.unparsed_lines

    @property
    def stderr_tail(self) -> bytes:
        """The last bytes the agent wrote to stderr, for a caller diagnosing a failed start; never logged."""
        return self._channel.stderr_tail

    def start(self, handler: Handler, on_end: Callable[[BaseException | None], None] | None = None) -> None:
        """``on_end`` is called once, when the agent's stream has ended for any reason (never for ``close``)."""
        self._handler = handler
        self._on_end = on_end
        self._channel.start()
        self._pump = asyncio.get_running_loop().create_task(self._run())

    async def _arrived(self, message: Mapping[str, JsonValue]) -> None:
        await self._queue.put(message)

    async def _ended(self, error: BaseException | None) -> None:
        await self._queue.put(StreamEnd(error))

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if isinstance(item, StreamEnd):
                self._finish(item.error)
                return
            try:
                await self._route(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a handler's failure must not stop the pump that answers requests
                self._finish(exc)
                await self._channel.close()
                return
            if self.invalid_messages + self._channel.unparsed_lines > MAX_INVALID_MESSAGES:
                self._finish(AcpStreamEnded("the agent is not speaking JSON-RPC 2.0"))
                await self._channel.close()
                return

    def _finish(self, error: BaseException | None) -> None:
        if not self.ended and self._on_end is not None:
            self._on_end(error)
        self.ended = True
        self._ended_with = error
        for method, future in self._pending.values():
            if not future.done():
                future.set_exception(AcpStreamEnded(f"the agent stopped before answering {method}"))
        self._pending.clear()

    async def _route(self, message: Mapping[str, JsonValue]) -> None:
        if message.get("jsonrpc") != JSONRPC:
            self.invalid_messages += 1
            await self._refuse(message)
            return
        method = message.get("method")
        raw_id = message.get("id")
        if method is not None:
            params = message.get("params", {})
            if not isinstance(method, str) or not method or ("id" in message and not _valid_id(raw_id)):
                self.invalid_messages += 1
                await self._refuse(message)
                return
            if params is not None and not isinstance(params, dict):
                self.invalid_messages += 1
                await self._refuse(message)
                return
            request_id = raw_id if "id" in message and _valid_id(raw_id) else None
            if self._handler is not None:
                await self._handler(Incoming(method, as_object(params), request_id))  # pyright: ignore[reportArgumentType]
            return
        if ("result" in message) == ("error" in message) or not _valid_id(raw_id):
            self.invalid_messages += 1
            return
        entry = self._pending.pop(raw_id, None) if isinstance(raw_id, int) else None
        if entry is None:
            self.unmatched_responses += 1
            return
        name, future = entry
        if future.done():
            return
        if "error" in message:
            error = as_object(message.get("error"))
            code = error.get("code")
            future.set_exception(
                AcpRpcError(
                    name,
                    code if isinstance(code, int) and not isinstance(code, bool) else 0,
                    as_str(error.get("message")),
                    error.get("data"),
                )
            )
        else:
            future.set_result(message.get("result"))

    async def _refuse(self, message: Mapping[str, JsonValue]) -> None:
        """A malformed request that carries an id is answered, so the agent never waits on it."""
        raw_id = message.get("id")
        if "method" in message and _valid_id(raw_id):
            await self.reply_error(raw_id, INVALID_REQUEST, "invalid request")

    async def request(
        self, method: str, params: Mapping[str, JsonValue] | None = None, *, timeout_s: float | None = REQUEST_TIMEOUT_S
    ) -> JsonValue:
        if self.ended:
            raise AcpStreamEnded(f"the agent has stopped; {method} not sent")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        message: dict[str, JsonValue] = {"jsonrpc": JSONRPC, "id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        try:
            await self._channel.write(message)
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, JsonValue] | None = None) -> None:
        message: dict[str, JsonValue] = {"jsonrpc": JSONRPC, "method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._channel.write(message)

    async def reply(self, request_id: JsonValue, result: JsonValue) -> None:
        with contextlib.suppress(Exception):
            await self._channel.write({"jsonrpc": JSONRPC, "id": request_id, "result": result})

    async def reply_error(self, request_id: JsonValue, code: int, message: str) -> None:
        with contextlib.suppress(Exception):
            await self._channel.write(
                {"jsonrpc": JSONRPC, "id": request_id, "error": {"code": code, "message": message}}
            )

    async def close(self) -> None:
        """Close stdin (an agent exits on EOF) and stop reading."""
        self._on_end = None
        await self._channel.close()
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
        self._finish(self._ended_with)


__all__ = ["REQUEST_TIMEOUT_S", "AcpRpc", "AcpRpcError", "AcpStreamEnded", "Handler", "Incoming"]
