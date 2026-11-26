"""APP_LEVEL permission stubs. RECORD_LEVEL ACL mapping is a later change here."""

from typing import Any

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.permission import EntityType, Permission, PermissionType


def app_level_permissions(
    *,
    scope: str,
    org_id: str,
    creator_email: str | None,
    created_by: str,
) -> list[Permission]:
    if scope == ConnectorScope.TEAM.value:
        return [
            Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=org_id,
            )
        ]
    if creator_email:
        return [
            Permission(
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
                email=creator_email,
                external_id=created_by,
            )
        ]
    # A personal connector with no creator to name is shared with nobody
    # rather than everybody.
    return []


async def share_permissions(connector: Any) -> list[Permission] | None:  # noqa: ANN401
    """The grants on a share's record group. Team scope: the whole organisation.
    Personal scope: the creator only; with no creator to name, nobody. None when
    a personal connector could not look up its creator: the stored grants stay
    as they are.
    """
    if connector.scope != ConnectorScope.TEAM.value and not connector.creator_email:
        try:
            await connector._load_creator_email(raise_on_error=True)
        except Exception as e:
            connector.logger.warning(
                "Personal connector %s could not look up its creator; its share grants are kept: %s",
                connector.connector_id, type(e).__name__,
            )
            return None
        if not connector.creator_email:
            connector.logger.warning(
                "Personal connector %s has no creator email; its shares are shared with no one",
                connector.connector_id,
            )
    return app_level_permissions(
        scope=connector.scope,
        org_id=connector.data_entities_processor.org_id,
        creator_email=connector.creator_email,
        created_by=connector.created_by,
    )
