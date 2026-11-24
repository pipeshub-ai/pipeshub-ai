"""Linking the user who authenticated a connector to its source account (processor + BaseConnector hook)."""

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.connector.connector_service import AuthenticatorGrant
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
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        result = await _link(_processor(tx), extra_present_emails=(" dash@gmail.com ",))

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        assert result.outcome is AuthenticatorLink.UNLINKED

    async def test_a_failed_edge_read_changes_nothing(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        tx.get_edges_from_node = AsyncMock(side_effect=RuntimeError("graph down"))
        proc = _processor(tx)
        result = await _link(proc)

        tx.remove_authenticated_as.assert_not_called()
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    @staticmethod
    def _unreadable_marker(proc: DataSourceEntitiesProcessor) -> None:
        async def get_config(
            key: str, use_cache: bool = True, default: object = None, raise_on_error: bool = False,
        ) -> object:
            if raise_on_error:
                raise RuntimeError("kv down")
            return default

        proc.config_service.get_config = AsyncMock(side_effect=get_config)

    async def test_a_failed_marker_read_never_links(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[])
        proc = _processor(tx)
        self._unreadable_marker(proc)
        result = await _link(proc)

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    @pytest.mark.parametrize("evidence", ["membership", "listing"])
    async def test_a_failed_marker_read_still_removes_a_link_the_source_shows_they_should_not_hold(
        self, evidence: str,
    ) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE] if evidence == "membership" else [])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        self._unreadable_marker(proc)
        extra = ("dash@gmail.com",) if evidence == "listing" else ()

        result = await _link(proc, extra_present_emails=extra)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNLINKED
        proc.config_service.set_config.assert_not_called()

    async def test_a_failed_marker_read_still_unlinks_when_the_account_is_the_authenticators_own(self) -> None:
        tx = _tx_store(creator_email="Darshan@PipesHub.com", edges=[])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        self._unreadable_marker(proc)

        result = await _link(proc)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNLINKED

    @pytest.mark.parametrize("stored", [
        "creator-key", {"creator-key": 1}, 7, [None], [""], ["  "], ["creator-key", 3], [["creator-key"]],
    ])
    async def test_a_malformed_marker_is_a_failed_read_and_never_links(self, stored: object) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[])
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=stored)
        result = await _link(proc)

        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    async def test_the_marker_is_written_before_the_link_is_removed(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        order: list[str] = []
        tx.remove_authenticated_as = AsyncMock(side_effect=lambda *_: order.append("unlink") or True)
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(side_effect=lambda *_: order.append("marker") or True)

        await _link(proc)

        assert order == ["marker", "unlink"]

    @pytest.mark.parametrize("failure", [{"return_value": False}, {"side_effect": RuntimeError("kv down")}])
    async def test_a_failed_marker_write_is_retried_once_then_warned_and_the_link_still_goes(
        self, failure: dict,
    ) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(**failure)

        result = await _link(proc)

        assert proc.config_service.set_config.await_count == 2
        proc.logger.warning.assert_called_once()
        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        assert result.outcome is AuthenticatorLink.UNLINKED

    async def test_a_marker_write_that_fails_once_is_written_on_the_retry(self) -> None:
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE])
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(side_effect=[False, True])

        await _link(proc)

        assert proc.config_service.set_config.await_count == 2
        proc.logger.warning.assert_not_called()

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

        tx.get_user_by_user_id.assert_awaited_once_with("admin-user-id", raise_on_error=True)
        tx.upsert_authenticated_as.assert_awaited_once_with("admin-key", "src-key", CONNECTOR, "org-1")

    @pytest.mark.parametrize("app", [None, MagicMock(authenticated_by=None), MagicMock(authenticated_by="")])
    async def test_instances_without_the_field_fall_back_to_the_creator(self, app) -> None:
        tx = _tx_store(source_user=_user("src-key", "darshan@pipeshub.com"))
        tx.get_app_by_id = AsyncMock(return_value=app)
        proc = _processor(tx)

        await proc.link_authenticator_to_source_user(CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA)

        tx.get_user_by_user_id.assert_awaited_once_with(CREATED_BY, raise_on_error=True)
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

    def _connector(
        self, stored: object = None, error: Exception | None = None, config_service: object = None,
        *, marker: object = None, marker_error: Exception | None = None,
        authenticator: dict | None = None, authenticator_error: Exception | None = None,
    ) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        async def get_config(key: str, **_: object) -> object:
            failure, value = (error, stored) if key == GRANT_KEY else (marker_error, marker)
            if failure:
                raise failure
            return value

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.config_service = config_service or MagicMock(
            get_config=AsyncMock(side_effect=get_config), set_config=AsyncMock(return_value=True),
            delete_config=AsyncMock(return_value=True),
        )
        conn.data_entities_processor = MagicMock(get_app_authenticator_user=AsyncMock(
            return_value={"_key": "creator-key", "email": "admin@corp.com"} if authenticator is None else authenticator,
            side_effect=authenticator_error,
        ))
        for name in ("remember_authenticator_grant", "authenticator_grant_kept", "_marker_may_list",
                     "_read_grant_record"):
            setattr(conn, name, partial(getattr(BaseConnector, name), conn))
        return conn

    async def _decide(self, conn: MagicMock, *, complete: bool, listed: bool) -> AuthenticatorGrant:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        return await BaseConnector.decide_authenticator_grant(
            conn, "Admin@Corp.com", listing_complete=complete, listed=listed,
        )

    async def _kept(self, conn: MagicMock, email: str = "Admin@Corp.com") -> bool:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        return await BaseConnector.authenticator_grant_kept(conn, email)

    async def _remember(self, conn: MagicMock, email: str, *, granted: bool) -> bool:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        return await BaseConnector.remember_authenticator_grant(conn, email, granted=granted)

    async def test_a_stored_grant_for_the_same_authenticator_is_kept(self) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": True})

        assert await self._kept(conn) is True
        conn.config_service.get_config.assert_any_await(GRANT_KEY, use_cache=False, raise_on_error=True)
        conn.config_service.get_config.assert_any_await(LISTED_KEY, use_cache=False, raise_on_error=True)
        conn.config_service.set_config.assert_not_awaited()

    async def test_a_stored_grant_is_not_kept_for_an_authenticator_the_marker_lists(self) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": True}, marker=["other-key", "creator-key"])

        assert await self._kept(conn) is False

    async def test_the_marker_of_another_authenticator_does_not_withhold_the_stored_grant(self) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": True}, marker=["creator-key"],
                               authenticator={"_key": "creator-key", "email": "someone.else@corp.com"})

        assert await self._kept(conn) is True

    @pytest.mark.parametrize("unreadable", [
        {"marker_error": RuntimeError("kv down")},
        {"marker": [None]},
        {"marker": "creator-key"},
        {"marker": ["creator-key"], "authenticator_error": RuntimeError("graph down")},
    ], ids=["marker-read-fails", "marker-entry-null", "marker-not-a-list", "authenticator-read-fails"])
    async def test_a_stored_grant_is_not_kept_when_the_marker_cannot_be_checked(self, unreadable: dict) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": True}, **unreadable)

        assert await self._kept(conn) is False
        conn.logger.warning.assert_called_once()

    @pytest.mark.parametrize("complete", [True, False])
    async def test_a_listing_that_names_the_authenticator_withholds_and_stores_no_grant(self, complete: bool) -> None:
        conn = self._connector({"email": "admin@corp.com", "granted": False})

        assert (await self._decide(conn, complete=complete, listed=True)).granted is False
        conn.config_service.set_config.assert_awaited_once_with(GRANT_KEY, {"email": "admin@corp.com", "granted": False})

    @pytest.mark.parametrize("complete", [True, False])
    async def test_a_given_grant_is_withdrawn_until_the_withdrawal_is_confirmed(self, complete: bool) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        kv = _DictConfig({GRANT_KEY: {"email": "admin@corp.com", "granted": True}})
        conn = self._connector(config_service=kv)

        first = await self._decide(conn, complete=complete, listed=True)
        assert first == AuthenticatorGrant(granted=False, withdraw=True)
        assert kv.values[GRANT_KEY]["granted"] is True, "nothing is stored before the view is taken back"

        retried = await self._decide(conn, complete=complete, listed=True)
        assert retried == AuthenticatorGrant(granted=False, withdraw=True), "a failed removal is tried again"

        await BaseConnector.confirm_authenticator_withdrawn(conn, "Admin@Corp.com")
        assert kv.values[GRANT_KEY] == {"email": "admin@corp.com", "granted": False}
        assert await self._decide(conn, complete=complete, listed=True) == AuthenticatorGrant(granted=False)

    @pytest.mark.parametrize("stored", [
        {"email": "admin@corp.com", "granted": False},
        {"email": "someone.else@corp.com", "granted": True},
    ])
    async def test_nothing_is_withdrawn_when_no_earlier_grant_was_given(self, stored: object) -> None:
        conn = self._connector(stored)

        assert await self._decide(conn, complete=True, listed=True) == AuthenticatorGrant(granted=False, withdraw=False)
        conn.config_service.set_config.assert_awaited_once_with(GRANT_KEY, {"email": "admin@corp.com", "granted": False})

    @pytest.mark.parametrize("complete", [True, False])
    async def test_no_record_at_all_is_withdrawn_as_a_grant_an_earlier_release_may_have_given(
        self, complete: bool,
    ) -> None:
        conn = self._connector(None)

        assert await self._decide(conn, complete=complete, listed=True) == AuthenticatorGrant(granted=False, withdraw=True)
        conn.config_service.set_config.assert_not_awaited()

    @pytest.mark.parametrize("unreadable", [{"error": RuntimeError("kv down")}, {"stored": "granted"}])
    async def test_nothing_is_withdrawn_or_stored_when_the_earlier_grant_cannot_be_read(self, unreadable: dict) -> None:
        conn = self._connector(**unreadable)

        assert await self._decide(conn, complete=True, listed=True) == AuthenticatorGrant(granted=False, withdraw=False)
        conn.logger.warning.assert_called_once()
        conn.config_service.set_config.assert_not_awaited()
        conn.config_service.delete_config.assert_not_awaited()

    async def test_a_complete_listing_without_the_authenticator_grants_and_stores_it(self) -> None:
        conn = self._connector()

        assert await self._decide(conn, complete=True, listed=False) == AuthenticatorGrant(granted=True, withdraw=False)
        conn.config_service.set_config.assert_awaited_once_with(GRANT_KEY, {"email": "admin@corp.com", "granted": True})

    @pytest.mark.parametrize("failure", [{"return_value": False}, {"side_effect": RuntimeError("kv down")}])
    async def test_a_grant_that_cannot_be_stored_is_not_given(self, failure: dict) -> None:
        conn = self._connector()
        conn.config_service.set_config = AsyncMock(**failure)

        assert await self._decide(conn, complete=True, listed=False) == AuthenticatorGrant(granted=False, withdraw=False)

    @pytest.mark.parametrize(("stored", "granted"), [
        ({"email": "admin@corp.com", "granted": True}, True),
        ({"email": "admin@corp.com", "granted": False}, False),
        (None, False),
    ])
    async def test_an_incomplete_listing_without_the_authenticator_follows_the_stored_record(
        self, stored: object, granted: bool,
    ) -> None:
        conn = self._connector(stored)

        assert await self._decide(conn, complete=False, listed=False) == AuthenticatorGrant(granted=granted, withdraw=False)
        conn.config_service.set_config.assert_not_awaited()

    async def test_two_incomplete_listings_the_first_naming_the_authenticator_both_withhold(self) -> None:
        # The processor records the listed authenticator in the marker on the first; the grant stays
        # stored until the withdrawal is confirmed, and the marker keeps the second from using it.
        kv = _DictConfig({GRANT_KEY: {"email": "admin@corp.com", "granted": True}, LISTED_KEY: ["creator-key"]})
        conn = self._connector(config_service=kv)

        assert (await self._decide(conn, complete=False, listed=True)).granted is False
        assert (await self._decide(conn, complete=False, listed=False)).granted is False

    @pytest.mark.parametrize("stored", [None, {"email": "admin@corp.com", "granted": False}])
    async def test_no_stored_grant_for_this_authenticator_grants_nothing(self, stored: object) -> None:
        conn = self._connector(stored)

        assert await self._kept(conn) is False
        conn.config_service.set_config.assert_not_awaited()

    @pytest.mark.parametrize("granted", [True, False])
    async def test_a_record_for_another_authenticator_is_replaced_by_no_grant(self, granted: bool) -> None:
        conn = self._connector({"email": "someone.else@corp.com", "granted": granted})

        assert await self._kept(conn) is False
        conn.config_service.set_config.assert_awaited_once_with(
            GRANT_KEY, {"email": "admin@corp.com", "granted": False},
        )

    async def test_an_old_authenticators_grant_does_not_come_back_after_a_swap(self) -> None:
        kv = _DictConfig({GRANT_KEY: {"email": "a@corp.com", "granted": True}})
        conn = self._connector(config_service=kv)

        assert await self._kept(conn, "b@corp.com") is False
        assert await self._kept(conn, "a@corp.com") is False
        assert kv.values[GRANT_KEY] == {"email": "a@corp.com", "granted": False}

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

    @pytest.mark.parametrize("failure", [{"return_value": False}, {"side_effect": RuntimeError("kv down")}])
    async def test_a_failed_grant_write_warns_and_never_aborts_the_sync(self, failure: dict) -> None:
        conn = self._connector()
        conn.config_service.set_config = AsyncMock(**failure)

        assert await self._remember(conn, "admin@corp.com", granted=True) is False

        conn.logger.warning.assert_called_once()
        conn.config_service.delete_config.assert_not_awaited()

    async def test_a_written_decision_answers_true(self) -> None:
        assert await self._remember(self._connector(), "admin@corp.com", granted=True) is True

    @pytest.mark.parametrize("failure", [{"return_value": False}, {"side_effect": RuntimeError("kv down")}])
    async def test_a_failed_no_grant_write_removes_the_record(self, failure: dict) -> None:
        conn = self._connector()
        conn.config_service.set_config = AsyncMock(**failure)

        await self._remember(conn, "admin@corp.com", granted=False)

        conn.config_service.delete_config.assert_awaited_once_with(GRANT_KEY)
        conn.logger.warning.assert_called_once()

    async def test_a_stale_grant_the_listing_withdrew_is_not_kept_when_the_write_fails(self) -> None:
        kv = _DictConfig({GRANT_KEY: {"email": "admin@corp.com", "granted": True}}, writes_fail=True)
        conn = self._connector(config_service=kv)

        await self._remember(conn, "admin@corp.com", granted=False)

        assert GRANT_KEY not in kv.values
        assert await self._kept(conn) is False

    async def test_a_record_that_cannot_be_removed_either_warns_that_a_stale_grant_may_remain(self) -> None:
        kv = _DictConfig({GRANT_KEY: {"email": "admin@corp.com", "granted": True}}, writes_fail=True, deletes_fail=True)
        conn = self._connector(config_service=kv)

        await self._remember(conn, "admin@corp.com", granted=False)

        assert conn.logger.warning.call_count == 2
        assert "stale" in conn.logger.warning.call_args.args[0]


