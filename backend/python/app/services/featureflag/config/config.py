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
