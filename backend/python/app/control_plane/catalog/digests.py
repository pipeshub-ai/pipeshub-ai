"""One digest scheme for catalog items (02-lld §5.4), over RFC 8785 canonical JSON.

Node computes the same (``control-plane/domain/digests.ts``); the shared vectors are
``contracts/control-plane/vectors/digests.json``. Each descriptor kind digests its own
fields; an item's ``descriptorSetDigest`` covers every descriptor by name, so the core
verifies a set without knowing any kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.contracts.canonical_json import canonical_digest
from harness_core.mcp_hub.annotations import declared_hints
from harness_core.mcp_hub.manifest import ToolDescriptor
from harness_core.skills import skill_bundle_manifest_digest

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.skills import BundleFileEntry

ENGINE_DIGEST_FIELDS = ("kind", "version", "binaryDigest", "optionsDigest")


class DuplicateDescriptorName(ValueError):
    reason = "duplicate_descriptor_name"


def tool_descriptor_digest(tool: Mapping[str, object]) -> str:
    """``ToolDescriptor.digest``, the digest run pins and MCP approvals use, over a wire-form descriptor."""
    description = tool.get("description")
    effect, sink = tool.get("effect"), tool.get("sink")
    return ToolDescriptor(
        str(tool["name"]),
        description if isinstance(description, str) else None,
        cast("dict[str, JsonValue]", tool.get("inputSchema") or {}),
        effect=effect if isinstance(effect, str) else None,
        sink=sink if isinstance(sink, str) else None,
        hints=declared_hints(tool.get("annotations")),
    ).digest()


def engine_digest(engine: Mapping[str, object]) -> str:
    return canonical_digest({field: engine.get(field) for field in ENGINE_DIGEST_FIELDS})


def skill_descriptor_digest(files: Mapping[str, BundleFileEntry]) -> str:
    """The skill's bundle digest, recomputed from its file list without the bytes."""
    return skill_bundle_manifest_digest(files)


def descriptor_set_digest(named_digests: Iterable[tuple[str, str]]) -> str:
    by_name: dict[str, str] = {}
    for name, digest in named_digests:
        if name in by_name:
            raise DuplicateDescriptorName(f"descriptor {name!r} appears twice")
        by_name[name] = digest
    return canonical_digest(by_name)