class TestRemoveUserGrantFromRecordGroups:
    """The connector's own grant (the authenticator's view of the connector account) leaves a
    record group whose source grants are kept as stored, and only that user's direct edge goes."""

    async def test_only_the_users_own_edge_on_those_groups_goes(self, monkeypatch) -> None:
        changed = AsyncMock()
        monkeypatch.setattr(
            "app.connectors.core.base.data_processor.data_source_entities_processor."
            "notify_connector_permissions_changed", changed,
        )
        tx = _tx_store()
        tx.get_user_by_email = AsyncMock(return_value=_user("u-key", "admin@corp.com"))
        groups = {"C1": MagicMock(id="rg-1"), "C2": None}
        tx.get_record_group_by_external_id = AsyncMock(
            side_effect=lambda *, connector_id, external_id: groups[external_id]
        )
        tx.batch_delete_edges = AsyncMock(return_value=1)
        proc = _processor(tx)

        removed = await proc.remove_user_grant_from_record_groups(CONNECTOR, ["C1", "C2", "C1"], "admin@corp.com")

        assert removed == 1
        tx.batch_delete_edges.assert_awaited_once_with(
            [{"from_id": "u-key", "from_collection": CollectionNames.USERS.value,
              "to_id": "rg-1", "to_collection": CollectionNames.RECORD_GROUPS.value}],
            collection=CollectionNames.PERMISSION.value,
        )
        changed.assert_awaited_once_with(CONNECTOR, "org-1")

    @pytest.mark.parametrize("user", [None, _user("", "admin@corp.com")])
    async def test_an_unknown_user_removes_nothing(self, user: object) -> None:
        tx = _tx_store()
        tx.get_user_by_email = AsyncMock(return_value=user)
        proc = _processor(tx)

        assert await proc.remove_user_grant_from_record_groups(CONNECTOR, ["C1"], "admin@corp.com") == 0
        tx.batch_delete_edges.assert_not_called()

    async def test_nothing_to_remove_reads_nothing(self) -> None:
        tx = _tx_store()
        proc = _processor(tx)

        assert await proc.remove_user_grant_from_record_groups(CONNECTOR, [], "admin@corp.com") == 0
        tx.get_user_by_email.assert_not_called()


