"""``call_tool``: how a model calls a tool that is not in its tool list, on every executor.

A session's tool list is fixed when the session starts (R4: prompt caching, and no tool swapped in mid-session).
A tool found later (``search_tools``, ``fetch_tools``) is called through ``call_tool`` with the name and arguments
it answered. Whoever runs the call unwraps it first, so grant, argument validation, approval, flow guard and journal
are the named tool's: the native kernel (``RunDriver._plan``), the legacy loop (``tool_loop.execute_tool_call``) and
the Tool Gateway (LZ1, ``harness_core.gateway.disclosure``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from harness_core.kernel.core.messages import ToolCall

if TYPE_CHECKING:
    from collections.abc import Mapping

CALL_TOOL: Final = "call_tool"
SEARCH_TOOLS: Final = "search_tools"
FETCH_TOOLS: Final = "fetch_tools"

CALL_TOOL_DESCRIPTION: Final = (
    "Call a tool search_tools found, by its exact name, with arguments matching its input schema."
)
CALL_TOOL_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The tool's exact name, as search_tools answered it."},
        "arguments": {"type": "object", "description": "The tool's arguments."},
    },
    "required": ["name"],
}


def unwrap_call(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | str:
    """``call_tool``'s target and its arguments, or why the call is malformed (the text the model is shown)."""
    name, inner = arguments.get("name"), arguments.get("arguments", {})
    if not isinstance(name, str) or not name or name in (SEARCH_TOOLS, CALL_TOOL, FETCH_TOOLS):
        return "call_tool needs the name of a tool search_tools found"
    if not isinstance(inner, dict):
        return "call_tool's arguments must be an object"
    return name, dict(inner)


def unwrapped(call: ToolCall) -> ToolCall:
    """The call ``call`` makes: the named tool's, under the same call id; any other call, or a malformed
    ``call_tool`` (its own refusal answers it), unchanged."""
    if call.name != CALL_TOOL:
        return call
    target = unwrap_call(call.arguments)
    if isinstance(target, str):
        return call
    name, arguments = target
    return ToolCall(id=call.id, name=name, arguments=arguments)


__all__ = [
    "CALL_TOOL",
    "CALL_TOOL_DESCRIPTION",
    "CALL_TOOL_INPUT_SCHEMA",
    "FETCH_TOOLS",
    "SEARCH_TOOLS",
    "unwrap_call",
    "unwrapped",
]
