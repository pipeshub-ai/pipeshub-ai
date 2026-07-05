"""Path-addressable tool registry with a name-based facade for LLM interop.

Tools are registered — and middleware routes — by hierarchical `path`
(`/toolsets/{toolset}/{tool_name}`), but the LLM's function-calling interface
only ever sees the tool's short, globally-unique `name` (`ToolCall.name`).
This registry is the single place that bridges the two: `register_tool`
indexes a tool under both; `resolve` looks up by path (for middleware/
`ToolExecutor`), `resolve_by_name` looks up by name (for the turn loop's
name-based dispatch and every other pre-existing name-based call site).

`register_toolset` is a *lightweight* grouping of already-registered tool
names for progressive disclosure (the `list_toolsets`/`fetch_tools` meta-tool
pair) — independent of path structure. `register_toolset_object` is the
richer alternative from `agent_loop.tools.toolset.Toolset`: it registers every
tool in the set AND creates the matching lightweight group in one call,
validating that each tool's path matches `{path_prefix}/{tool.name}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.hooks.middleware.routing import path_match
from app.agent_loop_lib.tools.base import Tag, Tool, ToolSummary

if TYPE_CHECKING:
    from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
    InvalidToolPathError,
    ToolNotFoundError,
    ToolPathMismatchError,
)
from app.agent_loop_lib.tools.toolset import Toolset

__all__ = ["ToolRegistry", "ToolsetGroup"]


class ToolsetGroup(BaseModel):
    """A named capability group (Skills, Memory, Web Search, Filesystem, a
    given MCP server, ...) presented to the model as a one-line overview
    instead of N full tool schemas upfront — the Phase 1 progressive tool
    disclosure design tenet. `fetch_tools(toolset)` loads the real schemas
    for `tool_names` on demand.

    Deliberately independent of the path-based `Tool.path` structure: a
    group is just a named subset of already-registered tool *names*, so
    ControlPlane can build groups conditionally at wiring time (only if
    the underlying tools ended up registered at all) without needing every
    grouped tool to share a literal path prefix.
    """

    name: str
    description: str
    tool_names: list[str] = Field(default_factory=list)


def _validate_path(path: str) -> None:
    if not path.startswith("/"):
        raise InvalidToolPathError(path, "must start with '/'")
    segments = [s for s in path.split("/") if s]
    if not segments:
        raise InvalidToolPathError(path, "must have at least one non-empty segment")
    if any(s in ("*", "**") for s in segments):
        raise InvalidToolPathError(path, "must not contain wildcard segments ('*'/'**')")


class ToolRegistry:
    """Registers `Tool` instances by path (primary) and by name (LLM-facing)."""

    def __init__(self) -> None:
        self._tools_by_path: dict[str, Tool] = {}
        self._path_by_name: dict[str, str] = {}
        self._extra_tags_by_path: dict[str, tuple[Tag, ...]] = {}
        self._groups: dict[str, ToolsetGroup] = {}

    # ---- registration ----------------------------------------------------

    def register_tool(self, tool: Tool, *, extra_tags: tuple[Tag, ...] = ()) -> None:
        """Register a single tool instance under its own `path` and `name`.

        Raises:
            InvalidToolPathError: if `tool.path` isn't a well-formed absolute path.
            DuplicateToolPathError: if another tool is already at that path.
            DuplicateToolNameError: if another tool already has that name —
                names must stay globally unique since the LLM addresses
                tools by name, not path.
        """
        _validate_path(tool.path)
        if tool.path in self._tools_by_path:
            raise DuplicateToolPathError(tool.path)
        if tool.name in self._path_by_name:
            raise DuplicateToolNameError(tool.name)
        self._tools_by_path[tool.path] = tool
        self._path_by_name[tool.name] = tool.path
        if extra_tags:
            self._extra_tags_by_path[tool.path] = extra_tags

    def register_toolset_object(self, toolset: Toolset) -> None:
        """Register every tool in `toolset`, plus a matching lightweight group.

        Raises:
            ToolPathMismatchError: if any tool's path doesn't equal
                `f"{toolset.path_prefix}/{tool.name}"`.
        """
        for tool in toolset.tools:
            expected = f"{toolset.path_prefix}/{tool.name}"
            if tool.path != expected:
                raise ToolPathMismatchError(tool.path, toolset.path_prefix, tool.name)
        for tool in toolset.tools:
            self.register_tool(tool, extra_tags=tuple(toolset.tags))
        self.register_toolset(
            toolset.name, toolset.description, [tool.name for tool in toolset.tools]
        )

    def register_toolset(self, name: str, description: str, tool_names: list[str]) -> None:
        """Group already-registered tool names under a named, overview-only
        capability. Registering the same name again replaces it."""
        self._groups[name] = ToolsetGroup(
            name=name, description=description, tool_names=list(tool_names)
        )

    # ---- path-based lookup (middleware / ToolExecutor) --------------------

    def resolve(self, path: str) -> Tool:
        try:
            return self._tools_by_path[path]
        except KeyError:
            raise ToolNotFoundError(path) from None

    def has_path(self, path: str) -> bool:
        return path in self._tools_by_path

    def tags_for(self, path: str) -> tuple[Tag, ...]:
        """The tool's effective tags: its owning toolset's tags (if any),
        merged with its own — see `Toolset.list_tools`."""
        tool = self.resolve(path)
        return tuple(self._extra_tags_by_path.get(path, ())) + tuple(tool.tags)

    def discover(
        self,
        path_pattern: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> list[ToolSummary]:
        """Cheap, phase-1 discovery: summaries matching an optional path glob
        and/or an optional tag filter (AND semantics across `tags`).

        Results are sorted by path for stable, deterministic output (e.g. for
        the `fetch_tools` tool, where the model sees the same ordering
        regardless of tool registration order)."""
        results: list[ToolSummary] = []
        for path in sorted(self._tools_by_path):
            tool = self._tools_by_path[path]
            if path_pattern is not None and not path_match(path, path_pattern):
                continue
            effective_tags = self.tags_for(path)
            if tags:
                tag_map = {t.key: t.value for t in effective_tags}
                if not all(tag_map.get(k) == v for k, v in tags.items()):
                    continue
            results.append(
                ToolSummary(
                    name=tool.name,
                    short_description=tool.short_description,
                    path=path,
                    tags=effective_tags,
                )
            )
        return results

    # ---- name-based lookup (LLM-facing) ------------------------------------

    def resolve_by_name(self, name: str) -> Tool:
        path = self._path_by_name.get(name)
        if path is None:
            raise ToolNotFoundError(name)
        return self._tools_by_path[path]

    def path_for_name(self, name: str) -> str | None:
        return self._path_by_name.get(name)

    def has(self, name: str) -> bool:
        return name in self._path_by_name

    def names(self) -> list[str]:
        return list(self._path_by_name.keys())

    def tags_for_name(self, name: str) -> tuple[Tag, ...]:
        path = self._path_by_name.get(name)
        return self.tags_for(path) if path is not None else ()

    def schemas(self, tool_names: list[str] | None = None) -> list["ToolSchema"]:
        """Return tool schemas for the LLM API.

        `tool_names` — if given, only return schemas for those names (unknown
        names are silently skipped). If None or empty, return all.
        """
        names_iter = (
            [n for n in tool_names if n in self._path_by_name]
            if tool_names
            else list(self._path_by_name.keys())
        )
        return [self.resolve_by_name(name).to_schema() for name in names_iter]

    # ---- toolset groups (progressive disclosure) ---------------------------

    def toolsets(self) -> list[ToolsetGroup]:
        return list(self._groups.values())

    def has_toolsets(self) -> bool:
        return bool(self._groups)

    def toolset_overview(self) -> list[dict[str, Any]]:
        """One-line-per-toolset summary for the `list_toolsets` meta-tool."""
        return [
            {"name": g.name, "description": g.description, "tool_count": len(g.tool_names)}
            for g in self._groups.values()
        ]

    def tools_in_toolset(self, name: str) -> list[str]:
        group = self._groups.get(name)
        return list(group.tool_names) if group is not None else []

    def grouped_tool_names(self) -> set[str]:
        """Every tool name that belongs to at least one toolset — these are
        the ones eligible for lazy disclosure; anything else is always
        visible (treated as an "essential", per the design tenet)."""
        grouped: set[str] = set()
        for group in self._groups.values():
            grouped.update(group.tool_names)
        return grouped
