class CONFIG:
    """Feature flag configuration constants"""

    # Feature flags
    ENABLE_WORKFLOW_BUILDER = "ENABLE_WORKFLOW_BUILDER"
    ENABLE_BETA_CONNECTORS = "ENABLE_BETA_CONNECTORS"
    # Controls whether coding_sandbox.* tools are exposed to agents.
    # Defaults to enabled; admins can disable from Labs.
    ENABLE_CODE_EXECUTION = "ENABLE_CODE_EXECUTION"
    # EE-only: knowledge-graph governance routes (merge, suggestions,
    # ontology promote/deprecate). Defaults to disabled so CE deployments
    # get a 404 on these routes without a separate CE/EE build.
    ENABLE_KG_GOVERNANCE = "ENABLE_KG_GOVERNANCE"
