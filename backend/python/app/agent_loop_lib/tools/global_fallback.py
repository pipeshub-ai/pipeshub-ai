"""`GlobalCatalogFallback`: the seam `search_tools` (see
`tools/builtin/lazy_toolsets.py::SearchToolsTool`) falls back to when a
query has zero hits in the CURRENT agent's `ToolRegistry` — a product may
have a much larger process-wide catalog of tools that exist but aren't
attached/enabled for this particular agent/org/user (e.g. a connector the
org hasn't installed). Surfacing those as a distinct "exists, but not
attached" result (instead of a flat "no such tool") lets the model tell the
user what to do next, and lets the caller emit an SSE event the frontend can
turn into an attach/connect prompt (`EventType.TOOL_UNAVAILABLE`).

Kept as a small Protocol — same DIP rationale as `ToolsetProvider`/
`ToolIndex` — so the library stays product-agnostic: PipesHub supplies the
real implementation (backed by `app.agents.tools.registry.
_global_tools_registry`) at wiring time; nothing here knows PipesHub exists.
`global_fallback=None` (the default) is a true no-op — zero behavior change
for callers that don't wire one in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["GlobalToolHit", "GlobalCatalogFallback"]


@dataclass(frozen=True)
class GlobalToolHit:
    """One tool found in the global catalog but not in the caller's
    registry. `reason` defaults to `"not_attached"` — the only reason a
    search-time fallback can determine on its own; `"not_authenticated"`
    is a distinct, execute-time signal (see `tool_adapter.py`'s
    `ToolsetAuthError` handling), never produced from a search miss."""

    name: str
    toolset: str | None
    description: str
    reason: str = "not_attached"


@runtime_checkable
class GlobalCatalogFallback(Protocol):
    """Implemented by the host application, not this library."""

    async def search(self, query: str, limit: int) -> list[GlobalToolHit]: ...
