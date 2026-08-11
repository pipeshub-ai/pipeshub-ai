"""etcd path helpers and shared constants for MCP server registry/auth.

Layout (namespaced under /services/mcp, per-instance/org-scoped — avoids the
single-list read-modify-write race a single `/services/mcp-instances` list key
would create under concurrent admin writes):

  /services/mcp/instances/{orgId}/{instanceId}                    -> MCPServerConfig (no secrets)
  /services/mcp/credentials/{instanceId}/{userId}                 -> per-user/admin auth record
  /services/mcp/credentials/{instanceId}/{userId}/oauth-tokens    -> OAuthTokens
  /services/mcp/credentials/{instanceId}/{userId}/dcr-client      -> legacy per-owner DCRClient
  /services/mcp/dcr-clients/{instanceId}                          -> shared per-instance DCRClient
  /services/mcp/oauth-clients/{instanceId}                        -> admin shared OAuth app creds
  /services/mcp/oauth-states/{state}                              -> CSRF state (state-keyed, O(1) callback lookup)

DCR clients used to be registered per-owner (one OAuth app per user per instance at the
provider) — kept above as `get_mcp_dcr_client_path` and still read first everywhere so
already-issued tokens keep refreshing against the exact client they were minted against.
New registrations go to the shared per-instance path (`get_mcp_shared_dcr_client_path`)
instead, so an org doesn't register one client per user at the provider for a single
instance.
"""

MCP_ROOT = "/services/mcp"

OAUTH_STATE_TTL_SECONDS = 600  # 10 minutes; ConfigurationService has no native etcd TTL, so states carry an embedded expiresAt


def normalize_mcp_type(type_id: str) -> str:
    """Normalize a catalog type id for use as a lookup/storage key."""
    return type_id.lower().strip().replace(" ", "_").replace("-", "_")


def get_mcp_instances_prefix(org_id: str) -> str:
    """Prefix to list every instance for an org: /services/mcp/instances/{orgId}/"""
    return f"{MCP_ROOT}/instances/{org_id}/"


def get_mcp_instance_path(org_id: str, instance_id: str) -> str:
    """Single instance metadata key: /services/mcp/instances/{orgId}/{instanceId}"""
    return f"{MCP_ROOT}/instances/{org_id}/{instance_id}"


def get_mcp_credentials_path(instance_id: str, user_id: str) -> str:
    """Per-user/admin auth record for an instance: /services/mcp/credentials/{instanceId}/{userId}"""
    return f"{MCP_ROOT}/credentials/{instance_id}/{user_id}"


def get_mcp_instance_credentials_prefix(instance_id: str) -> str:
    """Prefix to list every user's auth record for an instance (used on full instance delete)."""
    return f"{MCP_ROOT}/credentials/{instance_id}/"


def get_mcp_oauth_tokens_path(instance_id: str, user_id: str) -> str:
    """OAuth token set: /services/mcp/credentials/{instanceId}/{userId}/oauth-tokens"""
    return f"{get_mcp_credentials_path(instance_id, user_id)}/oauth-tokens"


def get_mcp_dcr_client_path(instance_id: str, user_id: str) -> str:
    """Legacy per-owner DCR-registered OAuth client: /services/mcp/credentials/{instanceId}/{userId}/dcr-client

    Superseded by `get_mcp_shared_dcr_client_path` for new registrations — kept for owners
    who registered before the shared path existed.
    """
    return f"{get_mcp_credentials_path(instance_id, user_id)}/dcr-client"


def get_mcp_shared_dcr_client_path(instance_id: str) -> str:
    """DCR-registered OAuth client shared across every user/agent authenticating against this
    instance: /services/mcp/dcr-clients/{instanceId}

    One registration per instance instead of one per user — see the module docstring for the
    legacy-vs-shared read order every resolution path follows.
    """
    return f"{MCP_ROOT}/dcr-clients/{instance_id}"


def get_mcp_oauth_client_config_path(instance_id: str) -> str:
    """Admin-configured shared OAuth app credentials: /services/mcp/oauth-clients/{instanceId}"""
    return f"{MCP_ROOT}/oauth-clients/{instance_id}"


def get_mcp_oauth_state_path(state: str) -> str:
    """CSRF state, keyed by the state value itself for O(1) callback lookup."""
    return f"{MCP_ROOT}/oauth-states/{state}"


def get_mcp_oauth_states_prefix() -> str:
    """Prefix to sweep all pending OAuth states (expired-state cleanup pass)."""
    return f"{MCP_ROOT}/oauth-states/"
