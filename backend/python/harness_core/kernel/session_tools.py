"""One tool list per chat session (R4): what the native loop shows a model is fixed when the session starts.

The first request of a session journals the tools array it sent (``SESSION_TOOLS_SLOT``, the array in the content
store, one entry per list key: a chat that changes mode gets a list per mode). Every later request of the session,
in this run or a later one, after a compaction, a resume or a re-drive on another worker, sends that array byte for
byte: the provider's prompt cache keeps the tools prefix, and nothing can swap a tool in mid-session. A tool found
later is called through ``call_tool``.

A pinned tool the run no longer has (its MCP server removed it, an admin took it away) or whose input schema changed
fails closed with a plain message; a new chat picks the change up. A changed description alone is not refused: the
chat keeps showing the description it started with, and the arguments still match.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from harness_core.contracts import canonical_digest
from harness_core.kernel.core.tool_schema import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.contracts.generated.enums import ToolErrorCode

SESSION_TOOLS_SLOT: Final = "session.toolLists"
# The keys of an engine's gateway lists in the same slot (``harness_core.engines.durable.tool_pin``): the loop's
# own lists are the others.
ENGINE_LIST_PREFIX: Final = "engine:"


def _entries(tools: Sequence[ToolSchema]) -> list[dict[str, object]]:
    return [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]


def encode_tools(tools: Sequence[ToolSchema]) -> bytes:
    """The array as stored: every field, in the order sent."""
    return json.dumps(_entries(tools), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_tools(data: bytes) -> tuple[ToolSchema, ...]:
    raw = json.loads(data.decode("utf-8"))
    return tuple(
        ToolSchema(name=str(t["name"]), description=str(t["description"]), input_schema=dict(t["inputSchema"]))
        for t in raw
    )


def tools_digest(tools: Sequence[ToolSchema]) -> str:
    return canonical_digest(_entries(tools))


def changed_since_pinned(
    name: str,
    pinned: Mapping[str, ToolSchema],
    current: ToolSchema | None,
    *,
    available: bool,
    withheld: str | None = None,
) -> tuple[str, ToolErrorCode] | None:
    """Why a call to ``name`` cannot run as the chat's list shows it, and the error code; ``None`` when it can.
    ``available``: the run has the tool. ``withheld``: why the run is never given it, which is then the answer: the
    tool was not removed (QAFINAL-FU-PW113)."""
    shown = pinned.get(name)
    if shown is None:
        return None
    if not available and withheld is not None:
        return withheld, "not_found"
    if not available:
        return (
            f"{name} is no longer available: it was removed after this chat started. "
            "Start a new chat to use the tools available now.",
            "not_found",
        )
    if current is not None and canonical_digest(current.input_schema) != canonical_digest(shown.input_schema):
        return (
            f"{name} changed after this chat started, so this chat cannot call it as listed. "
            "Start a new chat to use its new version.",
            "invalid_call",
        )
    return None


__all__ = [
    "ENGINE_LIST_PREFIX",
    "SESSION_TOOLS_SLOT",
    "changed_since_pinned",
    "decode_tools",
    "encode_tools",
    "tools_digest",
]
