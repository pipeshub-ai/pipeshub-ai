"""SourceCatalog — the single source of truth for knowledge-source rendering.

Every connector ID appears in exactly one place: `SourceCatalog.render()`.
All other prompt sections that formerly re-listed sources use this module.

Finding H (plan): there are two prompt shapes.
  - Agent route (`/agent/{id}/chat/stream`): `agent_knowledge` is populated,
    IDs are actionable → `ids_actionable=True`.
  - Chat route (`/chat/stream`): `agent_knowledge` is empty, only
    `available_connectors` (type names only, no UUIDs) is present →
    `ids_actionable=False`.

Callers use `from_state(state)` which detects the shape automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.modules.agents.capability_summary import (
    classify_knowledge_sources,
    format_connector_filter_lines,
)
from app.modules.agents.context.retrieval_routing import build_routing_guidance


class SourceKind(StrEnum):
    KB = "kb"
    APP = "app"


@dataclass(frozen=True)
class KnowledgeSource:
    """One searchable source — either a KB collection or an app connector."""

    label: str
    kind: SourceKind
    app: str          # connector type key (e.g. "confluence"); "" for KB
    source_id: str    # connector_id / record_group_id; "" when no id (KB-no-id case)
    scope_lines: tuple[str, ...] = field(default_factory=tuple)  # type: ignore[assignment]

    @property
    def list_files_param(self) -> str:
        """Parameter name to pass this source's ID to `knowledgehub.list_files`.

        The two tools disagree on how a KB id is passed:
          - KB collection  → `record_group_ids`  (knowledge_hub.py:451)
          - app connector  → `connector_ids`
        """
        if self.kind == SourceKind.KB:
            return "record_group_ids"
        return "connector_ids"

    @property
    def retrieval_param(self) -> str:
        """The parameter name is always `connector_ids` for
        `retrieval.search_internal_knowledge` — a KB's id is its connector_id."""
        return "connector_ids"


