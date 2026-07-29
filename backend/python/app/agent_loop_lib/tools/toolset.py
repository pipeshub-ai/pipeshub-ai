"""Toolset interface: a named, tagged collection of related Tools.

A Toolset groups tools that share a provider or purpose, e.g. all filesystem
operations (`ls`, `read_file`, `write_file`, ...) live under one
`FilesystemToolset` whose `path_prefix` is `/toolsets/filesystem`. Every tool
in the set must have a `path` equal to `f"{path_prefix}/{tool.name}"` — the
`ToolRegistry` enforces this at registration time.

Finer-grained categorization (read vs. write vs. destructive, risk level,
required scope, ...) is deliberately *not* encoded in the path. Use `Tag` on
the toolset and/or individual tools instead, and scope middleware with
`agent_loop.hooks.middleware.routing.by_tag`/`by_tags` — this keeps a tool's
address stable even if its risk classification changes, and lets a single
toolset mix categories freely.

This also doubles as the Phase-1 progressive-disclosure unit: a toolset's
`description` is what `list_toolsets` shows the model as a one-line overview
before it ever pays for the full schemas of the tools inside (see
`ToolRegistry.toolset_overview`/`tools_in_toolset`).

`ToolsetBuilder` is the concrete implementation for connector classes (Jira,
Confluence, Slack, ...) that use the ``@tool`` decorator on their async
methods — it scans a class instance, binds decorated methods, and produces
`BoundMethodTool` instances ready for registration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tag, Tool, ToolSummary
from app.agent_loop_lib.tools.decorators import (
    TOOL_META_ATTR,
    BoundMethodTool,
    ToolMeta,
)
from app.agent_loop_lib.tools.errors import ToolNotFoundError

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry

__all__ = ["Toolset", "ToolsetBuilder"]

logger = logging.getLogger(__name__)

# `(attr_name, ToolMeta)` pairs found by `_tool_attrs_for_class`, keyed by
# the connector CLASS (not instance) — a class's set of `@tool`-decorated
# methods is fixed at import time and never changes for the life of the
# process, so the `dir()` scan below only needs to run once per class no
# matter how many per-request instances (each wrapping a different,
# freshly authenticated client) get built from it afterward. Deliberately
# process-global, not per-request: nothing here is request- or
# credential-scoped, so there's nothing to invalidate.
_TOOL_ATTR_CACHE: dict[type, list[tuple[str, ToolMeta]]] = {}


def _tool_attrs_for_class(cls: type) -> list[tuple[str, ToolMeta]]:
    """One-time reflection over every `@tool`-decorated attribute on `cls`.
    Looked up via the CLASS rather than an instance so a `@property` on the
    connector class returns the descriptor itself (never invoking the
    getter) instead of evaluating it against whichever instance happens to
    trigger the first cache miss — irrelevant for attributes that actually
    carry `TOOL_META_ATTR`, but avoids a surprising side effect on ones that
    don't."""
    cached = _TOOL_ATTR_CACHE.get(cls)
    if cached is not None:
        return cached
    found: list[tuple[str, ToolMeta]] = []
    for attr_name in dir(cls):
        if attr_name.startswith("_"):
            continue
        attr = getattr(cls, attr_name, None)
        if attr is None or not callable(attr):
            continue
        func = getattr(attr, "__func__", attr)
        meta: ToolMeta | None = getattr(func, TOOL_META_ATTR, None)
        if meta is None:
            continue
        found.append((attr_name, meta))
    _TOOL_ATTR_CACHE[cls] = found
    return found


