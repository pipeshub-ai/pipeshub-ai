class CONFIG:
    """Feature flag configuration constants"""

    # Feature flags
    ENABLE_WORKFLOW_BUILDER = "ENABLE_WORKFLOW_BUILDER"
    ENABLE_BETA_CONNECTORS = "ENABLE_BETA_CONNECTORS"
    # Controls whether agents can load/use MCP (Model Context Protocol) servers
    # and whether MCP is shown anywhere in the UI.
    # Defaults to disabled; admins must opt in from Labs.
    ENABLE_MCP = "ENABLE_MCP"
