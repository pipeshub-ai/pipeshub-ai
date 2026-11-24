ensure_org_context = None
oauth_apps_router = None
knowledge_hub_service_factory = None
sharing_router = None 
agent_sharing_router = None
allowed_connector_list_scopes: set[str] = {"personal", "team"}
# Organization-wide rules for toolset tools (the "Tool approvals" editor). Off: a toolset tool
# starts from what it does to data, and the person in the chat answers its approval card.
organization_toolset_tool_rules = False
from app.config.redaction import can_reveal_secrets, hide_secrets_by_default
from app.api.routes.search_llm_resolver import resolve_llm_for_search
from app.api.routes.toolset_resolvers import (
    REDACTED_PLACEHOLDER,
    check_user_is_admin,
    get_oauth_credentials_for_toolset,
    get_toolset_by_id,
    is_redacted_placeholder,
    load_instances_for_mutation,
    mask_oauth_secrets,
    resolve_inherited_from_org_id,
)
from app.api.routes.mcp_resolvers import (
    build_schedule_refresh_kwargs,
    forbid_inherited_mcp_mutation,
    get_mcp_instance as get_mcp_instance_resolved,
    load_mcp_instances,
    mask_mcp_instance_for_response,
    resolve_instance_owner_config_service,
    resolve_mcp_instances_with_inheritance,
)
from app.utils.oauth_config import fetch_oauth_config_by_id
from app.api.middlewares.auth import (
    authMiddleware,
    deny_service_tokens,
    extract_bearer_token,
    get_config_service,
    isJwtTokenValid,
    require_scopes,
    require_service_token,
)
# Resolvers must be bound before importing connector_router: the router module
# imports these symbols from edition_config (circular). Bind first so a
# mid-load re-entry finds them on this partially initialized module.
from app.connectors.api.connector_resolvers import (
    authorize_connector_stats,
    build_graph_data_store,
    default_connector_scope,
    annotate_oauth_inheritance,
    ensure_oauth_default,
    filter_oauth_configs_for_list,
    forbid_inherited_oauth_mutation,
    mask_oauth_config_for_response,
    oauth_create_extra_fields,
    resolve_config_service,
    resolve_oauth_config,
    resolve_oauth_configs,
    resolve_shared_oauth_config_for_flow,
    schedule_token_refresh_kwargs,
    strip_redacted_fields,
)
vector_store_rebuild_available = True
# Low-level service classes (token refresh, OAuth registry, EventService):

from app.edition_services import TokenRefreshService, ToolsetTokenRefreshService
from app.edition_services import get_data_entities_processor_cls


# Routers served by one service each. They are read on attribute access, so a service
# that never mounts one does not load its module (the connector service would otherwise
# load the whole chat and agent layer, and with it the parsers). Several also import this
# module at load, which a module-level import here would turn into a cycle.
# ``tests/unit/test_import_boundaries.py`` reads this table.
_LAZY_ATTRS: dict[str, str] = {
    "connector_router": "app.connectors.api.router",
    "search_router": "app.api.routes.search",
    "toolsets_router": "app.api.routes.toolsets",
    "ai_models_registry_router": "app.api.routes.ai_models_registry",
    "agent_router": "app.api.routes.agent",
    "chatbot_router": "app.api.routes.chatbot",
    "entity_router": "app.api.routes.entity",
}


def __getattr__(name: str):
    """Re-export the routers in ``_LAZY_ATTRS`` without importing them at module load.

    `app.connectors.api.router` imports the resolvers above from this module,
    so importing it here made the two modules mutually dependent. That held
    together only when `edition_config` was imported first: entering through
    the router instead — as `pytest tests/unit/connectors` does — reached this
    line while `router` was still partially initialized and raised ImportError.

    Deferring to attribute access removes the cycle at import time while
    keeping `from app.edition_config import connector_router` working, and the
    ordering the resolvers need is then satisfied for free: by the time anyone
    can read this attribute, the module is fully bound.
    """
    module = _LAZY_ATTRS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return importlib.import_module(module).router
