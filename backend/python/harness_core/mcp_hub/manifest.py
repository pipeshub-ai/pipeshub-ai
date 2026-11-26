"""Manifest approval (the rug-pull guard): an admin approves what a server's tools say; anything else waits.

A tool's digest covers what reaches the model: its name, description and input
schema, in RFC 8785 canonical form. A tool is admitted only while its digest equals
the approved one; a changed or new tool is disabled until an admin approves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.contracts.canonical_json import canonical_digest
from harness_core.engines.types import JsonValue
from harness_core.mcp_hub.errors import ToolChanged, ToolUnapproved

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """One tool as its server lists it (raw name, not the exported one)."""

    name: str
    description: str | None
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # What the tool does and where its arguments go (the `effect` and `sink` tags): they decide approvals, so a
    # declared value is covered. MCP servers declare neither, which keeps their approved digests as they were.
    effect: str | None = None
    sink: str | None = None
    # An MCP tool's behaviour hints (``readOnlyHint``, ``openWorldHint``, …): they set its effect and sink (FG2), so a
    # declared hint is covered too and a server that flips one loses its approval. None declared, no change.
    hints: Mapping[str, bool] = field(default_factory=dict[str, bool])

    def digest(self) -> str:
        """The one tool descriptor digest: run pins (version 2), MCP approvals and the catalog all use it."""
        covered: dict[str, JsonValue] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }
        if self.effect is not None:
            covered["effect"] = self.effect
        if self.sink is not None:
            covered["sink"] = self.sink
        if self.hints:
            covered["annotations"] = dict(self.hints)
        return canonical_digest(covered)


@dataclass(frozen=True, slots=True)
class ApprovedManifest:
    """The digests an admin approved, per raw tool name."""

    tools: Mapping[str, str]
    approved_by: str
    approved_at: int

    def digest(self) -> str:
        return canonical_digest(dict(self.tools))

    def granted_digest(self, names: Iterable[str]) -> str | None:
        """What a run that may call ``names`` of this server pins: the approved entries of exactly those tools.
        None when one of them is not approved (there is nothing approved to bind it to)."""
        wanted = sorted(set(names))
        if not wanted or any(name not in self.tools for name in wanted):
            return None
        return canonical_digest({name: self.tools[name] for name in wanted})


@dataclass(frozen=True, slots=True)
class DisabledTool:
    name: str
    reason_code: str
    digest: str
    approved_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestReview:
    admitted: tuple[ToolDescriptor, ...]
    disabled: tuple[DisabledTool, ...]
    removed: tuple[str, ...]

    @property
    def drifted(self) -> bool:
        return bool(self.disabled or self.removed)

    def drift_digest(self) -> str:
        """Names one drift, so the same drift is recorded once however many requests see it."""
        return canonical_digest(
            {
                "disabled": sorted([d.name, d.reason_code, d.digest] for d in self.disabled),
                "removed": sorted(self.removed),
            }
        )


def review_manifest(approved: ApprovedManifest | None, discovered: Iterable[ToolDescriptor]) -> ManifestReview:
    approved_tools: Mapping[str, str] = approved.tools if approved is not None else {}
    admitted: list[ToolDescriptor] = []
    disabled: list[DisabledTool] = []
    listed: set[str] = set()
    for tool in discovered:
        listed.add(tool.name)
        digest = tool.digest()
        expected = approved_tools.get(tool.name)
        if expected is None:
            disabled.append(DisabledTool(tool.name, ToolUnapproved.reason_code, digest))
        elif expected != digest:
            disabled.append(DisabledTool(tool.name, ToolChanged.reason_code, digest, expected))
        else:
            admitted.append(tool)
    removed = tuple(sorted(set(approved_tools) - listed))
    return ManifestReview(tuple(admitted), tuple(disabled), removed)


__all__ = ["ApprovedManifest", "DisabledTool", "ManifestReview", "ToolDescriptor", "review_manifest"]
