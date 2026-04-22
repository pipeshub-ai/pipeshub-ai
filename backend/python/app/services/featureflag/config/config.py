class CONFIG:
    """Feature flag configuration constants"""

    # Feature flags
    ENABLE_WORKFLOW_BUILDER = "ENABLE_WORKFLOW_BUILDER"
    ENABLE_BETA_CONNECTORS = "ENABLE_BETA_CONNECTORS"
    # Controls whether coding_sandbox.* and database_sandbox.* tools are
    # exposed to agents. Defaults to enabled; admins can disable from Labs.
    ENABLE_CODE_EXECUTION = "ENABLE_CODE_EXECUTION"
