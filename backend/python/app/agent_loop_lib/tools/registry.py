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

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.hooks.middleware.routing import path_match
from app.agent_loop_lib.tools.base import Tag, Tool, ToolSummary

if TYPE_CHECKING:
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.tools.provider import ToolsetProvider
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

    `parent` makes groups hierarchical — e.g. a `connectors` category
    group (own `tool_names` empty) with `jira`/`slack`/... registered as
    children (`parent="connectors"`). This is purely an organizational
    layer over the same flat `tool_names` membership: `ToolRegistry.
    tools_in_toolset` resolves a category to the union of its descendants'
    tools, and `toolset_overview` renders the tree — nothing else in the
    registry needs to know a group has children.
    """

    name: str
    description: str
    tool_names: list[str] = Field(default_factory=list)
    parent: str | None = None


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
        # Provider-backed (not-yet-materialized) tools: cheap summaries
        # only — see `tools/provider.py`. `_provider_owner` maps a tool
        # name to the `ToolsetProvider` that can build it; once a name is
        # materialized (`materialize()`), it moves into `_tools_by_path`/
        # `_path_by_name` above like any eagerly-registered tool, and every
        # sync lookup (`resolve_by_name`, `schemas`, `has`) treats it
        # identically from then on — these two dicts stop being consulted
        # for that name.
        self._providers: dict[str, "ToolsetProvider"] = {}
        self._provider_owner: dict[str, str] = {}
        self._provider_tool_summary: dict[str, ToolSummary] = {}
        # One lock per provider-backed name that's had `materialize()`
        # attempted — serializes concurrent `fetch_tools`/`search_tools`/
        # `tool_preloading` calls that race to materialize the SAME name
        # (see `materialize()`'s TOCTOU note below). Created lazily via a
        # synchronous `setdefault`, so creating the lock itself can't race.
        self._materialize_locks: dict[str, asyncio.Lock] = {}

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

    def register_tool_if_absent(self, tool: Tool, *, extra_tags: tuple[Tag, ...] = ()) -> bool:
        """Idempotent `register_tool`: a no-op returning `False` if
        `tool.name` is already registered, otherwise registers it and
        returns `True`.

        For callers (e.g. `hooks/citations.py`'s dynamic
        `_FetchFullRecordTool` registration) that discover "this tool
        should exist" independently from several concurrent POST_TOOL_USE
        invocations — the plain check-then-act `if not has(name):
        register_tool(...)` raises `DuplicateToolNameError` on whichever
        call loses that race, aborting the rest of ITS OWN middleware
        (e.g. the grant that should follow registration) even though the
        tool ends up registered either way. This collapses check-and-act
        into one call so every caller's own follow-up work always runs.
        """
        if tool.name in self._path_by_name:
            return False
        self.register_tool(tool, extra_tags=extra_tags)
        return True

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
            toolset.name, toolset.description, [tool.name for tool in toolset.tools],
            parent=toolset.parent,
        )

    def register_toolset(
        self, name: str, description: str, tool_names: list[str], *, parent: str | None = None,
    ) -> None:
        """Group already-registered tool names under a named, overview-only
        capability. Registering the same name again replaces it. `parent`
        nests this group under another (already- or later-registered —
        resolution is by name at lookup time, not at registration time)
        group name, e.g. `register_toolset("jira", ..., parent="connectors")`."""
        self._groups[name] = ToolsetGroup(
            name=name, description=description, tool_names=list(tool_names), parent=parent,
        )

    def register_toolset_provider(self, provider: "ToolsetProvider") -> None:
        """Register a toolset whose tools are materialized on demand (see
        `tools/provider.py`) instead of built eagerly. Only the cheap
        `summary()`/`list_tools()` are read now; `materialize(name)` is
        awaited lazily, exactly once per name, by whichever caller is about
        to make that name resolvable — `fetch_tools`/`search_tools` (see
        `tools/builtin/lazy_toolsets.py`) and `tool_preloading` are the only
        three call sites that grow `agent.visible_tools`, so they're the
        only ones that need to call `materialize`/`materialize_many`.

        Raises:
            DuplicateToolNameError: if any tool name collides with an
                already-registered (or another provider's) tool name.
        """
        summary = provider.summary()
        tool_summaries = provider.list_tools()
        for ts in tool_summaries:
            if ts.name in self._path_by_name or ts.name in self._provider_owner:
                raise DuplicateToolNameError(ts.name)
        for ts in tool_summaries:
            self._provider_owner[ts.name] = summary.name
            self._provider_tool_summary[ts.name] = ts
        self._providers[summary.name] = provider
        self.register_toolset(
            summary.name, summary.description,
            [ts.name for ts in tool_summaries], parent=summary.parent,
        )

    def is_provider_backed(self, name: str) -> bool:
        return name in self._provider_owner

    def is_materialized(self, name: str) -> bool:
        return name in self._path_by_name

    async def materialize(self, name: str) -> Tool:
        """Builds and registers the real `Tool` for a provider-backed name,
        then caches it exactly like any eagerly-registered tool — every
        subsequent `resolve_by_name`/`schemas()` call for `name` is a plain
        sync dict lookup afterward. A no-op returning the cached tool if
        `name` is already materialized (whether it started out provider-
        backed or was never anything but a plain registered tool).

        Guarded by a per-name lock: `await self._providers[owner].
        materialize(name)` is a genuine await (the provider may hit a
        network/subprocess), so two concurrent callers (e.g. one turn's
        `fetch_tools` and `search_tools` racing to materialize the same
        toolset — see `tools/builtin/lazy_toolsets.py`) could otherwise both
        pass the `name in self._path_by_name` check, both materialize, and
        have the second `register_tool` raise `DuplicateToolNameError`.
        The lock serializes them; the loser re-checks after acquiring it and
        returns the winner's already-registered tool instead of racing.

        Raises:
            DuplicateToolPathError / DuplicateToolNameError: if the
                provider hands back a `Tool` whose path/name collides with
                something already registered — a provider bug, since it
                already advertised this exact name via `list_tools()`.
            ToolNotFoundError: `name` isn't registered under any provider
                or as a plain tool.
        """
        if name in self._path_by_name:
            return self._tools_by_path[self._path_by_name[name]]
        owner = self._provider_owner.get(name)
        if owner is None:
            raise ToolNotFoundError(name)
        lock = self._materialize_locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name in self._path_by_name:
                return self._tools_by_path[self._path_by_name[name]]
            tool = await self._providers[owner].materialize(name)
            self.register_tool(tool)
            return tool

    async def materialize_many(self, names: Iterable[str]) -> None:
        """Materializes every provider-backed, not-yet-materialized name in
        `names`; a plain or already-materialized name is a no-op. Call this
        BEFORE adding provider-backed names to `agent.visible_tools` — once
        a name is visible, the turn loop resolves its schema synchronously
        (`agent/tool_loop.py::tool_schemas_for_turn`), so materialization
        can't happen lazily at that point."""
        for name in names:
            if name in self._provider_owner and name not in self._path_by_name:
                await self.materialize(name)

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
        regardless of tool registration order). Provider-backed tools that
        haven't been materialized yet (see `tools/provider.py`) are included
        via their cached `ToolSummary` — the whole point of provider-backed
        toolsets is that discovery/search never has to pay materialization
        cost, so `search_tools` finds them exactly like any other tool."""
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
        for name in sorted(self._provider_tool_summary):
            if name in self._path_by_name:
                continue  # materialized already -> already covered above
            summary = self._provider_tool_summary[name]
            if path_pattern is not None and not path_match(summary.path, path_pattern):
                continue
            if tags:
                tag_map = {t.key: t.value for t in summary.tags}
                if not all(tag_map.get(k) == v for k, v in tags.items()):
                    continue
            results.append(summary)
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

    def expand_tool_names(self, tool_names: list[str]) -> list[str]:
        """Resolve each name in *tool_names* to concrete registered tool
        names, expanding toolset group names (e.g. ``"jira"`` ->
        ``["jira__search_issues", "jira__create_issue", ...]``).

        Resolution order per name:

        1. **Exact match** — the name is already a registered tool name.
        2. **Toolset group** — the name matches a ``register_toolset`` group;
           expand to that group's ``tool_names``.
        3. **Prefix** — find all registered names starting with
           ``f"{name}__"`` (the ``app_name__tool_name`` convention).
        4. **Skip** — no match at all; the name is silently dropped (same
           as the previous ``schemas()`` behaviour).

        Deduplication preserves first-seen order.
        """
        expanded: list[str] = []
        seen: set[str] = set()

        def _add(n: str) -> None:
            if n not in seen:
                seen.add(n)
                expanded.append(n)

        for name in tool_names:
            if name in self._path_by_name:
                _add(name)
                continue
            group = self._groups.get(name)
            if group is not None:
                for member in group.tool_names:
                    if member in self._path_by_name:
                        _add(member)
                continue
            prefix = f"{name}__"
            matched = [n for n in self._path_by_name if n.startswith(prefix)]
            if matched:
                for m in matched:
                    _add(m)
                continue

        return expanded

    def schemas(self, tool_names: list[str] | None = None) -> list["ToolSchema"]:
        """Return tool schemas for the LLM API.

        ``tool_names`` — if given, resolve each to concrete tool names
        (expanding toolset group names and ``app__`` prefixes — see
        ``expand_tool_names``), then return schemas for the resulting set.
        If ``None`` or empty, return all.
        """
        names_iter = (
            self.expand_tool_names(tool_names)
            if tool_names
            else list(self._path_by_name.keys())
        )
        return [self.resolve_by_name(name).to_schema() for name in names_iter]

    # ---- toolset groups (progressive disclosure) ---------------------------

    def toolsets(self) -> list[ToolsetGroup]:
        return list(self._groups.values())

    def has_toolsets(self) -> bool:
        return bool(self._groups)

    def children_of(self, name: str | None) -> list[ToolsetGroup]:
        """Direct children of `name` (or every top-level group, for `None`)."""
        return [g for g in self._groups.values() if g.parent == name]

    def toolset_overview(self, parent: str | None = None) -> list[dict[str, Any]]:
        """One-line-per-toolset summary tree for the `list_toolsets`
        meta-tool / system prompt overview — top-level groups by default;
        pass a group name to get just that node's children (drilling into
        a category, e.g. "connectors" -> "jira"/"slack"/...). Each entry
        carries a `"children"` list only when that group actually has any,
        so a flat (non-hierarchical) registry renders exactly as before."""
        overview = []
        for g in self._groups.values():
            if g.parent != parent:
                continue
            entry: dict[str, Any] = {
                "name": g.name,
                "description": g.description,
                "tool_count": len(self.tools_in_toolset(g.name)),
            }
            children = self.toolset_overview(parent=g.name)
            if children:
                entry["children"] = children
            overview.append(entry)
        return overview

    def _resolve_group(self, name: str) -> ToolsetGroup | None:
        """Exact lookup first; case-insensitive fallback if the model
        capitalised or changed casing of the toolset name."""
        group = self._groups.get(name)
        if group is not None:
            return group
        lower = name.lower()
        for key, g in self._groups.items():
            if key.lower() == lower:
                return g
        return None

    def toolset_detail(self, name: str) -> dict[str, Any] | None:
        """Per-tool one-line descriptions for one toolset (recursing into
        any children) — the "agent knows the Jira toolset exists, and gets
        a one-line description of each tool inside it" tier, one level
        cheaper than paying for full schemas via `fetch_tools`. Returns
        `None` for an unknown toolset name."""
        group = self._resolve_group(name)
        if group is None:
            return None
        tools: list[dict[str, str]] = []
        for tool_name in self.tools_in_toolset(group.name):
            path = self._path_by_name.get(tool_name)
            if path is not None:
                tool = self._tools_by_path[path]
                tools.append({"name": tool.name, "short_description": tool.short_description})
                continue
            provider_summary = self._provider_tool_summary.get(tool_name)
            if provider_summary is not None:
                tools.append({
                    "name": provider_summary.name,
                    "short_description": provider_summary.short_description,
                })
        return {
            "name": group.name,
            "description": group.description,
            "tools": tools,
            "children": [child.name for child in self.children_of(group.name)],
        }

    def tools_in_toolset(self, name: str, *, _seen: frozenset[str] = frozenset()) -> list[str]:
        """Tool names belonging to `name`, including (recursively) every
        descendant group's tools — a leaf group's own `tool_names` plus,
        for a category group, the union of its children. `_seen` guards
        against a misconfigured parent cycle; not part of the public API."""
        if name in _seen:
            return []
        group = self._resolve_group(name) if not _seen else self._groups.get(name)
        if group is None:
            return []
        seen = _seen | {group.name}
        names = list(group.tool_names)
        for child in self.children_of(group.name):
            names.extend(self.tools_in_toolset(child.name, _seen=seen))
        deduped: list[str] = []
        added: set[str] = set()
        for n in names:
            if n not in added:
                added.add(n)
                deduped.append(n)
        return deduped

    def grouped_tool_names(self) -> set[str]:
        """Every tool name that belongs to at least one toolset — these are
        the ones eligible for lazy disclosure; anything else is always
        visible (treated as an "essential", per the design tenet)."""
        grouped: set[str] = set()
        for group in self._groups.values():
            grouped.update(group.tool_names)
        return grouped
