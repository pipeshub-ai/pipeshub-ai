"""Which addresses a connector may dial, by the scope it was created with."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def public_only_for_scope(scope: object) -> bool:
    """Whether a connector of this scope may reach public addresses only.

    A personal connector can be created by any user, so it never reaches private-network
    addresses. A team connector is created by an admin and follows the deployment setting
    (``PIPESHUB_BLOCK_PRIVATE_ADDRESSES``). Anything else, including a missing scope,
    gets the stricter rule.
    """
    return str(getattr(scope, "value", scope)) != ConnectorScope.TEAM.value


async def connector_public_only(graph_provider: IGraphDBProvider | None, connector_id: str) -> bool:
    """Whether a saved connector may reach public addresses only, read from its app record.

    For code that reaches a connector's source outside the connector itself (background
    jobs, the query tool). No graph to ask, or no record, gets the stricter rule.
    """
    if graph_provider is None:
        return True
    app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    return public_only_for_scope((app or {}).get("scope"))
