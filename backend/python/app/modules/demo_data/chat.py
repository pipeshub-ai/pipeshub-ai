"""Keep switched-off demo data out of a chat or agent run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.demo_data.access import excluded_demo_connector_ids
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Read by tools that open records by id, which no scope filter covers.
EXCLUDED_APP_IDS_STATE_KEY = "excluded_app_ids"


async def demo_exclusions_for_run(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    user_info: dict[str, Any],
    log: logging.Logger,
) -> frozenset[str]:
    try:
        return await excluded_demo_connector_ids(
            graph_provider, config_service, user_info.get("orgId", ""), user_info.get("userId", "")
        )
    except Exception as exc:
        # Unreadable setting: run as before rather than fail the request.
        log.warning("demo data setting unreadable: %s", exc)
        return frozenset()


def exclude_from_query(query_info: dict[str, Any], excluded: frozenset[str]) -> dict[str, Any]:
    """Drop excluded apps from the run's knowledge and source filters.

    An explicit scope that loses its only sources must stay empty: without
    ``strictScope`` an empty scope means "search everything the user can
    access", which would widen an agent built on the demo alone.
    """
    if not excluded:
        return query_info
    knowledge = list(query_info.get("knowledge") or [])
    filters = dict(query_info.get("filters") or {})
    kept_knowledge = [k for k in knowledge if not (isinstance(k, dict) and k.get("connectorId") in excluded)]
    apps = list(filters.get("apps") or [])
    kept_apps = [a for a in apps if a not in excluded]
    if len(kept_knowledge) == len(knowledge) and len(kept_apps) == len(apps):
        return query_info
    if apps:
        filters["apps"] = kept_apps
    if not kept_knowledge and not kept_apps and not filters.get("kb"):
        filters[STRICT_SCOPE_FILTER_KEY] = True
    updated = {**query_info, "filters": filters}
    if knowledge:
        updated["knowledge"] = kept_knowledge
    return updated


def exclude_from_state(chat_state: dict[str, Any], excluded: frozenset[str]) -> None:
    """Record the exclusion for tools, and keep the demo out of the source catalog."""
    chat_state[EXCLUDED_APP_IDS_STATE_KEY] = excluded
    if not excluded:
        return
    connectors = chat_state.get("available_connectors")
    if isinstance(connectors, list):
        chat_state["available_connectors"] = [
            c for c in connectors if not (isinstance(c, dict) and c.get("id") in excluded)
        ]


def excluded_app_ids(state: dict[str, Any] | None) -> frozenset[str]:
    value = (state or {}).get(EXCLUDED_APP_IDS_STATE_KEY)
    return value if isinstance(value, frozenset) else frozenset(value or ())
