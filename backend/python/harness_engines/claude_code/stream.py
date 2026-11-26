"""Claude Code's stream-json protocol over the sandbox process's stdin and stdout.

``claude -p --input-format stream-json --output-format stream-json`` reads one
JSON object per line (user messages and ``control_request``s) and writes one
per line: ``system``, ``assistant``, ``user``, ``result`` messages, the
``control_response`` to each of our control requests (by ``request_id``), and
control requests of its own. Responses resolve their request; everything else
is queued in arrival order for the running turn.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineCrashed
from harness_core.engines.jsonlines import JsonLinesChannel, StreamEnd
from harness_core.engines.jsonview import as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ProcessHandle

CONTROL_TIMEOUT_S = 60.0


class ClaudeControlError(Exception):
    """An error ``control_response``; its message is Claude Code's and stays out of logs."""

    def __init__(self, subtype: str, message: str) -> None:
        super().__init__(f"control request {subtype} failed")
        self.subtype = subtype
        self.detail = message


class ClaudeStreamEnded(EngineCrashed):
    """Claude Code's stdout ended: the process exited or its stream broke."""


def user_message(text: str) -> dict[str, JsonValue]:
    """With a ``uuid``, Claude Code reports the message's fate in ``command_lifecycle`` frames (2.1.294)."""
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "uuid": str(uuid.uuid4()),
    }


class ClaudeStream:
    def __init__(self, process: ProcessHandle) -> None:
        self._channel = JsonLinesChannel(process, self._dispatch, self._ended)
        self._pending: dict[str, tuple[str, asyncio.Future[Mapping[str, JsonValue]]]] = {}
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
        """The last bytes Claude Code wrote to stderr, for a caller diagnosing a failed start; never logged."""
        return self._channel.stderr_tail

    def start(self) -> None:
        self._channel.start()

    async def _ended(self, error: BaseException | None) -> None:
        for subtype, future in self._pending.values():
            if not future.done():
                future.set_exception(ClaudeStreamEnded(f"Claude Code stopped before answering {subtype}"))
        self._pending.clear()
        await self.incoming.put(StreamEnd(error))

    async def _dispatch(self, message: Mapping[str, JsonValue]) -> None:
        if as_str(message.get("type")) != "control_response":
            await self.incoming.put(message)
            return
        response = as_object(message.get("response"))
        entry = self._pending.pop(as_str(response.get("request_id")), None)
        if entry is None or entry[1].done():
            return
        subtype, future = entry
        if as_str(response.get("subtype")) == "success":
            future.set_result(as_object(response.get("response")))
        else:
            future.set_exception(ClaudeControlError(subtype, as_str(response.get("error"))))

    async def control(
        self, subtype: str, fields: Mapping[str, JsonValue] | None = None, *, timeout_s: float = CONTROL_TIMEOUT_S
    ) -> Mapping[str, JsonValue]:
        if self.ended:
            raise ClaudeStreamEnded(f"Claude Code has stopped; {subtype} not sent")
        self._next_id += 1
        request_id = f"harness-{self._next_id}"
        future: asyncio.Future[Mapping[str, JsonValue]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (subtype, future)
        request: dict[str, JsonValue] = {"subtype": subtype, **dict(fields or {})}
        try:
            await self._channel.write({"type": "control_request", "request_id": request_id, "request": request})
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self._pending.pop(request_id, None)

    async def reply(self, request_id: str, response: Mapping[str, JsonValue]) -> None:
        """Answer a control request Claude Code sent."""
        await self._channel.write(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": dict(response)},
            }
        )

    async def send(self, message: Mapping[str, JsonValue]) -> None:
        await self._channel.write(message)

    async def close(self) -> None:
        """Close stdin (Claude Code exits on EOF) and stop reading."""
        await self._channel.close()
        for subtype, future in self._pending.values():
            if not future.done():
                future.set_exception(ClaudeStreamEnded(f"closed before {subtype} was answered"))
        self._pending.clear()


__all__ = [
    "CONTROL_TIMEOUT_S",
    "ClaudeControlError",
    "ClaudeStream",
    "ClaudeStreamEnded",
    "StreamEnd",
    "user_message",
]
