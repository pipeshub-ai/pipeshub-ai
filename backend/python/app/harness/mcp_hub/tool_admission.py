"""Which of an MCP server's listed tools a harness run may use (D-MCP-5).

This build needs no admin approval of MCP tools: a connected server's discovered tools are usable at once, and the
description scanner holds back only the tools it flags (MX-04). An edition extension may supply its own policy through
``app.edition_config.mcp_tool_admission``; every caller asks the policy, never a store of approvals, so a build without
one keeps nothing of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from app.agents.mcp.models import MCPToolInfo
    from app.harness.mcp_hub.manifests import ManifestConfig
    from app.harness.mcp_hub.run_pinning import McpRunPinning
    from harness_core.mcp_hub.manifest import (
        ApprovedManifest,
        ManifestReview,
        ToolDescriptor,
    )


class McpToolAdmission(Protocol):
    async def requires_admin_approval(self, org_id: str, config_service: ManifestConfig) -> bool:
        """Whether the org's runs use only the tools an admin approved. ``config_service`` is the caller's own: where
        an edition keeps its setting, it reads it there, so the policy holds no configuration client of its own."""
        ...

    async def admit(
        self,
        config_service: ManifestConfig,
        infos: Sequence[MCPToolInfo],
        *,
        tenant: str,
        instance_id: str,
        at_ms: int,
        instance: Mapping[str, Any] | None = None,
        raw: Mapping[str, ToolDescriptor] | None = None,
    ) -> tuple[list[MCPToolInfo], ManifestReview]:
        """The listed tools a run may have, and why the others are held. ``raw``: each tool as its server wrote it,
        for the description scanner."""
        ...

    async def approved_manifest(self, config_service: ManifestConfig, instance_id: str) -> ApprovedManifest | None:
        """The tools an admin approved for the server, which a run is pinned to; None: no approval binds it."""
        ...

    async def approved_document(self, config_service: ManifestConfig, instance_id: str) -> dict[str, Any] | None:
        """The stored approval as the capability sync reads it; None: none."""
        ...

    async def unapproved(self, config_service: ManifestConfig, instance_id: str, names: Iterable[str]) -> list[str]:
        """Of ``names``, the tools an agent may not be saved with until an admin approves them."""
        ...

    async def standing(
        self, config_service: ManifestConfig, instance_id: str, infos: Sequence[MCPToolInfo]
    ) -> list[dict[str, Any]] | None:
        """Each listed tool's approval as the builder shows it, in the listing's order; None: there is none to show."""
        ...

    async def forget(self, config_service: Any, instance_id: str) -> None:  # noqa: ANN401 - ConfigurationService
        """A deleted server's stored admission state, removed with it (best effort)."""
        ...

    def start(self, config_service: Any, holder: Any, *, at_ms: int) -> object | None:  # noqa: ANN401
        """Background work the query service starts once per process (kept on ``holder``, the app's state)."""
        ...

    async def run_pinning(self, org_id: str, config_service: ManifestConfig) -> McpRunPinning:
        """How the org's harness runs pin their MCP tools at admission and hold them at the claim."""
        ...


class OpenMcpToolAdmission:
    """No admin pre-approval of MCP tools, for any org: every listed tool the description scanner does not flag."""

    async def requires_admin_approval(self, org_id: str, config_service: ManifestConfig) -> bool:
        return False

    async def admit(
        self,
        config_service: ManifestConfig,
        infos: Sequence[MCPToolInfo],
        *,
        tenant: str,
        instance_id: str,
        at_ms: int,
        instance: Mapping[str, Any] | None = None,
        raw: Mapping[str, ToolDescriptor] | None = None,
    ) -> tuple[list[MCPToolInfo], ManifestReview]:
        from app.harness.mcp_hub.manifests import open_admission

        return open_admission(infos, raw)

    async def approved_manifest(self, config_service: ManifestConfig, instance_id: str) -> ApprovedManifest | None:
        return None

    async def approved_document(self, config_service: ManifestConfig, instance_id: str) -> dict[str, Any] | None:
        return None

    async def unapproved(self, config_service: ManifestConfig, instance_id: str, names: Iterable[str]) -> list[str]:
        return []

    async def standing(
        self, config_service: ManifestConfig, instance_id: str, infos: Sequence[MCPToolInfo]
    ) -> list[dict[str, Any]] | None:
        return None

    async def forget(self, config_service: Any, instance_id: str) -> None:  # noqa: ANN401
        return None

    def start(self, config_service: Any, holder: Any, *, at_ms: int) -> object | None:  # noqa: ANN401
        return None

    async def run_pinning(self, org_id: str, config_service: ManifestConfig) -> McpRunPinning:
        from app.harness.mcp_hub.run_pinning import LIVE_MCP_PINNING

        return LIVE_MCP_PINNING


OPEN_MCP_TOOL_ADMISSION = OpenMcpToolAdmission()


def mcp_tool_admission() -> McpToolAdmission:
    """The edition's policy, read at call time so an edition extension that sets it is honoured."""
    from app import edition_config

    policy = getattr(edition_config, "mcp_tool_admission", None)
    return policy if policy is not None else OPEN_MCP_TOOL_ADMISSION


async def requires_admin_approval(org_id: str, config_service: ManifestConfig) -> bool:
    return await mcp_tool_admission().requires_admin_approval(org_id, config_service)


__all__ = [
    "OPEN_MCP_TOOL_ADMISSION",
    "McpToolAdmission",
    "OpenMcpToolAdmission",
    "mcp_tool_admission",
    "requires_admin_approval",
]
