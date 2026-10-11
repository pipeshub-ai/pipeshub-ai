"""etcd path helpers and shared constants for MCP server registry/auth.

Layout (namespaced under /services/mcp, per-instance/org-scoped — avoids the
single-list read-modify-write race a single `/services/mcp-instances` list key
would create under concurrent admin writes):

  /services/mcp/instances/{instanceId}                            -> org-wide MCPServerConfig (no secrets)
  /services/mcp/user-instances/{orgId}/{userId}/{instanceId}      -> a user's personal instance
  /services/mcp/credentials/{instanceId}/{userId}                 -> per-user/admin auth record
  /services/mcp/credentials/{instanceId}/{userId}/oauth-tokens    -> OAuthTokens
  /services/mcp/credentials/{instanceId}/{userId}/dcr-client      -> legacy per-owner DCRClient
  /services/mcp/dcr-clients/{instanceId}                          -> shared per-instance DCRClient
  /services/mcp/oauth-clients/{instanceId}                        -> admin shared OAuth app creds
  /services/mcp/oauth-states/{sha256(state)}                      -> CSRF state (state-keyed, O(1) callback lookup)
  /services/mcp/oauth-state-claims/{sha256(state)}                -> single-use claim of a state (callback)
  /services/mcp/refresh-locks/{instanceId}/{ownerId}              -> cluster-wide token refresh lock
  /services/mcp/tool-catalogs/pointers/{instanceId}/{ownerKey}    -> which catalog a sign-in's discovery gave
  /services/mcp/tool-catalogs/blobs/{sha256}                      -> a tool catalog, stored once (`tool_cache`)
  /services/mcp/tool-policies/{instanceId}                        -> company rules per tool (admin; `tool_approvals`)
  /services/mcp/agent-tool-rules/{agentKey}/{instanceId}          -> an agent's rules per tool (its editors)
  /services/mcp/user-tool-rules/{userId}/{instanceId}             -> a person's own rules (their assistant chats)
  /services/mcp/tool-approvals/chat-grants/{conversationId}/{instanceId} -> "Allow for this chat"
  /services/mcp/tool-approvals/pending/{approvalId}               -> a call waiting for approval (15 min)
  /services/mcp/tool-approvals/claims/{approvalId}                -> single-use claim of an approval

DCR clients used to be registered per-owner (one OAuth app per user per instance at the
provider) — kept above as `get_mcp_dcr_client_path` and still read first everywhere so
already-issued tokens keep refreshing against the exact client they were minted against.
New registrations go to the shared per-instance path (`get_mcp_shared_dcr_client_path`)
instead, so an org doesn't register one client per user at the provider for a single
instance.
"""

import hashlib

MCP_ROOT = "/services/mcp"

OAUTH_STATE_TTL_SECONDS = 600  # 10 minutes; ConfigurationService has no native etcd TTL, so states carry an embedded expiresAt


def normalize_mcp_type(type_id: str) -> str:
    """Normalize a catalog type id for use as a lookup/storage key."""
    return type_id.lower().strip().replace(" ", "_").replace("-", "_")


def get_mcp_instances_prefix() -> str:
    """Prefix to list every instance for the current org scope: /services/mcp/instances/"""
    return f"{MCP_ROOT}/instances/"


def get_mcp_instance_path(instance_id: str) -> str:
    """Single instance metadata key: /services/mcp/instances/{instanceId}"""
    return f"{MCP_ROOT}/instances/{instance_id}"


def get_mcp_org_user_instances_prefix(org_id: str) -> str:
    """Every personal instance in one org: /services/mcp/user-instances/{orgId}/ (admin review only)."""
    return f"{MCP_ROOT}/user-instances/{org_id}/"


def get_mcp_user_instances_prefix(org_id: str, user_id: str) -> str:
    """One user's personal instances: /services/mcp/user-instances/{orgId}/{userId}/"""
    return f"{MCP_ROOT}/user-instances/{org_id}/{user_id}/"