class Toolset(ABC):
    """Abstract base class for a logical grouping of related tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique toolset identifier, e.g. 'filesystem'."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this toolset provides."""

    @property
    @abstractmethod
    def path_prefix(self) -> str:
        """Base path for all tools in this set, e.g. '/toolsets/filesystem'."""

    @property
    def tags(self) -> list[Tag]:
        """Toolset-level tags, merged into each child tool's tags on discovery."""
        return []

    @property
    def parent(self) -> str | None:
        """Optional owning category group name (e.g. `"connectors"`) for
        hierarchical `list_toolsets` presentation — see `ToolRegistry.
        toolset_overview`. `None` (default) registers this toolset as a
        top-level group, same as before this existed."""
        return None

    @property
    @abstractmethod
    def tools(self) -> list[Tool]:
        """The concrete tools that belong to this toolset."""

    def get_tool(self, name: str) -> Tool:
        """Resolve a tool by its short name (not full path) within this toolset.

        Raises:
            ToolNotFoundError: if no tool with that name belongs to this toolset.
        """
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise ToolNotFoundError(f"{self.path_prefix}/{name}")

    def list_tools(self) -> list[ToolSummary]:
        """Lightweight summaries for lazy discovery.

        Each tool's own tags are merged with this toolset's tags (toolset tags
        first, so a tool can override a toolset-level tag with the same key by
        declaring its own tag afterwards — last-write-wins on duplicate keys
        is left to the consumer, both are included here as-is).
        """
        summaries = []
        for tool in self.tools:
            summary = tool.to_summary()
            merged_tags = tuple(self.tags) + summary.tags
            summaries.append(
                ToolSummary(
                    name=summary.name,
                    short_description=summary.short_description,
                    path=summary.path,
                    tags=merged_tags,
                )
            )
        return summaries


class ToolsetBuilder(Toolset):
    """Builds a `Toolset` from a class instance's ``@tool``-decorated methods.

    This is the standard way to expose a connector class (Jira, Confluence,
    Slack, ...) as a set of agent-callable tools::

        jira = Jira(client)
        toolset = ToolsetBuilder(
            jira,
            name="jira",
            description="JIRA issue tracking and project management",
            path_prefix="/tools/jira",
            tags=[Tag(key="provider", value="atlassian")],
        )
        toolset.register_into(registry)

    The ``path_prefix`` **must** match the prefix used in each
    ``@tool(path=...)`` declaration on the class. The builder validates
    this at construction time and logs a warning for mismatches (but still
    includes the tool, since the path is the authoritative value).
    """

    def __init__(
        self,
        instance: Any,
        *,
        name: str,
        description: str,
        path_prefix: str,
        tags: list[Tag] | None = None,
    ) -> None:
        self._instance = instance
        self._name = name
        self._description = description
        self._path_prefix = path_prefix
        self._tags = list(tags or [])
        self._tools = self._collect_tools()

    def _collect_tools(self) -> list[BoundMethodTool]:
        tools: list[BoundMethodTool] = []
        for attr_name, meta in _tool_attrs_for_class(type(self._instance)):
            attr = getattr(self._instance, attr_name, None)
            if attr is None or not callable(attr):
                continue
            if not meta.path.startswith(self._path_prefix):
                logger.warning(
                    "Tool %r has path %r which does not start with toolset "
                    "path_prefix %r — including it anyway",
                    attr_name, meta.path, self._path_prefix,
                )
            tools.append(BoundMethodTool(attr, meta))
        return tools

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def path_prefix(self) -> str:
        return self._path_prefix

    @property
    def tags(self) -> list[Tag]:
        return list(self._tags)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    def register_into(self, registry: "ToolRegistry") -> None:
        """Register all tools individually and create a toolset group.

        Uses ``register_tool`` + ``register_toolset`` rather than the
        stricter ``register_toolset_object`` — the latter validates
        ``tool.path == f"{path_prefix}/{tool.name}"``, which doesn't hold
        for connector tools where ``tool.name`` is ``app__method`` (globally
        unique for LLM addressing) while the path uses the short method name.
        """
        for t in self._tools:
            registry.register_tool(t, extra_tags=tuple(self._tags))
        registry.register_toolset(
            self._name,
            self._description,
            [t.name for t in self._tools],
        )
