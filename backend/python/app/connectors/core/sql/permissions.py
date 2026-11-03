"""Who a SQL connector's database is granted to."""

from typing import Any

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.permission import EntityType, Permission, PermissionType


async def database_permissions(connector: Any) -> list[Permission]:  # noqa: ANN401
    """The grants on the database record group, which every schema, table and
    column inherits.

    Team scope: the whole organisation. Personal scope: the creator only;
    with no creator to name, nobody rather than everybody.
    """
    if connector.scope == ConnectorScope.TEAM.value:
        return [Permission(type=PermissionType.OWNER, entity_type=EntityType.ORG)]
    if not connector.creator_email:
        await connector._load_creator_email()
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
