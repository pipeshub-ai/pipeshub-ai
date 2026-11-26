"""A tool's identity is its instance; the name a model sees is derived from it.

A granted tool is ``ToolRef(source, instance, tool)`` (wire form ``source@instance/tool``):
``source`` is the toolset type (``jira``) or ``mcp``, ``instance`` the configured instance's id.
Two instances of one type are two identities. The exported name stays today's whenever the
type has one instance among the tools being named, so every grant that exists today keeps its
names (and every journal its calls); only when a type has several instances does each carry
the instance's tag. The tag is a digest of the instance id, never its editable display name, so
a rename does not rename tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from harness_core.mcp_hub.names import export_namespace, fit_export_name, name_digest
from harness_core.tool_identity.errors import ToolRefInvalid

if TYPE_CHECKING:
    from collections.abc import Iterable

# The instance a grant names when the definition bound no instance (a type-keyed toolset).
LEGACY_INSTANCE = "default"
_SOURCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_INSTANCE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_UNSAFE_PART = re.compile(r"[^A-Za-z0-9_-]")
# 64 (the providers' function names) less the longest prefix an engine adds (``mcp__tools__``):
# a scoped name reaches every engine's model, so a new name never makes a grant refusable.
SCOPED_NAME_MAX = 51
# Leaves a scoped tool at least ten characters of its own name before a digest.
_SCOPED_GROUP_MAX = 30


@dataclass(frozen=True, slots=True, order=True)
class ToolRef:
    source: str
    instance: str
    tool: str

    @classmethod
    def parse(cls, tool_id: str) -> ToolRef:
        source, at, rest = tool_id.partition("@")
        instance, slash, tool = rest.partition("/")
        if not (at and slash) or not (
            _SOURCE.fullmatch(source) and _INSTANCE.fullmatch(instance) and _TOOL.fullmatch(tool)
        ):
            raise ToolRefInvalid(f"{tool_id[:300]!r} is not source@instance/tool")
        return cls(source, instance, tool)

    @property
    def type_keyed(self) -> bool:
        return self.instance == LEGACY_INSTANCE

    @override
    def __str__(self) -> str:
        return f"{self.source}@{self.instance}/{self.tool}"


def type_key(name: str) -> str:
    """The comparable form of a toolset type, as the graph stores toolset names."""
    return name.lower().strip().replace(" ", "").replace("_", "")


def instance_tag(instance: str) -> str:
    return name_digest(instance)


def shared_types(bindings: Iterable[tuple[str, str]]) -> frozenset[str]:
    """The types bound to more than one instance among ``(type, instance)`` pairs."""
    instances: dict[str, set[str]] = {}
    for kind, instance in bindings:
        instances.setdefault(type_key(kind), set()).add(instance)
    return frozenset(kind for kind, seen in instances.items() if len(seen) > 1)


def scoped_group(group: str, instance: str, *, shared: bool) -> str:
    """A connector toolset's group (``jira``) for one instance: unchanged unless its type is shared."""
    if not shared:
        return group
    tag = f"_{instance_tag(instance)}"
    return _UNSAFE_PART.sub("_", group)[: _SCOPED_GROUP_MAX - len(tag)] + tag


def scoped_tool(group: str, instance: str, tool: str) -> tuple[str, str]:
    """``(group, tool)`` path segments of one instance's tool, whose ``{group}__{tool}`` fits ``SCOPED_NAME_MAX``."""
    scoped = scoped_group(group, instance, shared=True)
    method = _UNSAFE_PART.sub("_", tool)
    room = SCOPED_NAME_MAX - len(scoped) - 2
    if method == tool and 0 < len(method) <= room:
        return scoped, method
    suffix = f"_{name_digest(scoped, tool)}"
    # No trailing "_" before the digest: "__" stays the one group separator in the name.
    return scoped, method[: room - len(suffix)].rstrip("_") + suffix


def connector_tool_name(group: str, instance: str, tool: str, *, shared: bool) -> str:
    """The name a model sees: today's ``{group}__{tool}`` unless the type is shared."""
    if not shared:
        return f"{group}__{tool}"
    scoped, method = scoped_tool(group, instance, tool)
    return f"{scoped}__{method}"


def scoped_mcp_tool_name(server_type: str, instance: str, tool: str) -> str:
    """A shared MCP type's tool for one instance: ``mcp_{type}_{tag}_{tool}``, escaped as P2.8 exports, within ``SCOPED_NAME_MAX``."""
    scoped = f"{export_namespace(server_type)}_{instance_tag(instance)}"
    return fit_export_name(f"mcp_{scoped}_{tool}", scoped, tool, limit=SCOPED_NAME_MAX)


__all__ = [
    "LEGACY_INSTANCE",
    "SCOPED_NAME_MAX",
    "ToolRef",
    "connector_tool_name",
    "instance_tag",
    "scoped_group",
    "scoped_mcp_tool_name",
    "scoped_tool",
    "shared_types",
    "type_key",
]