class _DictConfig:
    """ConfigurationService as a dict: set_config answers False when writes fail, as the real one does."""

    def __init__(self, values: dict, *, writes_fail: bool = False, deletes_fail: bool = False) -> None:
        self.values = dict(values)
        self.writes_fail, self.deletes_fail = writes_fail, deletes_fail

    async def get_config(self, key: str, **_: object) -> object:
        return self.values.get(key)

    async def set_config(self, key: str, value: object) -> bool:
        if self.writes_fail:
            return False
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        if self.deletes_fail:
            return False
        return self.values.pop(key, None) is not None


class TestAuthenticatorUser:
    async def test_the_user_who_authenticated_is_read_not_the_creator(self) -> None:
        tx = _tx_store(authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "admin@x.com"})
        proc = _processor(tx)

        user = await proc.get_app_authenticator_user(CONNECTOR, CREATED_BY)

        assert user["email"] == "admin@x.com"
        tx.get_user_by_user_id.assert_awaited_once_with("admin-user-id", raise_on_error=True)

    async def test_instances_without_the_field_fall_back_to_the_creator(self) -> None:
        tx = _tx_store()
        proc = _processor(tx)

        await proc.get_app_authenticator_user(CONNECTOR, CREATED_BY)

        tx.get_user_by_user_id.assert_awaited_once_with(CREATED_BY, raise_on_error=True)

    async def test_nobody_to_read_answers_an_empty_user(self) -> None:
        tx = _tx_store()
        tx.get_app_by_id = AsyncMock(return_value=None)
        proc = _processor(tx)

        assert await proc.get_app_authenticator_user(CONNECTOR, None) == {}
        tx.get_user_by_user_id.assert_not_called()

    @pytest.mark.parametrize("failing", ["get_app_by_id", "get_user_by_user_id"])
    async def test_a_failed_read_raises_instead_of_answering_nobody(self, failing: str) -> None:
        tx = _tx_store()
        setattr(tx, failing, AsyncMock(side_effect=RuntimeError("graph down")))
        proc = _processor(tx)

        with pytest.raises(RuntimeError):
            await proc.get_app_authenticator_user(CONNECTOR, CREATED_BY)

        tx.get_app_by_id.assert_awaited_once_with(CONNECTOR, raise_on_error=True)


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
