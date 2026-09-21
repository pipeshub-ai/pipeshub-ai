"""Linking the user who authenticated a connector to its source account (processor + BaseConnector hook)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import User

CONNECTOR = "jira-1"
CREATED_BY = "creator-user-id"


def _processor(tx_store) -> DataSourceEntitiesProcessor:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider = MagicMock()
    provider.transaction = MagicMock(return_value=ctx)
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = "org-1"
    return proc


def _tx_store(creator_email="dash@gmail.com", source_user=None, authenticated_by=None) -> AsyncMock:
    tx = AsyncMock()
    tx.get_app_by_id = AsyncMock(return_value=MagicMock(authenticated_by=authenticated_by))
    tx.get_user_by_user_id = AsyncMock(return_value={"_key": "creator-key", "email": creator_email})
    tx.get_user_by_email = AsyncMock(return_value=source_user)
    return tx


def _user(key: str, email: str) -> User:
    return User(id=key, email=email, org_id="org-1")


class TestLinkAuthenticatorToSourceUser:
    async def test_different_email_links_to_existing_source_user(self) -> None:
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"))
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")
        tx.batch_upsert_app_users.assert_not_called()
        tx.remove_authenticated_as.assert_not_called()

    async def test_missing_source_user_is_created_as_an_inactive_stub_first(self) -> None:
        tx = _tx_store()
        tx.get_user_by_email = AsyncMock(side_effect=[None, _user("src-key", "darshan@pipeshub.com")])
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        (stub,), = tx.batch_upsert_app_users.await_args.args
        assert stub.email == "darshan@pipeshub.com"
        assert stub.source_user_id == "acct-123"
        assert stub.connector_id == CONNECTOR
        assert stub.is_active is False
        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")

    async def test_same_email_removes_any_existing_link(self) -> None:
        tx = _tx_store(creator_email="Dash@Gmail.com")
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "dash@gmail.com", "acct-123", Connectors.JIRA)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        tx.get_user_by_email.assert_not_called()

    async def test_unknown_creator_does_nothing(self) -> None:
        tx = _tx_store()
        tx.get_user_by_user_id = AsyncMock(return_value=None)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_not_called()


class TestCacheInvalidation:
    """The cached per-user record maps are keyed per connector and per user, so a link that
    appears or disappears makes them wrong until they are dropped (TTL is 300s)."""

    def _patch_notify(self, monkeypatch) -> AsyncMock:
        notify = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            notify,
        )
        return notify

    async def test_a_new_link_drops_the_connectors_cached_maps(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"))
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_a_removed_link_drops_them_too(self, monkeypatch) -> None:
        """Otherwise the source account's records keep being served after the link is gone."""
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(creator_email="Darshan@PipesHub.com")
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_no_link_to_remove_leaves_the_cache_alone(self, monkeypatch) -> None:
        """The common case: emails match and there was never a link, on every sync."""
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(creator_email="darshan@pipeshub.com")
        tx.remove_authenticated_as = AsyncMock(return_value=False)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        notify.assert_not_called()

    async def test_an_unknown_authenticator_leaves_the_cache_alone(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store()
        tx.get_user_by_user_id = AsyncMock(return_value=None)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        notify.assert_not_called()

    async def test_the_cache_is_dropped_only_after_the_write_is_committed(self, monkeypatch) -> None:
        """Dropping it while the transaction is open would let a concurrent search refill it
        from the pre-link state."""
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"))
        proc = _processor(tx)
        committed_at_notify = []
        ctx = proc.data_store_provider.transaction()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            AsyncMock(side_effect=lambda *_: committed_at_notify.append(ctx.__aexit__.await_count)),
        )

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        assert committed_at_notify == [1]


class TestWhoTheLinkHangsOff:
    async def test_the_user_who_authenticated_is_linked_not_the_creator(self) -> None:
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"), authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "admin@x.com"})
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.get_user_by_user_id.assert_awaited_once_with("admin-user-id")
        tx.upsert_authenticated_as.assert_awaited_once_with("admin-key", "src-key", CONNECTOR, "org-1")

    @pytest.mark.parametrize("app", [None, MagicMock(authenticated_by=None), MagicMock(authenticated_by="")])
    async def test_instances_without_the_field_fall_back_to_the_creator(self, app) -> None:
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"))
        tx.get_app_by_id = AsyncMock(return_value=app)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.get_user_by_user_id.assert_awaited_once_with(CREATED_BY)
        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")

    async def test_the_authenticators_own_email_matching_removes_the_link(self) -> None:
        tx = _tx_store(authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "Darshan@PipesHub.com"})
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()


class TestRegisterAuthenticatedSourceUser:
    def _connector(self) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.connector_name = Connectors.JIRA
        conn.data_entities_processor = MagicMock()
        conn.data_entities_processor.link_authenticator_to_source_user = AsyncMock()
        return conn

    async def test_delegates_email_source_id_and_connector_identity(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        conn.data_entities_processor.link_authenticator_to_source_user.assert_awaited_once_with(
            CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA
        )

    @pytest.mark.parametrize(
        ("email", "source_user_id"),
        [(None, "acct-123"), ("", "acct-123"), ("darshan@pipeshub.com", None), ("darshan@pipeshub.com", "")],
    )
    async def test_skips_without_an_email_or_source_id(self, email, source_user_id) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(conn, email, source_user_id)
        conn.data_entities_processor.link_authenticator_to_source_user.assert_not_called()

    async def test_failure_is_logged_not_raised(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        conn.data_entities_processor.link_authenticator_to_source_user.side_effect = RuntimeError("graph down")
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        conn.logger.warning.assert_called_once()