@dataclass(frozen=True)
class SourceCatalog:
    """Immutable per-request catalog of all knowledge sources.

    Build once via `from_state(state)`, then pass to every section that
    needs to mention a source or its ID.
    """

    sources: tuple[KnowledgeSource, ...]
    ids_actionable: bool  # False on the chat route — see Finding H

    # ---------------------------------------------------------------------------
    # Factory
    # ---------------------------------------------------------------------------

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "SourceCatalog":
        """Build a catalog from a `ChatState` / `tool_state` dict.

        Two input shapes are supported:
          1. `agent_knowledge` is non-empty → agent route; IDs actionable.
          2. `agent_knowledge` is empty but `available_connectors` is set →
             chat route; IDs withheld (ids_actionable=False).
        """
        agent_knowledge: list = state.get("agent_knowledge") or []
        connector_configs: dict = state.get("connector_configs") or {}

        if agent_knowledge:
            classified = classify_knowledge_sources(
                agent_knowledge,
                connector_configs=connector_configs if isinstance(connector_configs, dict) else None,
            )
            sources: list[KnowledgeSource] = []
            for s in classified:
                kind = SourceKind.KB if s.get("source_type") == "kb" else SourceKind.APP
                scope: list[str] = []
                for fl in format_connector_filter_lines(s.get("filters")):
                    scope.append(fl)
                sources.append(KnowledgeSource(
                    label=s.get("label", ""),
                    kind=kind,
                    app=s.get("type_key", "") if kind == SourceKind.APP else "",
                    source_id=s.get("connector_id", ""),
                    scope_lines=tuple(scope),
                ))
            return cls(sources=tuple(sources), ids_actionable=True)

        # Chat route fallback: available_connectors gives type names only
        available_connectors: list = state.get("available_connectors") or []
        sources_chat: list[KnowledgeSource] = []
        seen_types: set[str] = set()
        for c in available_connectors:
            t = (c.get("type") or "").upper()
            if t and t not in seen_types:
                seen_types.add(t)
                sources_chat.append(KnowledgeSource(
                    label=t,
                    kind=SourceKind.APP,
                    app=t.lower(),
                    source_id="",  # no UUID on chat route
                ))
        return cls(sources=tuple(sources_chat), ids_actionable=False)

    # ---------------------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------------------

    def is_empty(self) -> bool:
        return not self.sources

    def duplicate_apps(self) -> tuple[str, ...]:
        """App type keys that appear more than once (same-type duplicate connectors)."""
        counts: dict[str, int] = {}
        for s in self.sources:
            if s.kind == SourceKind.APP and s.app:
                counts[s.app] = counts.get(s.app, 0) + 1
        return tuple(k for k, v in counts.items() if v > 1)

    def live_only_apps(self, toolset_apps: frozenset[str]) -> tuple[str, ...]:
        """App type keys that have toolset tools but NO indexed knowledge entry.

        These are the services that should be queried via their live API alone —
        retrieval has no snapshot for them. Derives the list dynamically rather
        than hardcoding "Slack, Outlook, Gmail, Calendar".
        """
        indexed_apps = frozenset(
            s.app.lower() for s in self.sources
            if s.kind == SourceKind.APP and s.app
        )
        return tuple(sorted(toolset_apps - indexed_apps))

    def kb_sources(self) -> tuple[KnowledgeSource, ...]:
        return tuple(s for s in self.sources if s.kind == SourceKind.KB)

    def app_sources(self) -> tuple[KnowledgeSource, ...]:
        return tuple(s for s in self.sources if s.kind == SourceKind.APP)

    # ---------------------------------------------------------------------------
    # Renderer
    # ---------------------------------------------------------------------------

    def render(self, tool_names: list[str] | None = None) -> str:
        """Return the canonical source-table text.

        This is the ONE place in the entire prompt where connector IDs appear.
        Every other section references the catalog by label, never by ID.
        Also the sole place the duplicate-connector warning
        (``build_routing_guidance``) renders — directly under the table it
        disambiguates, rather than a separate section elsewhere.

        When ``tool_names`` is provided the header resolves tool references to
        their granted spellings (double-underscore form), so the printed names
        are callable.  Omitting it falls back to the dotted canonical forms.
        """
        if self.is_empty():
            return ""

        from app.modules.agents.context.tool_names import granted

        _tool_names = tool_names or []
        retrieval = (
            granted("knowledgegraph.search", _tool_names)
            or granted("retrieval.search_internal_knowledge", _tool_names)
            or "knowledgegraph__search"
        )
        navigate = (
            granted("knowledgegraph.navigate", _tool_names)
            or "knowledgegraph__navigate"
        )
        list_files = (
            granted("knowledgegraph.list_files", _tool_names)
            or granted("knowledgehub.list_files", _tool_names)
            or "knowledgegraph__list_files"
        )

        lines: list[str] = [
            "## Knowledge Sources",
            "",
        ]

        # Canonical knowledge-system block — always rendered when has_knowledge,
        # regardless of ids_actionable. On the chat route (ids_actionable=False)
        # the model has no concrete IDs to pass, so the per-source ID table is
        # omitted, but the structural model and tool mapping still help the agent
        # decide which tool to use for what kind of question.
        lookup = granted("knowledgegraph.lookup_record", _tool_names)
        fetch = granted("knowledgegraph.fetch_record", _tool_names)
        if not lookup:
            lookup = "knowledgegraph__lookup_record"
        lines.extend([
            "**The knowledge system.** A source (an app connector or a KB) holds record "
            "groups (a project, space, drive, folder), which hold records (one ticket, page, "
            "file), and a record holds its content as blocks. Records nest — an epic over its "
            "stories, a page over its child pages. Every record has a Record ID and a Location "
            "showing where it sits.",
            "",
            "**Every tool returns metadata or content.**",
            f"- `{retrieval}(query=...)` — content. The top-ranked blocks from the best-matching "
            "records. Not all blocks of any record, and not every record that matches: it is a "
            "ranked sample, so its result set is never a complete list of what exists.",
            f"- `{list_files}(query=...)`, `{lookup}(identifiers=[...])`, "
            f'`{navigate}(node_id="<id>")` — metadata: Record IDs, fields, and structure. No content.',
            *(
                [f"- `{fetch}(record_ids=[...])` — one whole record, all of it."]
                if fetch else []
            ),
            "",
            "**Combining them.** Search finds a starting point by wording; navigate on a "
            "record's Location or Record ID reveals what search structurally cannot — siblings, "
            "children, linked records, and the group's total count. For a question that must be "
            "exhaustive (\"all\", \"how many\", \"every\"), do not count search results: navigate "
            "the record group or scope list_files to one source, then read what matters.",
            "",
        ])
        # ID-specific header — only when connector IDs are actionable (agent route).
        # On the chat route the model has no IDs to pass so we skip this instruction.
        if self.ids_actionable:
            lines.extend([
                f"Pass a source's id as `source_ids: [\"<id>\"]` to "
                f"`{retrieval}` or `{list_files}`, "
                f"and as `node_id` to `{navigate}`. "
                "Both KB ids and app connector ids are accepted as `source_ids`.",
                "",
            ])

        for s in self.sources:
            if not self.ids_actionable:
                # Chat route: no UUIDs — just name the type
                lines.append(f"- {s.label}")
                continue

            if s.kind == SourceKind.KB:
                if s.source_id:
                    line = f"- **{s.label}** — KB collection — id `{s.source_id}`"
                else:
                    line = f"- **{s.label}** — KB collection (omit connector_ids to search full KB)"
            else:
                line = f"- **{s.label}** — app: {s.app} — id `{s.source_id}`"
                if s.scope_lines:
                    line += " — " + "; ".join(s.scope_lines)

            lines.append(line)

        routing = build_routing_guidance(self)
        if routing:
            lines.append("")
            lines.append(routing)

        return "\n".join(lines)
