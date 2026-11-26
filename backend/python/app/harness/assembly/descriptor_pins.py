"""Pins that bind a run to what was approved (R2 F4): a tool's descriptor and an MCP server's approved manifest.

Admission asks ``ToolDescriptors`` for each granted tool's descriptor digest (name, description, the whole input
schema, effect, sink and MCP hints in RFC 8785 form: a toolset tool's as its code declares it, an MCP tool's as an
admin approved it) and Node pins them as ``digestVersion: 2``. A claim's item runtimes
(``app.harness.assembly.items``, CPD-7) ask again and compare: a schema that changed, or an approval that moved, since
admission is ``CapabilityLost`` (bounded re-checks, then ``capability_lost``), never a run on what nobody pinned. A
pin without a version predates this, or had nothing approved to bind to, and is not verified. While the MCP hub is off
no admin approves an MCP tool: ``LiveMcpDescriptors`` binds the tools found by live discovery instead, at admission
and again at the claim; a tool its server lists at another digest than its pin is withheld at the claim, never
registered, as the hub withholds one held as changed (WP-84).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from app.agents.registry.tool_descriptors import toolset_tool_descriptor

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.harness.contracts.generated.pipeshub.run_spec import McpItem
    from app.harness.mcp_hub.manifests import ManifestConfig
    from harness_core.contracts.generated.run_spec import ToolGrant
    from harness_core.engines.types import JsonValue
    from harness_core.mcp_hub.manifest import ApprovedManifest

DESCRIPTOR_DIGEST_VERSION = 2
MCP_SOURCE = "mcp"


class ToolDescriptorSource(Protocol):
    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        """The descriptor digest of a toolset's tool as its code declares it; None for one the release lacks."""
        ...

    async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
        """The server's approved manifest; None when none was approved. ``ManifestUnavailable`` when unreadable."""
        ...


class ToolDescriptors:
    """``ToolDescriptorSource`` over the release's toolset registry and the MCP tools the build's admission policy
    approved (none where no admin approves MCP tools)."""

    def __init__(self, manifests: ManifestConfig) -> None:
        self._manifests = manifests

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        from app.agents.registry.toolset_registry import get_toolset_registry

        registry = get_toolset_registry()
        if not registry.get_all_toolsets():
            registry.auto_discover_toolsets()
        metadata = registry.get_toolset_metadata(toolset) or {}
        tools = cast("list[dict[str, JsonValue]]", metadata.get("tools") or [])
        declared = next((t for t in tools if t.get("name") == tool), None)
        if declared is None:
            return None
        try:
            return toolset_tool_descriptor(declared).digest()
        except (TypeError, ValueError):
            return None

    async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
        from app.harness.mcp_hub.tool_admission import mcp_tool_admission

        return await mcp_tool_admission().approved_manifest(self._manifests, instance_id)


class LiveMcpDescriptors:
    """``ToolDescriptorSource`` whose MCP manifests are the tools discovered live (``mcp_digests``): what governs an
    MCP server while the hub is off. A server that was not discovered has none."""

    def __init__(self, base: ToolDescriptorSource, live: Mapping[str, Mapping[str, str]]) -> None:
        self._base = base
        self._live = live

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        return self._base.toolset_tool(toolset, tool)

    async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
        from harness_core.mcp_hub.manifest import ApprovedManifest

        tools = self._live.get(instance_id)
        return ApprovedManifest(tools=dict(tools), approved_by="live", approved_at=0) if tools else None


class DescriptorPin(BaseModel):
    """One version-2 pin: a granted tool (``tool:<toolId>``) or an MCP server's approved manifest (``mcp:<id>``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern="^(?:tool|mcp)$")
    ref: str
    digest: str
    # An MCP pin covers the server's tools the run was granted, by their raw names.
    tools: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ref}"


def pinned_descriptors(tools: Iterable[ToolGrant], servers: Iterable[McpItem]) -> tuple[DescriptorPin, ...]:
    """The spec's version-2 pins; an MCP pin covers the server's granted tools (by raw name, as approved)."""
    pins = [
        DescriptorPin(kind="tool", ref=grant.tool_id, digest=grant.descriptor_digest)
        for grant in tools
        if grant.digest_version == DESCRIPTOR_DIGEST_VERSION
    ]
    granted: dict[str, list[str]] = {}
    for grant in tools:
        source, instance, tool = _parts(grant.tool_id)
        if source == MCP_SOURCE:
            granted.setdefault(instance, []).append(tool)
    pins += [
        DescriptorPin(
            kind="mcp",
            ref=server.instance_id,
            digest=server.manifest_digest,
            tools=tuple(sorted(granted.get(server.instance_id, ()))),
        )
        for server in servers
        if server.digest_version == DESCRIPTOR_DIGEST_VERSION
    ]
    return tuple(pins)


def _parts(tool_id: str) -> tuple[str, str, str]:
    source, rest = tool_id.split("@", 1)
    instance, tool = rest.split("/", 1)
    return source, instance, tool
