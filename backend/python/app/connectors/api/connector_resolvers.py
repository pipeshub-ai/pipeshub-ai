"""OSS connector edition resolvers — pass-through / single-tenant behavior.

EE counterparts live in ``app.ee.connectors.api.connector_resolvers``.
Shared router imports these via ``edition_config`` (comment-flip).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException, Request

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.api.middlewares.auth import is_request_admin

logger = logging.getLogger(__name__)

_CONNECTOR_SECRET_FIELDS = frozenset({"clientSecret", "client_secret", "clientsecret"})


def resolve_config_service(container: Any, org_id: Optional[str]) -> Any:
    """Return the container config service (no org wrapping on OSS)."""
    del org_id
    return container.config_service()


def build_graph_data_store(logger_: logging.Logger, graph_provider: Any, org_id: Optional[str]) -> GraphDataStore:
    """OSS GraphDataStore has no org_id constructor arg."""
    del org_id
    return GraphDataStore(logger_, graph_provider)


async def lookup_user_for_records(graph_provider: Any, user_id: str, org_id: Optional[str]) -> Any:
    """OSS: lookup by external userId only."""
    del org_id
    return await graph_provider.get_user_by_user_id(user_id=user_id)


def records_user_id_arg(user: dict[str, Any], external_user_id: str) -> str:
    """OSS get_records expects the graph ``_key``."""
    del external_user_id
    return user["_key"]


async def authorize_connector_stats(
    request: Request,
    graph_provider: Any,
    connector_registry: Any,
    connector_id: str,
    org_id: str,
) -> None:
    """OSS: KB role or can_user_view_connector (no is_connector_in_org)."""
    del org_id
    user_id = request.state.user.get("userId")
    is_admin = is_request_admin(request)

    app_doc = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if not app_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Connector instance {connector_id} not found",
        )

    if app_doc.get("type") == Connectors.KNOWLEDGE_BASE.value:
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail=f"User not found for user_id: {user_id}",
            )
        user_role = await graph_provider.get_user_kb_permission(connector_id, user.get("_key"))
        if user_role not in ("OWNER", "WRITER", "READER"):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Insufficient KB permissions for connector {connector_id}. "
                    "Required: OWNER, WRITER, or READER"
                ),
            )
        return

    can_view = await connector_registry.can_user_view_connector(
        connector_id, app_doc, user_id, is_admin=is_admin
    )
    if not can_view:
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient permissions to access stats for connector {connector_id}",
        )


async def assert_hard_delete_record_org(
    request: Request,
    graph_provider: Any,
    record_id: str,
) -> None:
    """OSS: no extra tenant gate on hard delete."""
    del request, graph_provider, record_id


def strip_redacted_fields(data: dict[str, Any]) -> dict[str, Any]:
    """OSS: no redaction — return a shallow copy."""
    return dict(data)


def mask_oauth_config_for_response(
    oauth_config: dict[str, Any],
    caller_org_id: str,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """OSS: admins get raw config; callers build essential fields themselves for non-admin."""
    del caller_org_id
    if is_admin:
        return {
            "config": dict(oauth_config.get("config") or {}),
            "inherited": False,
        }
    return {"config": {}, "inherited": False}


async def resolve_oauth_configs(
    container: Any,
    config_path: str,
    org_id: str,
    config_service: Any,
    *,
    scope: Optional[str] = None,
) -> list[dict[str, Any]]:
    """OSS: read configs from etcd via config_service (own org filtered by callers)."""
    del container, org_id, scope
    configs = await config_service.get_config(config_path, default=[])
    if not isinstance(configs, list):
        return []
    return configs


async def resolve_oauth_config(
    container: Any,
    config_path: str,
    org_id: str,
    config_id: str,
    config_service: Any,
) -> Optional[dict[str, Any]]:
    """OSS: find config by id + org in local etcd list."""
    configs = await resolve_oauth_configs(container, config_path, org_id, config_service)
    return next(
        (c for c in configs if c.get("_id") == config_id and c.get("orgId") == org_id),
        None,
    )


async def forbid_inherited_oauth_mutation(
    container: Any,
    config_path: str,
    org_id: str,
    config_id: str,
) -> None:
    """OSS: no inheritance — no-op (caller already 404'd local miss)."""
    del container, config_path, org_id, config_id


def default_connector_scope(
    connector_type: str,
    connector_registry: Any,
    body_scope: Optional[str],
) -> Optional[str]:
    """OSS: do not require connectorScope on OAuth create."""
    del connector_type, connector_registry
    scope = (body_scope or "").strip().lower()
    return scope or None


async def resolve_shared_oauth_config_for_flow(
    auth_config: dict[str, Any],
    oauth_config_id: str,
    org_id: str,
    oauth_config_path: str,
    config_service: Any,
    *,
    container: Any = None,
    logger_: Optional[logging.Logger] = None,
) -> Optional[dict[str, Any]]:
    """OSS: local etcd lookup only (own org)."""
    del container, logger_
    oauth_configs = await config_service.get_config(oauth_config_path, default=[])
    if not isinstance(oauth_configs, list):
        oauth_configs = []
    for oauth_cfg in oauth_configs:
        if oauth_cfg.get("_id") == oauth_config_id and oauth_cfg.get("orgId") == org_id:
            return oauth_cfg
    return None


def schedule_token_refresh_kwargs(org_id: Optional[str]) -> dict[str, Any]:
    """OSS schedule_token_refresh has no org_id kwarg."""
    del org_id
    return {}


def oauth_create_extra_fields(
    *,
    connector_scope: Optional[str],
    oauth_instance_name: str,
) -> dict[str, Any]:
    del connector_scope, oauth_instance_name
    return {}


def ensure_oauth_default(
    existing_configs: list[dict[str, Any]],
    new_config: dict[str, Any],
    org_id: str,
) -> None:
    """OSS: no isDefault management needed."""
    del existing_configs, new_config, org_id


def annotate_oauth_inheritance(
    auth_dict: dict[str, Any],
    shared_oauth_config: dict[str, Any],
    org_id: str,
) -> None:
    """OSS: no inheritance tracking needed."""
    del auth_dict, shared_oauth_config, org_id


def filter_oauth_configs_for_list(
    oauth_configs: list[dict[str, Any]],
    org_id: str,
    *,
    scope: Optional[str] = None,
) -> list[dict[str, Any]]:
    """OSS listing filters by org in the registry helper; return list as-is here."""
    del org_id, scope
    return oauth_configs
