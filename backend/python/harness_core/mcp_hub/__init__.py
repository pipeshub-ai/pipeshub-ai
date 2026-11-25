"""MCP hub (client side): schema cache, pooled sessions, manifest approval and tool names. Product-neutral."""

from harness_core.mcp_hub.description_scan import DescriptionFinding, scan_descriptor
from harness_core.mcp_hub.errors import (
    ManifestStale,
    ManifestUnavailable,
    MCPHubRefused,
    SessionOwnerMismatch,
    ToolChanged,
    ToolDescriptionFlagged,
    ToolNameCollision,
    ToolNameInvalid,
    ToolUnapproved,
)
from harness_core.mcp_hub.manifest import (
    ApprovedManifest,
    DisabledTool,
    ManifestReview,
    ToolDescriptor,
    review_manifest,
)
from harness_core.mcp_hub.memory import InMemoryToolSchemaCache
from harness_core.mcp_hub.names import (
    check_spec_name,
    export_description,
    export_name,
    export_names,
    export_namespace,
)
from harness_core.mcp_hub.pool import SessionLease, SessionPool
from harness_core.mcp_hub.ports import ToolSchemaCache
from harness_core.mcp_hub.types import (
    CachedTools,
    PoolConfig,
    PooledSessionInfo,
    SchemaKey,
    SessionKey,
    list_ttl_s,
)

__all__ = [
    "ApprovedManifest",
    "CachedTools",
    "DescriptionFinding",
    "DisabledTool",
    "InMemoryToolSchemaCache",
    "SessionLease",
    "MCPHubRefused",
    "ManifestReview",
    "ManifestStale",
    "ManifestUnavailable",
    "PoolConfig",
    "PooledSessionInfo",
    "SchemaKey",
    "SessionKey",
    "SessionOwnerMismatch",
    "SessionPool",
    "ToolChanged",
    "ToolDescriptionFlagged",
    "ToolDescriptor",
    "ToolNameCollision",
    "ToolNameInvalid",
    "ToolSchemaCache",
    "ToolUnapproved",
    "check_spec_name",
    "export_description",
    "export_name",
    "export_names",
    "export_namespace",
    "list_ttl_s",
    "review_manifest",
    "scan_descriptor",
]
