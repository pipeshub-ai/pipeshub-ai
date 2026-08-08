"""`ToolsetProvider`: the seam for toolsets whose tools are too expensive to
build eagerly at registration time.

Most toolsets are cheap to construct fully up front — instantiating a
handful of `Tool` objects costs nothing. Some are not: an MCP server round
trip to list tools, a remote catalog fetch, or any toolset with enough tools
that building every one of them "just in case" would be wasted work for the
vast majority of runs that never touch most of it.

`ToolsetProvider` is deliberately narrower than `Toolset` (`tools/toolset.py`):
`Toolset.tools` is an eagerly-computed property — nothing stops an
implementation from making it expensive, but the registry has no way to
avoid paying for it. A `ToolsetProvider` never exposes a `tools` property at
all — only a cheap `summary()` + `list_tools()` (both pure/local, no I/O)
plus a per-tool `materialize()` coroutine — so the registry can enforce "no
full `Tool` object is built until something actually needs it" structurally,
not just by convention.

No concrete MCP (or other remote) provider ships in this module — this is
the interface a future one plugs into `ToolRegistry.register_toolset_provider`
without any change to the registry, the turn loop, or the meta-tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import Tool, ToolSummary

__all__ = ["ToolsetSummary", "ToolsetProvider", "EagerToolsetProvider"]


@dataclass(frozen=True)
class ToolsetSummary:
    """What `list_toolsets` needs about a provider-backed toolset —
    the same shape `ToolRegistry.toolset_overview()` renders for any other
    toolset, just sourced from `ToolsetProvider.summary()` instead of a
    plain `register_toolset(...)` call."""

    name: str
    description: str
    parent: str | None = None


@runtime_checkable
class ToolsetProvider(Protocol):
    """A toolset whose tools are materialized lazily, one at a time.

    `summary()`/`list_tools()` must be cheap and safe to call repeatedly —
    they back `list_toolsets`/`search_tools` for every run that has this
    toolset registered, not just the first one that ends up needing it.
    `materialize(name)` is where the expensive work belongs (an MCP
    `tools/list` + building an adapter `Tool`, a remote schema fetch, ...);
    the registry calls it at most once per tool name and caches the result
    (see `ToolRegistry.materialize`), so implementations don't need their
    own memoization.
    """

    def summary(self) -> "ToolsetSummary": ...

    def list_tools(self) -> "list[ToolSummary]": ...

    async def materialize(self, name: str) -> "Tool": ...


class EagerToolsetProvider:
    """Wraps an already-built list of `Tool`s as a `ToolsetProvider` — the
    trivial case where there's nothing to actually defer. Exists so
    call sites that want to go through the provider seam uniformly (or
    tests exercising it) don't need to write a bespoke provider just to
    wrap tools that were cheap to build in the first place."""

    def __init__(
        self, name: str, description: str, tools: "list[Tool]", *, parent: str | None = None,
    ) -> None:
        self._summary = ToolsetSummary(name=name, description=description, parent=parent)
        self._tools_by_name = {tool.name: tool for tool in tools}

    def summary(self) -> ToolsetSummary:
        return self._summary

    def list_tools(self) -> "list[ToolSummary]":
        return [tool.to_summary() for tool in self._tools_by_name.values()]

    async def materialize(self, name: str) -> "Tool":
        return self._tools_by_name[name]
