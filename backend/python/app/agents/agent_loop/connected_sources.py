"""The sources a run can reach besides company knowledge, known without their tools (ADR-OWD-07 D-DISC-2).

Lazy disclosure keeps a large grant's tool schemas out of the request until the model finds them (C3.29, LZ1), and
MCP tools load only on demand. Before D-DISC-2 it hid the sources with their schemas: a run granted Atlassian
Rovo's 32 tools showed the chat model one placeholder line for it and its Codex job nothing at all, so neither looked
for it. A source is now a fact of the run, whatever its schemas' disclosure: each connected MCP server and connector
toolset of the grant by the name a person knows it by, with one line from its own metadata, in the toolset listing,
in the tool search's ranking and description, and in a job's task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from harness_core.mcp_hub.names import export_description
from harness_core.tools.tags import PROVIDER_TAG_KEY, TAG_PROVIDER_CONNECTOR

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.agent_loop_lib.tools.registry import ToolRegistry

SUMMARY_MAX: Final = 160
# The sources a description or a task names: more would make the list the size lazy disclosure saves.
LISTED_SOURCES_MAX: Final = 12
# `tool_state` key: each MCP instance's group with the name a person knows it by and its summary (MCPToolProvider).
SOURCES_STATE_KEY: Final = "connected_sources"
# The category every MCP server's group is nested under (`lazy_tools_wiring` reads it from here: this module stays
# free of the agent loop's imports, which an idle worker never loads).
MCP_PARENT: Final = "mcp"
# Between an MCP group's server name and its summary: `ToolsetAwareToolIndex` reads both as the source's own words.
SUMMARY_SEPARATOR: Final = " — "
FALLBACK_SUMMARY: Final = "attached MCP server tools."
# A summary read off a server's own tool names: enough to say what it covers, never the size of its listing.
TOOLS_NAMED_MAX: Final = 8
_TOOL_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

Kind = Literal["mcp", "toolset"]


@dataclass(frozen=True, slots=True)
class ConnectedSource:
    name: str
    group: str
    kind: Kind
    summary: str
    # How many of its tools the run may call; ``None`` for a server granted whole and never listed (D-LAZY-7).
    tools: int | None

    def part(self) -> dict[str, Any]:
        """The source as a person reads it in a job's brief (``JobTask.sources``)."""
        part: dict[str, Any] = {"name": self.name, "kind": self.kind, "summary": self.summary}
        if self.tools is not None:
            part["tools"] = self.tools
        return part

    def line(self) -> str:
        return f"{self.name}{SUMMARY_SEPARATOR}{self.summary}" if self.summary else self.name


@dataclass(frozen=True, slots=True)
class ConnectedSources:
    sources: tuple[ConnectedSource, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.sources)

    def listed(self) -> tuple[ConnectedSource, ...]:
        return self.sources[:LISTED_SOURCES_MAX]

    def parts(self) -> list[dict[str, Any]]:
        return [source.part() for source in self.listed()]

    def lines(self) -> list[str]:
        return [source.line() for source in self.listed()]

    def restricted(self, granted: Iterable[str], registry: ToolRegistry) -> ConnectedSources:
        """The sources with a tool in ``granted`` (a child's narrower grant), counted over it."""
        names = set(granted)
        groups = {g.name: g for g in registry.toolsets()}
        kept = []
        for source in self.sources:
            group = groups.get(source.group)
            count = sum(1 for n in group.tool_names if n in names) if group is not None else 0
            if count:
                kept.append(ConnectedSource(source.name, source.group, source.kind, source.summary, count))
        return ConnectedSources(tuple(kept))

    @classmethod
    def of(
        cls,
        registry: ToolRegistry,
        granted: Iterable[str],
        *,
        essential: Iterable[str] = (),
        state: Mapping[str, Any] | None = None,
        known: Iterable[ConnectedSource] = (),
    ) -> ConnectedSources:
        """The grant's sources: each MCP server group and each connector toolset group that is not essential (the
        knowledge base is not a connected source), in the registry's order, with its count of granted tools; then each
        of ``known`` (servers as stored metadata names them, D-LAZY-7) that names none of its tools yet. Reads only the
        registry, the run's state and what is stored: no server is asked anything."""
        names = set(granted)
        kept = set(essential)
        labels = (state or {}).get(SOURCES_STATE_KEY) or {}
        found: list[ConnectedSource] = []
        for group in registry.toolsets():
            if group.name in kept:
                continue
            tools = [n for n in group.tool_names if n in names]
            if not tools:
                continue
            if group.parent == MCP_PARENT:
                kind: Kind = "mcp"
            elif _from_connector(registry, tools[0]):
                kind = "toolset"
            else:
                continue
            label = labels.get(group.name)
            if isinstance(label, dict):
                name, summary = str(label.get("name") or group.name), str(label.get("summary") or "")
            else:
                name, summary = group.name, export_description(group.description, limit=SUMMARY_MAX)
            found.append(ConnectedSource(name=name, group=group.name, kind=kind, summary=summary, tools=len(tools)))
        seen = {source.group for source in found}
        found += [source for source in known if source.group not in seen and source.tools is None]
        return cls(tuple(found))


