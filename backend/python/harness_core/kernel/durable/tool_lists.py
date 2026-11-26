"""The kernel's half of one tool list per session (R4, ``harness_core.kernel.session_tools``).

``apply`` gives a request the session's list under its key, journaling the list with the first request that has
none. ``refusal`` answers a call the list shows but the run can no longer run as shown. ``unwrap`` turns a
``call_tool`` into the call it makes, when the run offers ``call_tool``.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.contracts.generated.common import ContentRef
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable.ports import WithheldTools
from harness_core.kernel.session_tools import (
    ENGINE_LIST_PREFIX,
    SESSION_TOOLS_SLOT,
    changed_since_pinned,
    decode_tools,
    encode_tools,
    tools_digest,
)
from harness_core.tools.call_tool import CALL_TOOL, unwrapped

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import JsonValue

    from harness_core.contracts.generated.enums import ToolErrorCode
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable.ports import DurableToolPort, ModelCall
    from harness_core.kernel.fold import SessionState
    from harness_core.logs import HarnessLog

class SessionToolLists:
    def __init__(
        self,
        put: Callable[[bytes], Awaitable[dict[str, JsonValue]]],
        blob: Callable[[ContentRef], Awaitable[bytes]],
        tools: DurableToolPort,
        log: HarnessLog,
    ) -> None:
        self._put = put
        self._blob = blob
        self._tools = tools
        self._log = log
        self._loaded: dict[str, tuple[ToolSchema, ...]] = {}
        # Lists this attempt pinned, until the commit that journals them folds: key -> (list, event).
        self._pinning: dict[str, tuple[tuple[ToolSchema, ...], NewEvent]] = {}

    async def apply(self, call: ModelCall, session: SessionState, events: list[NewEvent]) -> ModelCall:
        key = call.tool_list_key
        if key is None:
            return call
        if key in session.tool_lists:
            self._pinning.pop(key, None)
            pinned = await self._list(key, session)
            if pinned is None:
                return call
            self._held(key, call, pinned)
            return dataclasses.replace(call, tools=list(pinned) or None)
        listed, event = self._pinning.get(key) or await self._pin(key, tuple(call.tools or ()))
        if not any(e is event for e in events):
            events.append(event)
        return dataclasses.replace(call, tools=list(listed) or None)

    def unwrap(self, call: ToolCall) -> ToolCall:
        if call.name != CALL_TOOL or self._tools.describe(CALL_TOOL) is None:
            return call
        return unwrapped(call)

    async def refusal(self, call: ToolCall, session: SessionState) -> tuple[str, ToolErrorCode] | None:
        """A call to a tool the session's lists show, which the run no longer has or whose arguments changed."""
        if not session.tool_lists:
            return None
        pinned: dict[str, ToolSchema] = {}
        for key in session.tool_lists:
            if key.startswith(ENGINE_LIST_PREFIX):
                continue
            for schema in await self._list(key, session) or ():
                _ = pinned.setdefault(schema.name, schema)
        if call.name not in pinned:
            return None
        current = next((s for s in self._tools.schemas() or () if s.name == call.name), None)
        withheld = self._tools.withheld_reason(call.name) if isinstance(self._tools, WithheldTools) else None
        refused = changed_since_pinned(
            call.name, pinned, current, available=self._tools.describe(call.name) is not None, withheld=withheld
        )
        if refused is not None:
            self._log.info("tools.pinned_call_refused", tool=call.name, errorCode=refused[1])
        return refused

    async def _pin(self, key: str, listed: tuple[ToolSchema, ...]) -> tuple[tuple[ToolSchema, ...], NewEvent]:
        ref = await self._put(encode_tools(listed))
        entry: dict[str, JsonValue] = {"ref": ref, "digest": tools_digest(listed), "count": len(listed)}
        event = NewEvent("loop.state_saved", {"slots": {SESSION_TOOLS_SLOT: {key: entry}}})
        self._pinning[key] = (listed, event)
        self._loaded[key] = listed
        self._log.info("tools.session_pinned", key=key, tools=len(listed), digest=entry["digest"])
        return listed, event

    async def _list(self, key: str, session: SessionState) -> tuple[ToolSchema, ...] | None:
        if key in self._loaded:
            return self._loaded[key]
        entry = session.tool_lists.get(key)
        ref = entry.get("ref") if isinstance(entry, dict) else None
        if not isinstance(ref, dict):
            self._log.warning("tools.session_list_unreadable", key=key)
            return None
        listed = decode_tools(await self._blob(ContentRef.model_validate(ref)))
        self._loaded[key] = listed
        return listed

    def _held(self, key: str, call: ModelCall, pinned: tuple[ToolSchema, ...]) -> None:
        offered = tuple(call.tools or ())
        if offered == pinned:
            return
        names, shown = {s.name for s in offered}, {s.name for s in pinned}
        self._log.debug(
            "tools.session_list_held",
            key=key,
            notListed=len(names - shown),
            listedNotOffered=len(shown - names),
        )


__all__ = ["SessionToolLists"]
