"""Instance-scoped tool identity: tool refs, the names models see, type-keyed references, the migration plan. Product-neutral."""

from harness_core.tool_identity.errors import (
    ToolIdentityRefused,
    ToolInstanceAmbiguous,
    ToolInstanceMissing,
    ToolRefInvalid,
)
from harness_core.tool_identity.legacy import (
    BoundInstance,
    ToolBinding,
    granted_ref,
    match_grant,
)
from harness_core.tool_identity.migration import (
    DefinitionBinding,
    Drifted,
    InstanceCandidate,
    OwnerBindings,
    Rekey,
    RekeyPlan,
    RevertPlan,
    Unresolved,
    plan_rekeys,
    plan_reverts,
)
from harness_core.tool_identity.names import (
    LEGACY_INSTANCE,
    SCOPED_NAME_MAX,
    ToolRef,
    connector_tool_name,
    instance_tag,
    scoped_group,
    scoped_mcp_tool_name,
    scoped_tool,
    shared_types,
    type_key,
)

__all__ = [
    "LEGACY_INSTANCE",
    "SCOPED_NAME_MAX",
    "BoundInstance",
    "DefinitionBinding",
    "Drifted",
    "InstanceCandidate",
    "OwnerBindings",
    "Rekey",
    "RekeyPlan",
    "RevertPlan",
    "ToolIdentityRefused",
    "ToolInstanceAmbiguous",
    "ToolInstanceMissing",
    "ToolBinding",
    "ToolRef",
    "ToolRefInvalid",
    "Unresolved",
    "connector_tool_name",
    "instance_tag",
    "granted_ref",
    "match_grant",
    "plan_rekeys",
    "plan_reverts",
    "scoped_group",
    "scoped_mcp_tool_name",
    "scoped_tool",
    "shared_types",
    "type_key",
]
