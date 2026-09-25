class CONFIG:
    """Feature flag configuration constants"""

    # Feature flags
    ENABLE_WORKFLOW_BUILDER = "ENABLE_WORKFLOW_BUILDER"
    ENABLE_BETA_CONNECTORS = "ENABLE_BETA_CONNECTORS"
    # Controls whether agents can load/use MCP (Model Context Protocol) servers
    # and whether MCP is shown anywhere in the UI.
    # Defaults to disabled; admins must opt in from Labs.
    ENABLE_MCP = "ENABLE_MCP"
    # Controls whether agents can load/use toolset actions (connector
    # integrations) and whether Actions is shown anywhere in the UI.
    # Defaults to enabled — unlike ENABLE_MCP, this is pre-existing
    # functionality; admins may opt out from Labs.
    ENABLE_ACTIONS = "ENABLE_ACTIONS"
    # Controls whether the admin vector-store cleanup (delete all embeddings)
    # and reindex operations are available. Defaults to disabled;
    # admins opt in from Labs.
    ENABLE_VECTOR_STORE_REBUILD = "ENABLE_VECTOR_STORE_REBUILD"
    # Controls whether users can create/import/manage Skills (SKILL.md
    # packages) and whether agents can load skill tools. Defaults to
    # enabled (Beta) — like ENABLE_ACTIONS, admins may opt out from Labs.
    ENABLE_SKILLS = "ENABLE_SKILLS"
    # Scopes a search by the containers a user can reach (connector, record
    # group, root record group) instead of sending every accessible record id
    # to the vector DB. Defaults to enabled; admins may opt out from Labs to
    # fall back to the record-id path.
    ENABLE_CONTAINER_PERMISSION_FILTER = "ENABLE_CONTAINER_PERMISSION_FILTER"
    # Controls whether the default assistant / Universal Agent Mode injects
    # the current user's name, email, and organization into the system prompt.
    # Defaults to enabled; admins may opt out from Labs. Agent Builder agents
    # use their own per-agent `sendUserContext` field instead of this flag.
    ENABLE_USER_CONTEXT = "ENABLE_USER_CONTEXT"
