"""Resolve the authenticated DataSource for a ``connector_id`` so the
filter-search tools can make native API calls (JQL, CQL, Slack operators).

The filtered-search tools need a *DataSource* (``JiraDataSource``,
``ConfluenceDataSource``, ``SlackDataSource``) — not the toolset's raw
``Client`` (``JiraClient``, …) — because adapter ``execute`` methods call
domain methods that only live on DataSources.

The toolset system (``_client_cache``) is per-user: each user must
configure their own OAuth/API-key for Jira, Confluence, etc. But
connectors are org-level: the org's connector already has authenticated
credentials that indexed all the data. Filtered search should use the
*connector's* credentials — they are always available when the connector
is active, and PipesHub's ``FilteredRetrievalBridge`` already
permission-gates the results.

``resolve_client_for_connector`` therefore calls
``adapter.build_datasource(config_service, connector_id)`` which uses
``Client.build_from_services`` (the same path connectors themselves use
at indexing time) and wraps the result in the proper DataSource. Built
DataSources are cached per ``connector_id`` for the duration of the
request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_DATASOURCE_CACHE_KEY = "_filter_datasource_cache"


async def get_user_key(graph_provider: Any, user_id: str) -> str | None:  # noqa: ANN401
    try:
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        return (user.get("_key") or user.get("id")) if user else None
    except Exception:
        logger.warning("Failed to resolve user key for %s", user_id)
        return None


async def resolve_connector_type(state: ChatState, connector_id: str) -> str | None:
    """Return the graph `type` string (e.g. `"JIRA"`, `"CONFLUENCE DATA CENTER"`)
    for *connector_id*, or `None` if it's unknown to this request's catalog."""
    from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog

    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return None
    org_id = state.get("org_id", "")
    user_id = state.get("user_id", "")
    user_key = await get_user_key(graph_provider, user_id)
    if not user_key:
        return None
    catalog = await ConnectorCatalog.build(state, graph_provider=graph_provider, user_key=user_key, org_id=org_id)
    connector = next((c for c in catalog.connectors if c.id == connector_id), None)
    return connector.type if connector else None


async def resolve_self_identity(state: ChatState, connector_id: str) -> str | None:
    """Return the asking session user's own `source_user_id` (Jira accountId,
    Slack member id, ...) on *connector_id*, or `None` if they have no
    resolvable identity there.

    Composed entirely from graph methods every `IGraphDBProvider`
    implementation (Arango, Neo4j) already exposes — no new interface
    method needed. This is the one deterministic way native queries'
    `currentUser()` / `me` self-reference gets substituted with the real
    asking user, never the connector's service-account identity (see
    `agent_loop/hooks/filter_value_resolution.py` for why that distinction
    matters on team-scope connectors).
    """
    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return None
    user_id = state.get("user_id", "")
    try:
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
    except Exception:
        logger.warning("resolve_self_identity: failed to look up session user %s", user_id)
        return None
    email = (user or {}).get("email")
    if not email:
        return None
    try:
        app_user = await graph_provider.get_app_user_by_email(email=email, connector_id=connector_id)
    except Exception:
        logger.warning("resolve_self_identity: failed to look up app user for connector %s", connector_id)
        return None
    return app_user.source_user_id if app_user and app_user.source_user_id else None


async def resolve_client_for_connector(
    state: ChatState, connector_id: str,
) -> tuple[str, Any] | None:
    """Return ``(connector_type, datasource)`` for *connector_id*, or
    ``None`` if the connector is unknown or cannot be authenticated.

    The datasource is built from the connector's org-level credentials
    (``/services/connectors/{id}/config``) via the adapter's
    ``build_datasource`` classmethod — the same ``Client.build_from_services``
    path connectors themselves use at index time. This means filtered search
    works for every active connector without requiring the user to also
    configure a personal toolset.
    """
    from app.agents.actions.filtered_search.registry import FilterAdapterRegistry

    connector_type = await resolve_connector_type(state, connector_id)
    if connector_type is None:
        return None

    adapter_cls = FilterAdapterRegistry.get(connector_type)
    if adapter_cls is None:
        return None

    ds_cache: dict[str, tuple[str, Any]] = state.setdefault(_DATASOURCE_CACHE_KEY, {})
    cached = ds_cache.get(connector_id)
    if cached is not None:
        return cached

    config_service = state.get("config_service")
    if not config_service:
        logger.warning("resolve_client_for_connector: config_service not available in state")
        return None

    try:
        datasource = await adapter_cls.build_datasource(
            config_service, connector_id, logger,
        )
    except Exception:
        logger.exception(
            "resolve_client_for_connector: failed to build datasource for connector %s (%s)",
            connector_id, connector_type,
        )
        return None

    result = (connector_type, datasource)
    ds_cache[connector_id] = result
    return result


__all__ = ["get_user_key", "resolve_connector_type", "resolve_client_for_connector", "resolve_self_identity"]
