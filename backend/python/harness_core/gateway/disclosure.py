"""Lazy tool disclosure at the gateway (LZ1, MCP-E2E §9): what ``tools/list`` shows an engine of a large grant.

The run's grant is unchanged: what may be called is the pin. Only the list shrinks: the grant less ``deferred``
(the product's connector and MCP tools), plus ``search_tools``, which answers names, descriptions and schemas from
the grant, and ``call_tool``, which the endpoint unwraps into a direct call of the named tool, so grant, argument
validation, approval, flow guard and journal are the direct call's and name the real tool. The list is fixed for the
attempt (no ``tools/list_changed``) and a function of the pin, so a resumed attempt shows what the first showed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from harness_core.gateway.types import McpResult, McpTool
from harness_core.tool_identity.spec_names import CALL_TOOL_SPEC
from harness_core.tools.call_tool import (
    CALL_TOOL,
    CALL_TOOL_DESCRIPTION,
    CALL_TOOL_INPUT_SCHEMA,
    SEARCH_TOOLS,
)
from harness_core.tools.call_tool import unwrap_call as _unwrap

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.kernel.core.tool_schema import ToolSchema

SEARCH_LIMIT: Final = 8
_MAX_LIMIT: Final = 20
INVALID_DISCLOSURE_CALL: Final = "invalid_disclosure_call"

SEARCH_DESCRIPTION: Final = (
    "Find tools of this run that are not listed (connected apps and MCP servers) by what they do. Answers each "
    "match's name, description and input schema; call one with call_tool."
)
# ADR-OWD-07 D-DISC-2: the sources the run's unlisted tools come from, each "<name> — <what it covers>", so an engine
# knows a connected server exists before it loads any of its tools. Bounded: the list is what lazy disclosure saves.
SOURCES_LISTED_MAX: Final = 12
SOURCE_LINE_MAX: Final = 160
_SOURCES_LEAD = " Connected sources: "
_SEARCH_SCHEMA: Final[Mapping[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What the tool should do, in a few words."},
        "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT},
    },
    "required": ["query"],
}
_SEARCH = McpTool(SEARCH_TOOLS, SEARCH_DESCRIPTION, _SEARCH_SCHEMA)
_CALL = McpTool(CALL_TOOL, CALL_TOOL_DESCRIPTION, CALL_TOOL_INPUT_SCHEMA)


def search_description(sources: Sequence[str]) -> str:
    """``search_tools``' description: what it does, then the sources it finds tools of, when there are any."""
    named = [line[:SOURCE_LINE_MAX] for line in sources[:SOURCES_LISTED_MAX] if line]
    return SEARCH_DESCRIPTION + (_SOURCES_LEAD + "; ".join(named) + "." if named else "")


def _refused(text: str) -> McpResult:
    return McpResult(text=text, is_error=True, disposition="refused", retryable=True, reason_code=INVALID_DISCLOSURE_CALL)


def unwrap_call(arguments: Mapping[str, JsonValue]) -> tuple[str, Mapping[str, JsonValue]] | McpResult:
    """``call_tool``'s target and its arguments, or the refusal of a malformed call."""
    target = _unwrap(arguments)
    return _refused(target) if isinstance(target, str) else target


def shown_tool(tool: str, arguments: Mapping[str, JsonValue], *, prefix: str = "") -> str:
    """The name an engine's activity line shows: for a ``call_tool`` through the gateway (``prefix``: the engine's
    name for the gateway's server), the tool it calls; any other tool as the engine named it."""
    name = arguments.get("name")
    called = tool.removeprefix(prefix) in (CALL_TOOL, CALL_TOOL_SPEC)
    return name if called and isinstance(name, str) and name else tool


def shown_arguments(tool: str, arguments: Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
    """The arguments of the tool an activity line shows (``shown_tool``'s): a ``call_tool`` wrapper's inner ones when
    ``arguments`` wrap a call of ``tool``, else ``arguments`` as they are."""
    if not arguments or arguments.get("name") != tool:
        return arguments
    unwrapped = unwrap_call(arguments)
    return unwrapped[1] if isinstance(unwrapped, tuple) and unwrapped[0] == tool else arguments


@dataclass(frozen=True, slots=True)
class Ranked:
    """A ranking's matches, best first, and the matching tools the run cannot use, each with why (a tool its source
    refused at first use: not approved, changed): named in the answer, never in place of a tool that can run."""

    names: Sequence[str]
    held: Mapping[str, str] = field(default_factory=lambda: {})


HELD_LEAD: Final = "Matching tools this run can't use: "


def _held_text(held: Mapping[str, str], shown: Callable[[str], str]) -> str:
    return HELD_LEAD + "".join(f"\n- {shown(name)}: {why}" for name, why in list(held.items())[:_MAX_LIMIT])


@dataclass(frozen=True, slots=True)
class LazyDisclosure:
    """``search`` ranks the grant's tools for a query (the product's ranking), best first, by name; a ranking may also
    name the matches it held (``Ranked``)."""

    deferred: frozenset[str]
    rank: Callable[[str, int], Awaitable[Sequence[str] | Ranked]]
    sources: tuple[str, ...] = ()
    """The run's connected sources, one line each (``search_description``)."""
    always: bool = False
    """Applies whatever the grant holds now: tools built at their source's first use (an MCP server listed then,
    ADR-OWD-07 D-LAZY-4) are found only through the search, so it is listed even before any of them is built."""

    def applies(self, granted: Sequence[ToolSchema]) -> bool:
        return self.always or any(s.name in self.deferred for s in granted)

    def listed(self, tools: tuple[McpTool, ...]) -> tuple[McpTool, ...]:
        search = McpTool(SEARCH_TOOLS, search_description(self.sources), _SEARCH_SCHEMA) if self.sources else _SEARCH
        return (*(t for t in tools if t.name not in self.deferred), search, _CALL)

    async def search(
        self,
        arguments: Mapping[str, JsonValue],
        granted: Sequence[ToolSchema] | Callable[[], Sequence[ToolSchema]],
        shown: Callable[[str], str] = str,
    ) -> McpResult:
        """``shown``: the name the engine is shown for a granted tool (OWD-7 spec names), as ``tools/list`` shows it.
        ``granted`` as a callable is read after the ranking, which may build the tools it found (D-LAZY-4)."""
        query, limit = arguments.get("query"), arguments.get("limit", SEARCH_LIMIT)
        if not isinstance(query, str) or not query.strip():
            return _refused("search_tools needs a query")
        if not isinstance(limit, int) or isinstance(limit, bool):
            limit = SEARCH_LIMIT
        ranked = await self.rank(query, max(1, min(limit, _MAX_LIMIT)))
        names, held = (ranked.names, ranked.held) if isinstance(ranked, Ranked) else (ranked, {})
        by_name = {s.name: s for s in (granted() if callable(granted) else granted)}
        found = [by_name[n] for n in names if n in by_name]
        text = json.dumps(
            [{"name": shown(s.name), "description": s.description, "inputSchema": s.input_schema} for s in found],
            ensure_ascii=False,
        ) if found else f"No tool of this run matches {query!r}." + ("" if held else " Try other words.")
        if held:
            text += "\n\n" + _held_text(held, shown)
        return McpResult(text=text, is_error=False, disposition="executed", retryable=False)


__all__ = [
    "CALL_TOOL", "SEARCH_DESCRIPTION", "SEARCH_TOOLS", "LazyDisclosure", "Ranked", "search_description", "shown_tool",
    "unwrap_call",
]
