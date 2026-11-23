"""Notion per-user mode: reading who may see a page from Notion.

In per-user mode a page or data source reaches a user only when their own Notion credential lists it
(``app.connectors.sources.notion.per_user``). The grant that listing gives is access read from the
source, so it is here. The free connector keeps walking each user's view to sync content and calls
``_with_user_grants``; without a licence key a company-wide connector turns no listing into a grant
(the processor's licence gate).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.sources.notion.per_user import GRANTED_TYPES
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from app.connectors.core.base.user_access import UserConnection
    from app.models.entities import Record


def user_grant(connection: UserConnection) -> Permission:
    return Permission(email=connection.email, type=PermissionType.READ, entity_type=EntityType.USER)


def with_user_grant(
    record: Record, permissions: list[Permission], connection: UserConnection
) -> tuple[Record, list[Permission]]:
    """A page or data source a user's own listing returned: their grant, and no inheritance.

    Notion lets a sub-page be narrower than its parent, so a page that inherited would reach
    everyone who can open the parent. A placeholder is never visible and is left as it is.
    """
    if record.record_type in GRANTED_TYPES and not record.is_placeholder:
        record.inherit_permissions = False
        # Other users' grants come from their own passes; the grants a user lost are dropped by their pass.
        record.adds_permissions = True
        return record, [user_grant(connection)]
    return record, permissions


class NotionPerUserPermissionsMixin:
    """The permission-reading half of ``NotionConnector``'s per-user mode."""

    async def _with_user_grants(
        self, rows: list[tuple[Record, list[Permission]]], connection: UserConnection | None
    ) -> list[tuple[Record, list[Permission]]]:
        """The rows a user's own listing returned, each with that user's grant (``with_user_grant``). Unchanged
        outside a user's pass, and without a licence key: the processor then keeps every stored grant and inherit
        edge whatever it is handed (``skips_permission_writes``), so the records are written as content only."""
        if connection is None or await self.data_entities_processor.skips_permission_writes(self.connector_id):
            return rows
        return [with_user_grant(record, permissions, connection) for record, permissions in rows]