def get_mcp_user_instance_path(org_id: str, user_id: str, instance_id: str) -> str:
    """A personal instance lives under its org and owner, so it only ever resolves for them."""
    return f"{MCP_ROOT}/user-instances/{org_id}/{user_id}/{instance_id}"


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


def get_mcp_step_up_scopes_path(instance_id: str, owner_id: str) -> str:
    """Scopes a 403 asked for, which the owner's next sign-in requests (`app.agents.mcp.step_up`).
    Under the credentials, so it goes with them."""
    return f"{get_mcp_credentials_path(instance_id, owner_id)}/step-up-scopes"


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


def _state_key(state: str) -> str:
    # Store keys are logged and listed; the state's hash finds the record without exposing it.
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def get_mcp_oauth_state_path(state: str) -> str:
    """CSRF state, keyed by a hash of the state value for O(1) callback lookup."""
    return f"{MCP_ROOT}/oauth-states/{_state_key(state)}"


def get_mcp_unhashed_oauth_state_path(state: str) -> str:
    """Where states were stored before they were hashed. Read by the callback so sign-ins
    started on an older server during a rolling deploy still complete; they expire in
    `OAUTH_STATE_TTL_SECONDS`, so this can go in the release after."""
    return f"{MCP_ROOT}/oauth-states/{state}"


def get_mcp_oauth_states_prefix() -> str:
    """Prefix to sweep all pending OAuth states (expired-state cleanup pass)."""
    return f"{MCP_ROOT}/oauth-states/"


def get_mcp_oauth_state_claim_path(state: str) -> str:
    """Created once, atomically, by the callback that gets to use `state`."""
    return f"{MCP_ROOT}/oauth-state-claims/{_state_key(state)}"


def get_mcp_unhashed_oauth_state_claim_path(state: str) -> str:
    """The claim of a state an older server stored unhashed, where that server claims it. Goes
    with `get_mcp_unhashed_oauth_state_path`."""
    return f"{MCP_ROOT}/oauth-state-claims/{state}"


def get_mcp_refresh_lock_path(instance_id: str, owner_id: str) -> str:
    """Held while one process refreshes the tokens at `get_mcp_credentials_path(...)`."""
    return f"{MCP_ROOT}/refresh-locks/{instance_id}/{owner_id}"


def get_mcp_tool_catalog_pointer_path(instance_id: str, owner_key: str) -> str:
    """Which cached tool catalog `owner_key`'s own discovery of the instance produced."""
    return f"{MCP_ROOT}/tool-catalogs/pointers/{instance_id}/{owner_key}"


def get_mcp_tool_catalog_blob_path(digest: str) -> str:
    """A tool catalog by its content hash: identical catalogs are stored once."""
    return f"{MCP_ROOT}/tool-catalogs/blobs/{digest}"


def get_mcp_tool_policy_path(instance_id: str) -> str:
    """The company's rules for an instance's tools."""
    return f"{MCP_ROOT}/tool-policies/{instance_id}"


def get_mcp_agent_tool_rules_path(agent_key: str, instance_id: str) -> str:
    return f"{MCP_ROOT}/agent-tool-rules/{agent_key}/{instance_id}"


def get_mcp_user_tool_rules_path(user_id: str, instance_id: str) -> str:
    return f"{MCP_ROOT}/user-tool-rules/{user_id}/{instance_id}"


def get_mcp_tool_chat_grants_path(conversation_id: str, instance_id: str) -> str:
    return f"{MCP_ROOT}/tool-approvals/chat-grants/{conversation_id}/{instance_id}"


def get_mcp_tool_approval_path(approval_id: str) -> str:
    return f"{MCP_ROOT}/tool-approvals/pending/{approval_id}"


def get_mcp_tool_approval_claim_path(approval_id: str) -> str:
    return f"{MCP_ROOT}/tool-approvals/claims/{approval_id}"