def known_source(name: str, group: str, summary: str, tools: int | None) -> ConnectedSource:
    """An MCP server as stored metadata knows it (``mcp_known.KnownServer``): no listing behind it (D-LAZY-7)."""
    return ConnectedSource(name=name, group=group, kind="mcp", summary=summary, tools=tools)


def source_summary(
    *,
    description: str | None = None,
    template: str | None = None,
    instructions: str | None = None,
    tools: Iterable[str] = (),
) -> str:
    """One line on what a source covers, from its own metadata: the instance's description, its catalog entry's, the
    first sentence of the server's instructions, or else the names of its tools; empty when none says anything.

    The tool names are the last resort (R36LIVE-FU-2): a server an admin attached without a description was shown to
    the model as "attached MCP server tools.", so asked for "the team's private note" it never looked there, while
    its own ``lookup_note`` said exactly what it holds."""
    for text in (description, template, _first_sentence(instructions)):
        line = export_description(" ".join((text or "").split()), limit=SUMMARY_MAX)
        if line:
            return line
    return tools_summary(tools)


def tools_summary(tools: Iterable[str]) -> str:
    """``Tools: lookup note, create ticket.``: a server's tool names as words, at most ``TOOLS_NAMED_MAX``; a name
    that is not a plain identifier is left out, so nothing but names reaches the line."""
    words = sorted({name.replace("_", " ").replace("-", " ").strip() for name in tools if _TOOL_NAME.match(name)})
    if not words:
        return ""
    more = len(words) - TOOLS_NAMED_MAX
    listed = ", ".join(words[:TOOLS_NAMED_MAX]) + (f" and {more} more" if more > 0 else "")
    return export_description(f"Tools: {listed}.", limit=SUMMARY_MAX)


def group_description(display_name: str, summary: str) -> str:
    """An MCP server's toolset line: its name and what it covers (``ToolsetAwareToolIndex`` reads both)."""
    return f"{display_name}{SUMMARY_SEPARATOR}{summary or FALLBACK_SUMMARY}"


def _first_sentence(text: str | None) -> str:
    flat = " ".join((text or "").split())
    for end in (". ", "! ", "? "):
        at = flat.find(end)
        if at > 0:
            flat = flat[: at + 1]
    return flat


def _from_connector(registry: ToolRegistry, name: str) -> bool:
    try:
        tool = registry.resolve_by_name(name)
    except Exception:
        return False
    return any(t.key == PROVIDER_TAG_KEY and t.value == TAG_PROVIDER_CONNECTOR.value for t in registry.tags_for(tool.path))


__all__ = [
    "FALLBACK_SUMMARY",
    "LISTED_SOURCES_MAX",
    "SOURCES_STATE_KEY",
    "SUMMARY_SEPARATOR",
    "ConnectedSource",
    "ConnectedSources",
    "group_description",
    "known_source",
    "source_summary",
    "tools_summary",
]
