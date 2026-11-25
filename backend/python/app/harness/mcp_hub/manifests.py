"""An MCP server's listed tools as a run may have them (D-MCP-5): what reaches the model of each, and the admission
the build's policy (``app.harness.mcp_hub.tool_admission``) gives a listing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from harness_core.mcp_hub.annotations import declared_hints
from harness_core.mcp_hub.description_scan import finding_codes, scan_descriptor
from harness_core.mcp_hub.errors import ToolDescriptionFlagged
from harness_core.mcp_hub.manifest import (
    DisabledTool,
    ManifestReview,
    ToolDescriptor,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from app.agents.mcp.models import MCPToolInfo


class ManifestConfig(Protocol):
    """The part of ``ConfigurationService`` an admission policy may read and write."""

    async def get_config(
        self,
        key: str,
        default: Any = None,  # noqa: ANN401
        use_cache: bool = False,  # noqa: FBT001, FBT002
        *,
        raise_on_error: bool = False,
    ) -> Any: ...  # noqa: ANN401

    async def set_config(self, key: str, value: Any) -> bool: ...  # noqa: ANN401


def descriptor_of(info: MCPToolInfo) -> ToolDescriptor:
    """What reaches the model of a tool: its server-side name, its escaped description and its schema."""
    return ToolDescriptor(info.name, info.description, dict(info.input_schema), hints=declared_hints(info.annotations))


async def admit_tools(
    config_service: ManifestConfig,
    infos: Iterable[MCPToolInfo],
    *,
    tenant: str,
    instance_id: str,
    at_ms: int,
    instance: Mapping[str, Any] | None = None,
    raw: Mapping[str, ToolDescriptor] | None = None,
) -> tuple[list[MCPToolInfo], ManifestReview]:
    """The listed tools a run may have, as the build's admission policy gives them, and why the others are held.
    ``instance``: the server's record; ``raw``: each tool as its server wrote it, for the scanner."""
    from app.harness.mcp_hub.tool_admission import mcp_tool_admission

    return await mcp_tool_admission().admit(
        config_service, list(infos), tenant=tenant, instance_id=instance_id, at_ms=at_ms, instance=instance, raw=raw
    )


def open_admission(
    listed: Sequence[MCPToolInfo], raw: Mapping[str, ToolDescriptor] | None = None
) -> tuple[list[MCPToolInfo], ManifestReview]:
    """D-MCP-5: the listed tools without an admin's approval, less those the description scanner flags (MX-04),
    read from the server's own text where there is one. Nothing stored is read, so no older manifest blocks a tool."""
    admitted: list[MCPToolInfo] = []
    held: list[DisabledTool] = []
    for info in listed:
        descriptor = descriptor_of(info)
        if finding_codes(scan_descriptor((raw or {}).get(info.name, descriptor))):
            held.append(DisabledTool(info.name, ToolDescriptionFlagged.reason_code, descriptor.digest()))
        else:
            admitted.append(info)
    review = ManifestReview(tuple(descriptor_of(i) for i in admitted), tuple(held), ())
    return admitted, review


__all__ = [
    "ManifestConfig",
    "admit_tools",
    "descriptor_of",
    "open_admission",
]
