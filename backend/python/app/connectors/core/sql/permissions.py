"""Who a SQL connector's database is granted to."""

from typing import Any

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.permission import EntityType, Permission, PermissionType


async def database_permissions(connector: Any) -> list[Permission] | None:  # noqa: ANN401
    """The grants on the database record group, which every schema, table and
    column inherits.

    Team scope: the whole organisation. Personal scope: the creator only;
    with no creator to name, nobody rather than everybody. None when the
    creator could not be looked up: the stored grants stay as they are.
    """
    if connector.scope == ConnectorScope.TEAM.value:
        return [Permission(type=PermissionType.OWNER, entity_type=EntityType.ORG)]
    if not connector.creator_email and connector.created_by:
        # Not BaseConnector._load_creator_email: it swallows a failed lookup,
        # which would read as a creator who is gone and remove their grant.
        try:
            async with connector.data_store_provider.transaction() as tx_store:
                user = await tx_store.get_user_by_user_id(connector.created_by, raise_on_error=True)
        except Exception as e:
            connector.logger.warning(
                "Personal connector %s could not look up its creator; its database grants are kept: %s",
                connector.connector_id, type(e).__name__,
            )
            return None
        if user and user.get("email"):
            connector.creator_email = user["email"]
    if connector.creator_email:
        return [Permission(
            type=PermissionType.OWNER,
            entity_type=EntityType.USER,
            email=connector.creator_email,
            external_id=connector.created_by,
        )]
    connector.logger.warning(
        "Personal connector %s has no creator email; its database is shared with no one",
        connector.connector_id,
    )
    return []
