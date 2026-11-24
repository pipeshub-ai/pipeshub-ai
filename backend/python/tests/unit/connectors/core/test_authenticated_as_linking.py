"""Linking the user who authenticated a connector to its source account (processor + BaseConnector hook)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    AuthenticatorLink,
    AuthenticatorLinkResult,
    DataSourceEntitiesProcessor,
)
from app.models.entities import User
from app.services.notification.types import NotificationSeverity, NotificationType

CONNECTOR = "jira-1"
CREATED_BY = "creator-user-id"


def _processor(tx_store) -> DataSourceEntitiesProcessor:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider = MagicMock()
    provider.transaction = MagicMock(return_value=ctx)
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=None)
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, config_service)
    proc.org_id = "org-1"
    return proc


def _tx_store(
    creator_email="dash@gmail.com", source_user=None, authenticated_by=None,
    edges: list[dict] = (), links: list[dict] = (),
) -> AsyncMock:
    tx = AsyncMock()
    tx.get_edges_from_node = AsyncMock(
        side_effect=lambda node, coll, **kw: list(edges)
        if coll == CollectionNames.USER_APP_RELATION.value
        else list(links)
    )
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

        (stub,), org = tx.batch_upsert_app_users.await_args.args
        assert org == "org-1", "the stub is created in the connector's org"
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


LISTED_KEY = "/services/connectors/jira-1/listedAuthenticators"
LISTED_EDGE = {"_to": f"apps/{CONNECTOR}", "sourceUserId": "acct-own"}
SOURCE = _user("src-key", "darshan@pipeshub.com")


async def _link(proc, email="darshan@pipeshub.com", source_id="acct-123", **kw) -> AuthenticatorLinkResult:
    return await proc.link_authenticator_to_source_user(
        CONNECTOR, CREATED_BY, email, source_id, Connectors.JIRA, **kw
    )


class TestListedAuthenticator:
    async def test_listed_creator_is_not_linked_and_any_link_is_removed(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        result = await _link(_processor(tx))

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNLINKED

    async def test_listing_marks_the_authenticator_once(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)
        await _link(proc)
        proc.config_service.set_config.assert_awaited_once_with(LISTED_KEY, ["creator-key"])

        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=["creator-key"])
        await _link(proc)
        proc.config_service.set_config.assert_not_called()

    async def test_marker_keeps_a_withdrawn_creator_unlinked(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[])
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=["creator-key"])
        await _link(proc)

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)

    async def test_marker_of_another_authenticator_does_not_count(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[])
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=["someone-else"])
        await _link(proc)

        tx.upsert_authenticated_as.assert_awaited_once()

    @pytest.mark.parametrize(
        "edge",
        [
            {"_to": f"apps/{CONNECTOR}", "sourceUserId": ""},
            {"_to": f"apps/{CONNECTOR}", "isExternalUser": True, "sourceUserId": "x"},
            {"_to": f"apps/{CONNECTOR}", "isShared": True},
            {"_to": "apps/other", "sourceUserId": "x"},
        ],
    )
    async def test_external_or_unbound_edges_are_not_listing(self, edge) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[edge])
        await _link(_processor(tx))

        tx.upsert_authenticated_as.assert_awaited_once()

    async def test_alias_match_ignores_case_and_spaces(self) -> None:
        tx = _tx_store(creator_email="Dash@Gmail.com", source_user=SOURCE)
        await _link(_processor(tx), extra_present_emails=(" dash@gmail.com ",))

        tx.upsert_authenticated_as.assert_not_called()

    async def test_a_failed_edge_read_changes_nothing(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        tx.get_edges_from_node = AsyncMock(side_effect=RuntimeError("graph down"))
        proc = _processor(tx)
        result = await _link(proc)

        tx.remove_authenticated_as.assert_not_called()
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    async def test_a_failed_marker_read_changes_nothing(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)

        async def get_config(key, use_cache=True, default=None, raise_on_error=False):
            if raise_on_error:
                raise RuntimeError("kv down")
            return default

        proc.config_service.get_config = AsyncMock(side_effect=get_config)
        result = await _link(proc)

        tx.remove_authenticated_as.assert_not_called()
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    @pytest.mark.parametrize("stored", ["creator-key", {"creator-key": 1}, 7])
    async def test_a_marker_that_is_not_a_list_is_a_failed_read(self, stored) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=stored)
        result = await _link(proc)

        tx.remove_authenticated_as.assert_not_called()
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    async def test_a_failed_marker_write_still_removes_the_link(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(side_effect=RuntimeError("kv down"))
        await _link(proc)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)

    async def test_removal_only_pass_never_links(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        await _link(_processor(tx), may_link=False)
        tx.upsert_authenticated_as.assert_not_called()
        tx.batch_upsert_app_users.assert_not_called()

        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        await _link(_processor(tx), may_link=False)
        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)

    @pytest.mark.parametrize(("email", "source_id"), [(None, "acct-123"), ("darshan@pipeshub.com", None)])
    async def test_unknown_source_never_links(self, email, source_id) -> None:
        tx = _tx_store(source_user=SOURCE)
        await _link(_processor(tx), email=email, source_id=source_id, may_link=True)

        tx.upsert_authenticated_as.assert_not_called()

    async def test_individual_connector_skips_the_listing_check(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)
        await _link(proc, check_listing=False)

        tx.upsert_authenticated_as.assert_awaited_once()
        proc.config_service.get_config.assert_not_called()
        colls = [c.args[1] for c in tx.get_edges_from_node.await_args_list]
        assert CollectionNames.USER_APP_RELATION.value not in colls

    async def test_unchanged_link_drops_nothing(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            notify,
        )
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": CONNECTOR, "_to": "users/src-key"}])
        result = await _link(_processor(tx))

        assert result.outcome is AuthenticatorLink.UNCHANGED
        notify.assert_not_called()

    async def test_retargeted_link_is_reported_as_linked(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            notify,
        )
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": CONNECTOR, "_to": "users/old-src"}])
        result = await _link(_processor(tx))

        assert result.outcome is AuthenticatorLink.LINKED
        assert result.authenticator_user_id == CREATED_BY
        assert result.source_email == "darshan@pipeshub.com"
        notify.assert_awaited_once()

    async def test_link_of_another_connector_is_not_this_one(self) -> None:
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": "drive-1", "_to": "users/src-key"}])
        result = await _link(_processor(tx))

        assert result.outcome is AuthenticatorLink.LINKED

    async def test_repeat_removal_is_quiet(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            notify,
        )
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        tx.remove_authenticated_as = AsyncMock(return_value=False)
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=["creator-key"])
        result = await _link(proc)

        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.config_service.set_config.assert_not_called()
        notify.assert_not_called()


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

    async def test_unlinking_a_listed_authenticator_drops_the_cached_maps(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)

        result = await _link(proc)

        assert result.outcome is AuthenticatorLink.UNLINKED
        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    @pytest.mark.parametrize("listed_by", ["marker", "listing"])
    async def test_the_removal_pass_drops_the_cached_maps_for_a_listed_authenticator(
        self, monkeypatch, listed_by: str,
    ) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(source_user=SOURCE)
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        if listed_by == "marker":
            proc.config_service.get_config = AsyncMock(return_value=["creator-key"])
        extra = {"Dash@Gmail.com"} if listed_by == "listing" else ()

        result = await _link(proc, None, None, may_link=False, extra_present_emails=extra)

        assert result.outcome is AuthenticatorLink.UNLINKED
        tx.upsert_authenticated_as.assert_not_called()
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
    def _connector(self, outcome=AuthenticatorLink.UNCHANGED, scope="team") -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.connector_name = Connectors.JIRA
        conn.scope = scope
        conn.notify = AsyncMock()
        conn._link_authenticator = lambda *a, **kw: BaseConnector._link_authenticator(conn, *a, **kw)
        conn._notification_title = lambda event: f"Jira: {event}"
        conn.data_entities_processor = MagicMock()
        conn.data_entities_processor.link_authenticator_to_source_user = AsyncMock(
            return_value=AuthenticatorLinkResult(outcome, CREATED_BY, "darshan@pipeshub.com")
        )
        return conn

    def _processor_call(self, conn: MagicMock) -> AsyncMock:
        return conn.data_entities_processor.link_authenticator_to_source_user

    async def test_delegates_email_source_id_and_connector_identity(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        self._processor_call(conn).assert_awaited_once_with(
            CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA,
            may_link=True, check_listing=True, extra_present_emails=(),
        )

    async def test_incomplete_listing_runs_removal_only(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(
            conn, "darshan@pipeshub.com", "acct-123", listing_complete=False
        )
        assert self._processor_call(conn).await_args.kwargs["may_link"] is False

    @pytest.mark.parametrize(
        ("email", "source_user_id"),
        [(None, "acct-123"), ("", "acct-123"), ("darshan@pipeshub.com", None), ("darshan@pipeshub.com", "")],
    )
    async def test_missing_email_still_lets_a_listed_creator_be_unlinked(self, email, source_user_id) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(conn, email, source_user_id)
        self._processor_call(conn).assert_awaited_once()
        assert self._processor_call(conn).await_args.kwargs["may_link"] is False
        conn.logger.warning.assert_called_once()

    async def test_personal_scope_skips_the_listing_check(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(scope="personal")
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        assert self._processor_call(conn).await_args.kwargs["check_listing"] is False

    async def test_a_new_link_notifies_only_the_authenticator(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(AuthenticatorLink.LINKED)
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        conn.notify.assert_awaited_once_with(
            type=NotificationType.CONNECTOR_INFO,
            severity=NotificationSeverity.INFO,
            title="Jira: You see what the connector's account sees",
            message="You can see what darshan@pipeshub.com can see in this connector, because it signs in as that account.",
            recipient_user_ids=[CREATED_BY],
        )

    @pytest.mark.parametrize("outcome", [AuthenticatorLink.UNCHANGED, AuthenticatorLink.UNLINKED])
    async def test_unchanged_or_unlinked_sends_no_notice(self, outcome) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(outcome)
        await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123")
        conn.notify.assert_not_awaited()

    async def test_removal_pass_never_links(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.drop_authenticator_link_if_listed(conn)
        self._processor_call(conn).assert_awaited_once_with(
            CONNECTOR, CREATED_BY, None, None, None,
            may_link=False, check_listing=True, extra_present_emails=(),
        )

    @pytest.mark.parametrize(("scope", "created_by"), [("personal", CREATED_BY), ("team", None)])
    async def test_removal_pass_is_skipped_when_there_is_nothing_to_drop(self, scope, created_by) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(scope=scope)
        conn.created_by = created_by
        await BaseConnector.drop_authenticator_link_if_listed(conn)
        self._processor_call(conn).assert_not_awaited()

    async def test_unknown_authenticator_never_aborts_the_sync(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        self._processor_call(conn).side_effect = RuntimeError("authenticator deleted")
        assert await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123") is None
        assert await BaseConnector.drop_authenticator_link_if_listed(conn) is None
        assert conn.logger.warning.call_count == 2

    async def test_extra_present_emails_reach_the_processor(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.register_authenticated_source_user(
            conn, "darshan@pipeshub.com", "acct-123", extra_present_emails=("a@x.com",)
        )
        assert self._processor_call(conn).await_args.kwargs["extra_present_emails"] == ("a@x.com",)


GRANT_KEY = "/services/connectors/jira-1/authenticatorGrant"


class TestAuthenticatorGrantRecord:
    """The last decision, taken on a complete user listing, whether the authenticator holds the
    connector account's view. It outlives the instance, so a restart or another replica whose first
    listing is incomplete keeps it instead of starting from no grant."""

    def _connector(self, stored: object = None, error: Exception | None = None) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.config_service = MagicMock(
            get_config=AsyncMock(return_value=stored, side_effect=error), set_config=AsyncMock(),
        )
        return conn

    async def _kept(self, conn: MagicMock, email: str = "Admin@Corp.com") -> bool:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        return await BaseConnector.authenticator_grant_kept(conn, email)

    async def test_a_stored_grant_for_the_same_authenticator_is_kept(self) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": True})

        assert await self._kept(conn) is True
        conn.config_service.get_config.assert_awaited_once_with(GRANT_KEY, use_cache=False, raise_on_error=True)

    @pytest.mark.parametrize("stored", [
        None,
        {"email": "admin@corp.com", "granted": False},
        {"email": "someone.else@corp.com", "granted": True},
    ])
    async def test_no_stored_grant_for_this_authenticator_grants_nothing(self, stored: object) -> None:
        assert await self._kept(self._connector(stored)) is False

    @pytest.mark.parametrize("stored", [True, "admin@corp.com", ["admin@corp.com"], {"granted": "yes"}])
    async def test_a_malformed_record_grants_nothing_and_warns(self, stored: object) -> None:
        conn = self._connector(stored)

        assert await self._kept(conn) is False
        conn.logger.warning.assert_called_once()

    async def test_a_failed_read_grants_nothing_and_warns(self) -> None:
        conn = self._connector(error=RuntimeError("kv down"))

        assert await self._kept(conn) is False
        conn.logger.warning.assert_called_once()

    @pytest.mark.parametrize("granted", [True, False])
    async def test_the_decision_is_stored_for_the_authenticator(self, granted: bool) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()

        await BaseConnector.remember_authenticator_grant(conn, " Admin@Corp.com", granted=granted)

        conn.config_service.set_config.assert_awaited_once_with(
            GRANT_KEY, {"email": "admin@corp.com", "granted": granted},
        )

    async def test_a_failed_write_never_aborts_the_sync(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        conn.config_service.set_config.side_effect = RuntimeError("kv down")

        assert await BaseConnector.remember_authenticator_grant(conn, "admin@corp.com", granted=True) is None
        conn.logger.warning.assert_called_once()


class TestAuthenticatorUser:
    async def test_the_user_who_authenticated_is_read_not_the_creator(self) -> None:
        tx = _tx_store(authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "admin@x.com"})
        proc = _processor(tx)

        user = await proc.get_app_authenticator_user(CONNECTOR, CREATED_BY)

        assert user["email"] == "admin@x.com"
        tx.get_user_by_user_id.assert_awaited_once_with("admin-user-id")

    async def test_instances_without_the_field_fall_back_to_the_creator(self) -> None:
        tx = _tx_store()
        proc = _processor(tx)

        await proc.get_app_authenticator_user(CONNECTOR, CREATED_BY)

        tx.get_user_by_user_id.assert_awaited_once_with(CREATED_BY)

    async def test_nobody_to_read_answers_an_empty_user(self) -> None:
        tx = _tx_store()
        tx.get_app_by_id = AsyncMock(return_value=None)
        proc = _processor(tx)

        assert await proc.get_app_authenticator_user(CONNECTOR, None) == {}
        tx.get_user_by_user_id.assert_not_called()


class TestAuthenticatorEmail:
    def _connector(self, user=None, error=None) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.data_entities_processor = MagicMock()
        conn.data_entities_processor.get_app_authenticator_user = AsyncMock(return_value=user, side_effect=error)
        return conn

    async def test_answers_the_authenticators_email(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector({"email": " admin@x.com "})

        assert await BaseConnector.authenticator_email(conn) == "admin@x.com"
        conn.data_entities_processor.get_app_authenticator_user.assert_awaited_once_with(CONNECTOR, CREATED_BY)

    @pytest.mark.parametrize("user", [None, {}, {"email": None}, {"email": "  "}])
    async def test_no_email_answers_none(self, user) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        assert await BaseConnector.authenticator_email(self._connector(user)) is None

    async def test_a_failed_read_is_logged_not_raised(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(error=RuntimeError("graph down"))

        assert await BaseConnector.authenticator_email(conn) is None
        conn.logger.warning.assert_called_once()


class TestUnlinkAuthenticator:
    def _patch_notify(self, monkeypatch) -> AsyncMock:
        notify = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            notify,
        )
        return notify

    async def test_a_removed_link_drops_the_connectors_cached_maps(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store()
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)

        await proc.unlink_authenticator(CONNECTOR)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_no_link_to_remove_leaves_the_cache_alone(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store()
        tx.remove_authenticated_as = AsyncMock(return_value=False)
        proc = _processor(tx)

        await proc.unlink_authenticator(CONNECTOR)

        notify.assert_not_called()
