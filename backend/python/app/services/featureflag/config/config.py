class CONFIG:
    """Feature flag configuration constants"""

    # Feature flags
    ENABLE_WORKFLOW_BUILDER = "ENABLE_WORKFLOW_BUILDER"
    ENABLE_BETA_CONNECTORS = "ENABLE_BETA_CONNECTORS"
    # Controls whether coding_sandbox.* tools are exposed to agents.
    # Defaults to enabled; admins can disable from Labs.
    ENABLE_CODE_EXECUTION = "ENABLE_CODE_EXECUTION"
    # Global kill switch for prompt caching (see app.llm.prompt_cache).
    # Defaults to enabled; admins can disable from Labs. Layered on top of
    # the ENABLE_PROMPT_CACHING env var floor — see
    # app.llm.prompt_cache.config.resolve_cache_config's docstring.
    ENABLE_PROMPT_CACHING = "ENABLE_PROMPT_CACHING"
