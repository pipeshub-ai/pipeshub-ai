"""Who a SQL connector's database is granted to."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.sql.permissions import database_permissions


def _connector(scope: str, email: str | None) -> SimpleNamespace:
    connector = SimpleNamespace(
        scope=scope, created_by="user-1", creator_email=None, connector_id="c1", logger=MagicMock(),
    )

    async def load() -> None:
        connector.creator_email = email

    connector._load_creator_email = AsyncMock(side_effect=load)
    return connector


class TestDatabasePermissions:
    async def test_team_scope_reaches_the_organisation(self) -> None:
        connector = _connector("team", "owner@example.com")
        assert [p.entity_type.value for p in await database_permissions(connector)] == ["ORG"]
        connector._load_creator_email.assert_not_awaited()

    async def test_personal_scope_reaches_the_creator_only(self) -> None:
        perms = await database_permissions(_connector("personal", "owner@example.com"))
        assert [(p.entity_type.value, p.email, p.external_id) for p in perms] == [
            ("USER", "owner@example.com", "user-1"),
        ]

    async def test_personal_scope_without_a_creator_email_reaches_no_one(self) -> None:
        assert await database_permissions(_connector("personal", None)) == []
