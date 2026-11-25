"""Typed refusals of the MCP hub; each ``reason_code`` names one cause (C-6)."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class MCPHubRefused(HarnessRefusal):
    reason_code: str = "mcp_hub_refused"


class ToolNameInvalid(MCPHubRefused):
    """The name breaks the MCP tool-name rule (2025-11-25: 1-128 of ``A-Za-z0-9_.-``)."""

    reason_code = "mcp_tool_name_invalid"


class ToolNameCollision(MCPHubRefused):
    """Two of a server's tools would reach the model under one name."""

    reason_code = "mcp_tool_name_collision"


class ToolUnapproved(MCPHubRefused):
    """The tool is not in the server's approved manifest."""

    reason_code = "mcp_tool_unapproved"


class ToolChanged(MCPHubRefused):
    """The tool's name, description or schema differs from what was approved (rug-pull guard)."""

    reason_code = "mcp_tool_changed"


class ToolDescriptionFlagged(MCPHubRefused):
    """The description scanner flagged the tool (MX-04) and the approval did not acknowledge its findings."""

    reason_code = "mcp_tool_description_flagged"


class ManifestStale(MCPHubRefused):
    """An approval names a digest the server no longer lists."""

    reason_code = "mcp_manifest_stale"


class ManifestUnavailable(MCPHubRefused):
    """The approved manifest could not be read; nothing is admitted without it."""

    reason_code = "mcp_manifest_unavailable"


class SessionOwnerMismatch(MCPHubRefused):
    """A pooled session was about to serve a request of another credential owner."""

    reason_code = "mcp_session_owner_mismatch"


__all__ = [
    "MCPHubRefused",
    "ManifestStale",
    "ManifestUnavailable",
    "SessionOwnerMismatch",
    "ToolChanged",
    "ToolDescriptionFlagged",
    "ToolNameCollision",
    "ToolNameInvalid",
    "ToolUnapproved",
]
