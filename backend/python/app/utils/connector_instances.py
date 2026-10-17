"""The single connector-instance lookup the chat and agent stream paths share.

`has_sql_connector_configured` / `has_slack_connector_configured` each issue
this same query. Both stream entry points need both answers, so calling them
separately cost two identical round trips per request on the pre-first-token
path. Fetch once here, then apply `connector_instances_have_sql` /
`connector_instances_have_slack` to the result.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["fetch_user_connector_instances"]


async def fetch_user_connector_instances(
    graph_provider: "IGraphDBProvider",
    user_id: str,
    org_id: str,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """Connector instances visible to this user, or `[]` on failure.

    Never raises: a lookup failure degrades to "no connectors configured",
    matching what the two `has_*_connector_configured` helpers did on error.
    """
    try:
        return await graph_provider.get_user_connector_instances(
            collection=CollectionNames.APPS.value,
            user_id=user_id,
            org_id=org_id,
            team_scope=ConnectorScope.TEAM.value,
            personal_scope=ConnectorScope.PERSONAL.value,
        ) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector instance lookup failed: %s", exc)
        return []
