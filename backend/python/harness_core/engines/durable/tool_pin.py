"""One gateway tool list per session for an engine (OWD-7, TG-14): what an engine is shown is fixed by its first run.

The loop keeps one list per chat session (``harness_core.kernel.session_tools``); an engine's list was fixed per run
only, so a tool an admin added, removed or changed between two turns of a conversation changed what the engine saw
mid-conversation (and its prompt cache). The first engine run of a session journals the list its gateway serves, in
the same slot the loop uses (``SESSION_TOOLS_SLOT``, one key per engine kind, since engines name tools differently);
every later run of the session serves that list byte for byte. A listed tool the run no longer has answers that it
was removed after the chat started; a tool granted since stays callable (the grant decides what may run) but is not
listed until a new chat.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from harness_core.contracts import canonical_digest
from harness_core.contracts.generated.common import ContentRef
from harness_core.gateway.types import McpTool
from harness_core.journal.types import NewEvent
from harness_core.kernel.session_tools import ENGINE_LIST_PREFIX, SESSION_TOOLS_SLOT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent
    from harness_core.logs import HarnessLog



@runtime_checkable
class PinsToolList(Protocol):
    """A ``ToolEndpoint`` whose ``tools/list`` can be held to a session's list."""

    def served_tools(self) -> tuple[McpTool, ...]:
        """What ``tools/list`` serves now, before any hold."""
        ...

    def hold_list(self, tools: tuple[McpTool, ...]) -> None:
        """Serve ``tools`` from now on; a call to one of them the run no longer has answers that it was removed."""
        ...


def _entries(tools: Sequence[McpTool]) -> list[dict[str, JsonValue]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": dict(t.input_schema),
            **({"auth": t.auth} if t.auth is not None else {}),
        }
        for t in tools
    ]


def encode_list(tools: Sequence[McpTool]) -> bytes:
    return json.dumps(_entries(tools), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_list(data: bytes) -> tuple[McpTool, ...]:
    raw = cast("list[dict[str, JsonValue]]", json.loads(data.decode("utf-8")))
    return tuple(
        McpTool(
            str(t["name"]),
            str(t["description"]),
            cast("dict[str, JsonValue]", t["inputSchema"]),
            auth="needs_authentication" if t.get("auth") == "needs_authentication" else None,
        )
        for t in raw
    )


def list_digest(tools: Sequence[McpTool]) -> str:
    return canonical_digest(_entries(tools))


class EngineToolPin:
    """The session's pinned list for one engine kind, read from the session stream (``apply``) and held on the
    attempt's endpoint (``pin``)."""

    def __init__(self, engine: str, log: HarnessLog) -> None:
        self.key = f"{ENGINE_LIST_PREFIX}{engine}"
        self._log = log
        self._entry: dict[str, JsonValue] | None = None

    def apply(self, events: Sequence[JournalEvent]) -> None:
        for event in events:
            if event.type != "loop.state_saved" or self._entry is not None:
                continue
            slots = event.data.get("slots")
            lists = slots.get(SESSION_TOOLS_SLOT) if isinstance(slots, dict) else None
            entry = lists.get(self.key) if isinstance(lists, dict) else None
            if isinstance(entry, dict):
                self._entry = cast("dict[str, JsonValue]", entry)

    async def pin(
        self,
        endpoint: PinsToolList,
        put: Callable[[bytes], Awaitable[ContentRef]],
        get: Callable[[ContentRef], Awaitable[bytes]],
    ) -> Sequence[NewEvent]:
        """Hold ``endpoint`` to the session's list, or journal what it serves as the session's list (the events to
        commit; empty when the session already has one). A list that cannot be read is not held: the run serves its
        own and logs it, rather than failing the conversation."""
        served = endpoint.served_tools()
        if self._entry is None:
            ref = await put(encode_list(served))
            digest = list_digest(served)
            self._entry = {"ref": ref.model_dump(mode="json"), "digest": digest, "count": len(served)}
            self._log.info("tools.session_pinned", key=self.key, tools=len(served), digest=digest)
            return (NewEvent("loop.state_saved", {"slots": {SESSION_TOOLS_SLOT: {self.key: self._entry}}}),)
        if self._entry.get("digest") == list_digest(served):
            return ()
        ref = self._entry.get("ref")
        try:
            pinned = decode_list(await get(ContentRef.model_validate(ref)))
        except Exception as exc:  # the session's list is unreadable: serve this run's own rather than fail the chat
            self._log.warning("tools.session_list_unreadable", key=self.key, errorType=type(exc).__name__)
            return ()
        names, shown = {t.name for t in served}, {t.name for t in pinned}
        self._log.info(
            "tools.session_list_held",
            key=self.key,
            notListed=len(names - shown),
            noLongerGranted=len(shown - names),
        )
        endpoint.hold_list(pinned)
        return ()


__all__ = ["ENGINE_LIST_PREFIX", "EngineToolPin", "PinsToolList", "decode_list", "encode_list", "list_digest"]
