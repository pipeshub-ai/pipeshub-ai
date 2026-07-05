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
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.tools.base import Tag, Tool, ToolSummary
from app.agent_loop_lib.tools.errors import ToolNotFoundError

__all__ = ["Toolset"]


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
