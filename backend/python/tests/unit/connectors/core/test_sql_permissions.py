"""Who a SQL connector's database is granted to."""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.sql.permissions import database_permissions


def _connector(scope: str, lookup: AsyncMock, created_by: str | None = "user-1") -> SimpleNamespace:
    tx_store = SimpleNamespace(get_user_by_user_id=lookup)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=tx_store)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connector = SimpleNamespace(
        scope=scope, created_by=created_by, creator_email=None, connector_id="c1", logger=MagicMock(),
        data_store_provider=SimpleNamespace(transaction=MagicMock(return_value=transaction)),
    )
    # The real lookup the SQL connectors inherit.
    connector._load_creator_email = MethodType(BaseConnector._load_creator_email, connector)
    return connector


def _finds(email: str | None) -> AsyncMock:
    return AsyncMock(return_value={"email": email})


class TestDatabasePermissions:
    async def test_team_scope_reaches_the_organisation(self) -> None:
        connector = _connector("team", _finds("owner@example.com"))
        assert [p.entity_type.value for p in await database_permissions(connector)] == ["ORG"]
        connector.data_store_provider.transaction.assert_not_called()

    async def test_personal_scope_reaches_the_creator_only(self) -> None:
        lookup = _finds("owner@example.com")
        connector = _connector("personal", lookup)
        perms = await database_permissions(connector)
        assert [(p.entity_type.value, p.email, p.external_id) for p in perms] == [
            ("USER", "owner@example.com", "user-1"),
        ]
        # Without raise_on_error a provider swallows a failure and it reads as "user gone".
        lookup.assert_awaited_once_with("user-1", raise_on_error=True)
        assert connector.creator_email == "owner@example.com"

    async def test_a_failed_creator_lookup_keeps_the_stored_grants(self) -> None:
        connector = _connector("personal", AsyncMock(side_effect=RuntimeError("graph down")))
        assert await database_permissions(connector) is None
        connector.logger.warning.assert_called_once()
        assert connector.creator_email is None

    async def test_a_creator_who_is_gone_is_granted_nothing(self) -> None:
        assert await database_permissions(_connector("personal", AsyncMock(return_value=None))) == []
        assert await database_permissions(_connector("personal", _finds(None))) == []

    async def test_without_created_by_no_lookup_and_no_grant(self) -> None:
        connector = _connector("personal", _finds("owner@example.com"), created_by=None)
        assert await database_permissions(connector) == []
        connector.data_store_provider.transaction.assert_not_called()

    async def test_a_cached_creator_email_needs_no_lookup(self) -> None:
        connector = _connector("personal", _finds("other@example.com"))
        connector.creator_email = "owner@example.com"
        (permission,) = await database_permissions(connector)
        assert permission.email == "owner@example.com"
        connector.data_store_provider.transaction.assert_not_called()
