ensure_org_context = None
oauth_apps_router = None
knowledge_hub_service_factory = None
sharing_router = None 
agent_sharing_router = None
allowed_connector_list_scopes: set[str] = {"personal", "team"}
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
    build_mcp_fallback_config_services,
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
    extract_bearer_token,
    get_config_service,
    isJwtTokenValid,
    require_scopes,
)
from app.api.routes.agent import router as agent_router
from app.api.routes.chatbot import router as chatbot_router
from app.api.routes.entity import router as entity_router
from app.api.routes.search import router as search_router
from app.api.routes.toolsets import router as toolsets_router
# Resolvers must be bound before importing connector_router: the router module
# imports these symbols from edition_config (circular). Bind first so a
# mid-load re-entry finds them on this partially initialized module.
from app.connectors.api.connector_resolvers import (
    assert_hard_delete_record_org,
    authorize_connector_stats,
    build_graph_data_store,
    default_connector_scope,
    annotate_oauth_inheritance,
    ensure_oauth_default,
    filter_oauth_configs_for_list,
    forbid_inherited_oauth_mutation,
    lookup_user_for_records,
    mask_oauth_config_for_response,
    oauth_create_extra_fields,
    records_user_id_arg,
    resolve_config_service,
    resolve_oauth_config,
    resolve_oauth_configs,
    resolve_shared_oauth_config_for_flow,
    resolve_stats_org_id,
    schedule_token_refresh_kwargs,
    strip_redacted_fields,
)
from app.connectors.api.router import router as connector_router
# Low-level service classes (token refresh, OAuth registry, EventService):

from app.edition_services import TokenRefreshService, ToolsetTokenRefreshService
from app.edition_services import get_data_entities_processor_cls
