"""APP_LEVEL permission stubs. RECORD_LEVEL ACL mapping is a later change here."""

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
    return [
        Permission(
            type=PermissionType.READ,
            entity_type=EntityType.ORG,
            external_id=org_id,
        )
    ]
