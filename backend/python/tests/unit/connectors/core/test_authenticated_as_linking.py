"""Linking the user who authenticated a connector to its source account (processor + BaseConnector hook).

The processor decides from the authenticator's ``Presence`` in this sync's user listing and from the
listed-authenticators marker; it reads no other evidence (a user-app edge can be a row the connector
wrote itself).
"""

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.connector.source_user_listing import Presence, SourceUserListing
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    AuthenticatorLink,
    AuthenticatorLinkResult,
    DataSourceEntitiesProcessor,
)
from app.models.entities import User
from app.services.notification.types import NotificationSeverity, NotificationType

CONNECTOR = "jira-1"
CREATED_BY = "creator-user-id"
P, A, U = Presence.PRESENT, Presence.ABSENT, Presence.UNKNOWN
NOTIFY = "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed"


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


LISTED_KEY = "/services/connectors/jira-1/listedAuthenticators"
LISTED_EDGE = {"_to": f"apps/{CONNECTOR}", "sourceUserId": "acct-own"}
# The authenticator's own link for this connector, to an earlier source account.
OWN_LINK = {"connectorId": CONNECTOR, "_to": "users/old-src"}
SOURCE = _user("src-key", "darshan@pipeshub.com")


async def _link(
    proc: DataSourceEntitiesProcessor, email: str | None = "darshan@pipeshub.com", source_id: str | None = "acct-123",
    *, presence: Presence = A,
) -> AuthenticatorLinkResult:
    return await proc.link_authenticator_to_source_user(
        CONNECTOR, CREATED_BY, email, source_id, Connectors.JIRA, presence=presence,
    )


def _unreadable_marker(proc: DataSourceEntitiesProcessor) -> None:
    async def get_config(key: str, use_cache: bool = True, default: object = None, raise_on_error: bool = False) -> object:
        if raise_on_error:
            raise RuntimeError("kv down")
        return default

    proc.config_service.get_config = AsyncMock(side_effect=get_config)


class TestLinkAuthenticatorToSourceUser:
    async def test_an_absent_authenticator_is_linked_to_the_existing_source_user(self) -> None:
        tx = _tx_store(source_user=SOURCE)

        result = await _link(_processor(tx))

        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")
        tx.batch_upsert_app_users.assert_not_called()
        tx.remove_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.LINKED

    async def test_missing_source_user_is_created_as_an_inactive_stub_first(self) -> None:
        tx = _tx_store()
        tx.get_user_by_email = AsyncMock(side_effect=[None, SOURCE])

        await _link(_processor(tx))

        (stub,), org = tx.batch_upsert_app_users.await_args.args
        assert org == "org-1", "the stub is created in the connector's org"
        assert (stub.email, stub.source_user_id, stub.connector_id, stub.is_active) == (
            "darshan@pipeshub.com", "acct-123", CONNECTOR, False,
        )
        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")

    @pytest.mark.parametrize("presence", [A, U])
    async def test_the_authenticators_own_account_removes_any_link(self, presence: Presence) -> None:
        tx = _tx_store(creator_email="Dash@Gmail.com")

        await _link(_processor(tx), "dash@gmail.com", presence=presence)

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        tx.get_user_by_email.assert_not_called()

    async def test_unknown_creator_does_nothing(self) -> None:
        tx = _tx_store()
        tx.get_user_by_user_id = AsyncMock(return_value=None)

        await _link(_processor(tx))

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_not_called()

    @pytest.mark.parametrize(("email", "source_id"), [(None, "acct-123"), ("darshan@pipeshub.com", None)])
    async def test_an_unknown_source_account_never_links(self, email: str | None, source_id: str | None) -> None:
        tx = _tx_store(source_user=SOURCE)

        await _link(_processor(tx), email, source_id)

        tx.upsert_authenticated_as.assert_not_called()


class TestPresenceAndMarker:
    """Every Presence against every marker state: present in it, absent from it, unreadable."""

    @staticmethod
    def _proc(tx: AsyncMock, marker: str) -> DataSourceEntitiesProcessor:
        proc = _processor(tx)
        if marker == "holds":
            proc.config_service.get_config = AsyncMock(return_value=["other-key", "creator-key"])
        elif marker == "others":
            proc.config_service.get_config = AsyncMock(return_value=["other-key"])
        elif marker == "unreadable":
            _unreadable_marker(proc)
        return proc

    # (presence, marker state, linked, link removed, marker written)
    CASES = [
        (P, "empty", False, True, True),
        (P, "others", False, True, True),
        (P, "holds", False, True, False),
        (P, "unreadable", False, True, False),
        (A, "empty", True, False, False),
        (A, "others", True, False, False),
        (A, "holds", False, True, False),
        (A, "unreadable", False, False, False),
        (U, "empty", False, False, False),
        (U, "others", False, False, False),
        (U, "holds", False, True, False),
        (U, "unreadable", False, False, False),
    ]

    @pytest.mark.parametrize(
        ("presence", "marker", "linked", "unlinked", "written"), CASES,
        ids=[f"{c[0].value}-{c[1]}" for c in CASES],
    )
    async def test_the_decision(
        self, presence: Presence, marker: str, linked: bool, unlinked: bool, written: bool,
    ) -> None:
        tx = _tx_store(source_user=SOURCE, links=[OWN_LINK])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = self._proc(tx, marker)

        result = await _link(proc, presence=presence)

        assert tx.upsert_authenticated_as.await_count == (1 if linked else 0)
        assert tx.remove_authenticated_as.await_count == (1 if unlinked else 0)
        assert proc.config_service.set_config.await_count == (1 if written else 0)
        expected = AuthenticatorLink.LINKED if linked else AuthenticatorLink.UNLINKED if unlinked else AuthenticatorLink.UNCHANGED
        assert result.outcome is expected

    async def test_a_present_authenticator_is_added_to_the_marker(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        proc = self._proc(tx, "others")

        await _link(proc, presence=P)

        proc.config_service.set_config.assert_awaited_once_with(LISTED_KEY, ["other-key", "creator-key"])

    @pytest.mark.parametrize("presence", [A, U])
    async def test_a_user_app_edge_is_not_evidence(self, presence: Presence) -> None:
        # A source-account edge may be a row the connector wrote itself (a creator stub), never proof.
        tx = _tx_store(source_user=SOURCE, edges=[LISTED_EDGE], links=[OWN_LINK])

        await _link(_processor(tx), presence=presence)

        assert tx.upsert_authenticated_as.await_count == (1 if presence is A else 0)
        tx.remove_authenticated_as.assert_not_called()
        assert CollectionNames.USER_APP_RELATION.value not in [c.args[1] for c in tx.get_edges_from_node.await_args_list]

    @pytest.mark.parametrize("stored", [
        "creator-key", {"creator-key": 1}, 7, [None], [""], ["  "], ["creator-key", 3], [["creator-key"]],
    ])
    async def test_a_malformed_marker_is_a_failed_read_and_never_links(self, stored: object) -> None:
        tx = _tx_store(source_user=SOURCE, links=[OWN_LINK])
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=stored)

        result = await _link(proc)

        tx.upsert_authenticated_as.assert_not_called()
        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.logger.warning.assert_called()

    async def test_an_unreadable_marker_still_lets_the_authenticators_own_account_unlink(self) -> None:
        tx = _tx_store(creator_email="Darshan@PipesHub.com")
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        _unreadable_marker(proc)

        result = await _link(proc)

        assert result.outcome is AuthenticatorLink.UNLINKED

    async def test_the_marker_is_written_before_the_link_is_removed(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        order: list[str] = []
        tx.remove_authenticated_as = AsyncMock(side_effect=lambda *_: order.append("unlink") or True)
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(side_effect=lambda *_: order.append("marker") or True)

        await _link(proc, presence=P)

        assert order == ["marker", "unlink"]

    @pytest.mark.parametrize("failure", [{"return_value": False}, {"side_effect": RuntimeError("kv down")}])
    async def test_a_failed_marker_write_is_retried_once_then_warned_and_the_link_still_goes(
        self, failure: dict,
    ) -> None:
        tx = _tx_store(source_user=SOURCE)
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(**failure)

        result = await _link(proc, presence=P)

        assert proc.config_service.set_config.await_count == 2
        proc.logger.warning.assert_called_once()
        assert result.outcome is AuthenticatorLink.UNLINKED

    async def test_a_marker_write_that_fails_once_is_written_on_the_retry(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        proc = _processor(tx)
        proc.config_service.set_config = AsyncMock(side_effect=[False, True])

        await _link(proc, presence=P)

        assert proc.config_service.set_config.await_count == 2
        proc.logger.warning.assert_not_called()

    async def test_a_failed_marker_read_never_rewrites_the_marker(self) -> None:
        tx = _tx_store(source_user=SOURCE)
        proc = _processor(tx)
        _unreadable_marker(proc)

        await _link(proc, presence=P)

        proc.config_service.set_config.assert_not_called()

    async def test_unchanged_link_drops_nothing(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": CONNECTOR, "_to": "users/src-key"}])

        result = await _link(_processor(tx))

        assert result.outcome is AuthenticatorLink.UNCHANGED
        notify.assert_not_called()

    async def test_retargeted_link_is_reported_as_linked(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": CONNECTOR, "_to": "users/old-src"}])

        result = await _link(_processor(tx))

        assert (result.outcome, result.authenticator_user_id, result.source_email) == (
            AuthenticatorLink.LINKED, CREATED_BY, "darshan@pipeshub.com",
        )
        notify.assert_awaited_once()

    async def test_link_of_another_connector_is_not_this_one(self) -> None:
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": "drive-1", "_to": "users/src-key"}])

        assert (await _link(_processor(tx))).outcome is AuthenticatorLink.LINKED

    async def test_repeat_removal_is_quiet(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        tx = _tx_store(source_user=SOURCE)
        tx.remove_authenticated_as = AsyncMock(return_value=False)
        proc = _processor(tx)
        proc.config_service.get_config = AsyncMock(return_value=["creator-key"])

        result = await _link(proc, presence=P)

        assert result.outcome is AuthenticatorLink.UNCHANGED
        proc.config_service.set_config.assert_not_called()
        notify.assert_not_called()


class TestAnotherUsersLink:
    """A connector authenticated again by someone else: the link the earlier authenticator holds for it
    goes on any Presence, since their credentials no longer run it."""

    @pytest.mark.parametrize(("email", "presence", "marker"), [
        ("darshan@pipeshub.com", U, "empty"),
        (None, U, "empty"),
        ("darshan@pipeshub.com", A, "unreadable"),
        (None, A, "empty"),
    ], ids=["unknown", "removal-pass", "absent-but-marker-unreadable", "absent-no-account"])
    async def test_a_link_the_authenticator_does_not_hold_is_removed(
        self, email: str | None, presence: Presence, marker: str, monkeypatch,
    ) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        tx = _tx_store(source_user=SOURCE, links=[{"connectorId": "drive-1", "_to": "users/src-key"}])
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = TestPresenceAndMarker._proc(tx, marker)

        result = await _link(proc, email, "acct-123" if email else None, presence=presence)

        assert result.outcome is AuthenticatorLink.UNLINKED
        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()
        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_the_authenticators_own_link_stays_while_presence_is_unknown(self) -> None:
        tx = _tx_store(source_user=SOURCE, links=[OWN_LINK])

        result = await _link(_processor(tx), presence=U)

        assert result.outcome is AuthenticatorLink.UNCHANGED
        tx.remove_authenticated_as.assert_not_called()

    async def test_no_link_at_all_changes_nothing(self, monkeypatch) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        tx = _tx_store(source_user=SOURCE)
        tx.remove_authenticated_as = AsyncMock(return_value=False)

        result = await _link(_processor(tx), presence=U)

        assert result.outcome is AuthenticatorLink.UNCHANGED
        notify.assert_not_called()

    async def test_a_connector_whose_listing_never_proves_anything_still_drops_it(self, monkeypatch) -> None:
        """A search-based listing (active users only, as Confluence Cloud's) is UNKNOWN for anyone it
        does not name; the link the previous authenticator left must still go."""
        from app.connectors.core.base.connector.connector_service import BaseConnector

        monkeypatch.setattr(NOTIFY, AsyncMock())
        tx = _tx_store(source_user=SOURCE, authenticated_by="new-admin-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "new-admin-key", "email": "new.admin@corp.com"})
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id, conn.created_by, conn.scope = CONNECTOR, CREATED_BY, "team"
        conn.connector_name = Connectors.CONFLUENCE
        conn.notify = AsyncMock()
        conn.data_entities_processor = _processor(tx)
        for name in ("_link_authenticator", "_authenticator_presence", "authenticator_email"):
            setattr(conn, name, partial(getattr(BaseConnector, name), conn))
        listing = SourceUserListing(includes_inactive=False)
        listing.add_row("someone@corp.com")
        listing.finish()

        await BaseConnector.drop_authenticator_link_if_listed(conn)
        await BaseConnector.register_authenticated_source_user(conn, "svc@corp.com", "acct-svc", listing=listing)

        assert listing.presence("new.admin@corp.com") is U
        assert tx.remove_authenticated_as.await_count == 2
        tx.upsert_authenticated_as.assert_not_called()


class TestCacheInvalidation:
    """The cached per-user record maps are keyed per connector and per user, so a link that
    appears or disappears makes them wrong until they are dropped (TTL is 300s)."""

    def _patch_notify(self, monkeypatch) -> AsyncMock:
        notify = AsyncMock()
        monkeypatch.setattr(NOTIFY, notify)
        return notify

    async def test_a_new_link_drops_the_connectors_cached_maps(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)

        await _link(_processor(_tx_store(source_user=SOURCE)))

        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    @pytest.mark.parametrize("listed_by", ["listing", "marker"])
    async def test_unlinking_a_listed_authenticator_drops_the_cached_maps(self, monkeypatch, listed_by: str) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(source_user=SOURCE)
        tx.remove_authenticated_as = AsyncMock(return_value=True)
        proc = _processor(tx)
        if listed_by == "marker":
            proc.config_service.get_config = AsyncMock(return_value=["creator-key"])

        result = await _link(proc, None, None, presence=P if listed_by == "listing" else U)

        assert result.outcome is AuthenticatorLink.UNLINKED
        tx.upsert_authenticated_as.assert_not_called()
        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_a_removed_link_drops_them_too(self, monkeypatch) -> None:
        """Otherwise the source account's records keep being served after the link is gone."""
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(creator_email="Darshan@PipesHub.com")
        tx.remove_authenticated_as = AsyncMock(return_value=True)

        await _link(_processor(tx))

        notify.assert_awaited_once_with(CONNECTOR, "org-1")

    async def test_no_link_to_remove_leaves_the_cache_alone(self, monkeypatch) -> None:
        """The common case: emails match and there was never a link, on every sync."""
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store(creator_email="darshan@pipeshub.com")
        tx.remove_authenticated_as = AsyncMock(return_value=False)

        await _link(_processor(tx))

        notify.assert_not_called()

    async def test_an_unknown_authenticator_leaves_the_cache_alone(self, monkeypatch) -> None:
        notify = self._patch_notify(monkeypatch)
        tx = _tx_store()
        tx.get_user_by_user_id = AsyncMock(return_value=None)

        await _link(_processor(tx))

        notify.assert_not_called()

    async def test_the_cache_is_dropped_only_after_the_write_is_committed(self, monkeypatch) -> None:
        """Dropping it while the transaction is open would let a concurrent search refill it
        from the pre-link state."""
        proc = _processor(_tx_store(source_user=SOURCE))
        committed_at_notify = []
        ctx = proc.data_store_provider.transaction()
        monkeypatch.setattr(NOTIFY, AsyncMock(side_effect=lambda *_: committed_at_notify.append(ctx.__aexit__.await_count)))

        await _link(proc)

        assert committed_at_notify == [1]


class TestWhoTheLinkHangsOff:
    async def test_the_user_who_authenticated_is_linked_not_the_creator(self) -> None:
        tx = _tx_store(source_user=SOURCE, authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "admin@x.com"})

        await _link(_processor(tx))

        tx.get_user_by_user_id.assert_awaited_once_with("admin-user-id", raise_on_error=True)
        tx.upsert_authenticated_as.assert_awaited_once_with("admin-key", "src-key", CONNECTOR, "org-1")

    @pytest.mark.parametrize("app", [None, MagicMock(authenticated_by=None), MagicMock(authenticated_by="")])
    async def test_instances_without_the_field_fall_back_to_the_creator(self, app) -> None:
        tx = _tx_store(source_user=SOURCE)
        tx.get_app_by_id = AsyncMock(return_value=app)

        await _link(_processor(tx))

        tx.get_user_by_user_id.assert_awaited_once_with(CREATED_BY, raise_on_error=True)
        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "src-key", CONNECTOR, "org-1")

    async def test_the_authenticators_own_email_matching_removes_the_link(self) -> None:
        tx = _tx_store(authenticated_by="admin-user-id")
        tx.get_user_by_user_id = AsyncMock(return_value={"_key": "admin-key", "email": "Darshan@PipesHub.com"})

        await _link(_processor(tx))

        tx.remove_authenticated_as.assert_awaited_once_with(CONNECTOR)
        tx.upsert_authenticated_as.assert_not_called()


def _listing(*emails: str | None, complete: bool = True, includes_inactive: bool = True) -> SourceUserListing:
    listing = SourceUserListing(includes_inactive=includes_inactive)
    for email in emails:
        listing.add_row(email)
    if complete:
        listing.finish()
    else:
        listing.cut("cut")
    return listing


class TestRegisterAuthenticatedSourceUser:
    def _connector(
        self, outcome=AuthenticatorLink.UNCHANGED, scope="team", authenticator: str | None = "Admin@Corp.com",
    ) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.connector_name = Connectors.JIRA
        conn.scope = scope
        conn.notify = AsyncMock()
        for name in ("_link_authenticator", "_authenticator_presence"):
            setattr(conn, name, partial(getattr(BaseConnector, name), conn))
        conn.authenticator_email = AsyncMock(return_value=authenticator)
        conn._notification_title = lambda event: f"Jira: {event}"
        conn.data_entities_processor = MagicMock()
        conn.data_entities_processor.link_authenticator_to_source_user = AsyncMock(
            return_value=AuthenticatorLinkResult(outcome, CREATED_BY, "darshan@pipeshub.com")
        )
        return conn

    def _processor_call(self, conn: MagicMock) -> AsyncMock:
        return conn.data_entities_processor.link_authenticator_to_source_user

    def _presence(self, conn: MagicMock) -> Presence:
        return self._processor_call(conn).await_args.kwargs["presence"]

    async def _register(self, conn: MagicMock, email: str | None = "darshan@pipeshub.com",
                        source_user_id: str | None = "acct-123", **kw: object) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        await BaseConnector.register_authenticated_source_user(conn, email, source_user_id, **kw)

    async def test_delegates_email_source_id_and_the_authenticators_presence(self) -> None:
        conn = self._connector()

        await self._register(conn, listing=_listing("a@corp.com"))

        self._processor_call(conn).assert_awaited_once_with(
            CONNECTOR, CREATED_BY, "darshan@pipeshub.com", "acct-123", Connectors.JIRA, presence=A,
        )

    @pytest.mark.parametrize(("listing", "expected"), [
        (_listing("a@corp.com", " admin@corp.com "), P),
        (_listing("a@corp.com"), A),
        (_listing("a@corp.com", complete=False), U),
        (_listing("a@corp.com", None), U),
        (_listing("a@corp.com", includes_inactive=False), U),
    ], ids=["present", "absent", "cut", "unidentified-row", "active-only"])
    async def test_the_presence_is_the_listings_for_the_authenticator(
        self, listing: SourceUserListing, expected: Presence,
    ) -> None:
        conn = self._connector()

        await self._register(conn, listing=listing)

        assert self._presence(conn) is expected

    async def test_no_listing_is_unknown(self) -> None:
        conn = self._connector()

        await self._register(conn)

        assert self._presence(conn) is U

    async def test_an_authenticator_that_cannot_be_read_is_unknown(self) -> None:
        conn = self._connector(authenticator=None)

        await self._register(conn, listing=_listing("a@corp.com"))

        assert self._presence(conn) is U

    async def test_a_personal_connectors_account_is_its_users_own(self) -> None:
        conn = self._connector(scope="personal")

        await self._register(conn)

        assert self._presence(conn) is A

    @pytest.mark.parametrize(
        ("email", "source_user_id"),
        [(None, "acct-123"), ("", "acct-123"), ("darshan@pipeshub.com", None), ("darshan@pipeshub.com", "")],
    )
    async def test_missing_email_still_lets_a_listed_creator_be_unlinked(self, email, source_user_id) -> None:
        conn = self._connector()

        await self._register(conn, email, source_user_id, listing=_listing("admin@corp.com"))

        assert self._presence(conn) is P
        conn.logger.warning.assert_called_once()

    async def test_a_new_link_notifies_only_the_authenticator(self) -> None:
        conn = self._connector(AuthenticatorLink.LINKED)

        await self._register(conn, listing=_listing("a@corp.com"))

        conn.notify.assert_awaited_once_with(
            type=NotificationType.CONNECTOR_INFO,
            severity=NotificationSeverity.INFO,
            title="Jira: You see what the connector's account sees",
            message="You can see what darshan@pipeshub.com can see in this connector, because it signs in as that account.",
            recipient_user_ids=[CREATED_BY],
        )

    @pytest.mark.parametrize("outcome", [AuthenticatorLink.UNCHANGED, AuthenticatorLink.UNLINKED])
    async def test_unchanged_or_unlinked_sends_no_notice(self, outcome) -> None:
        conn = self._connector(outcome)

        await self._register(conn, listing=_listing("a@corp.com"))

        conn.notify.assert_not_awaited()

    async def test_unknown_authenticator_never_aborts_the_sync(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        self._processor_call(conn).side_effect = RuntimeError("authenticator deleted")

        assert await BaseConnector.register_authenticated_source_user(conn, "darshan@pipeshub.com", "acct-123") is None
        assert await BaseConnector.drop_authenticator_link_if_listed(conn) is None
        assert conn.logger.warning.call_count == 2


class TestRemovalPass:
    _connector = TestRegisterAuthenticatedSourceUser._connector
    _presence = TestRegisterAuthenticatedSourceUser._presence
    _processor_call = TestRegisterAuthenticatedSourceUser._processor_call

    async def test_removal_pass_never_links(self) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.drop_authenticator_link_if_listed(conn)
        self._processor_call(conn).assert_awaited_once_with(CONNECTOR, CREATED_BY, None, None, None, presence=U)

    @pytest.mark.parametrize(("listing", "expected"), [
        (_listing("admin@corp.com"), P),
        (_listing("a@corp.com"), U),
    ], ids=["listing-present", "listing-absent-is-not-used"])
    async def test_removal_pass_uses_only_positive_evidence(self, listing: object, expected: Presence) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector()
        await BaseConnector.drop_authenticator_link_if_listed(conn, listing)
        assert self._presence(conn) is expected

    @pytest.mark.parametrize(("scope", "created_by"), [("personal", CREATED_BY), ("team", None)])
    async def test_removal_pass_is_skipped_when_there_is_nothing_to_drop(self, scope, created_by) -> None:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        conn = self._connector(scope=scope)
        conn.created_by = created_by
        await BaseConnector.drop_authenticator_link_if_listed(conn)
        self._processor_call(conn).assert_not_awaited()


class TestAuthenticatorGetsTokenView:
    """The connector account's view goes to the authenticator only when this sync's listing proves
    them ABSENT from the source and the listed-authenticators marker does not hold them. Nothing is
    stored or taken back: the decision is made fresh on every sync."""

    def _connector(
        self, *, marker: object = None, marker_error: Exception | None = None,
        authenticator: dict | None = None, authenticator_error: Exception | None = None,
    ) -> MagicMock:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        async def get_config(key: str, **_: object) -> object:
            assert key == LISTED_KEY, key
            if marker_error:
                raise marker_error
            return marker

        conn = MagicMock(spec=BaseConnector)
        conn.logger = MagicMock()
        conn.connector_id = CONNECTOR
        conn.created_by = CREATED_BY
        conn.config_service = MagicMock(
            get_config=AsyncMock(side_effect=get_config), set_config=AsyncMock(return_value=True),
            delete_config=AsyncMock(return_value=True),
        )
        conn.data_entities_processor = MagicMock(get_app_authenticator_user=AsyncMock(
            return_value={"_key": "creator-key", "email": "Admin@Corp.com"} if authenticator is None else authenticator,
            side_effect=authenticator_error,
        ))
        for name in ("authenticator_email", "_marker_holds"):
            setattr(conn, name, partial(getattr(BaseConnector, name), conn))
        return conn

    async def _gets(self, conn: MagicMock, listing: SourceUserListing | None) -> bool:
        from app.connectors.core.base.connector.connector_service import BaseConnector

        return await BaseConnector.authenticator_gets_token_view(conn, listing)

    async def test_an_authenticator_the_listing_proves_absent_gets_the_view(self) -> None:
        conn = self._connector()

        assert await self._gets(conn, _listing("a@corp.com", "b@corp.com")) is True
        conn.config_service.get_config.assert_awaited_once_with(LISTED_KEY, use_cache=False, raise_on_error=True)
        conn.config_service.set_config.assert_not_awaited()
        conn.config_service.delete_config.assert_not_awaited()

    async def test_an_authenticator_the_listing_names_gets_nothing(self) -> None:
        conn = self._connector()

        assert await self._gets(conn, _listing("a@corp.com", " admin@corp.com ")) is False
        conn.config_service.get_config.assert_not_awaited()

    @pytest.mark.parametrize("listing", [
        None,
        _listing("a@corp.com", complete=False),
        _listing("a@corp.com", None),
        _listing("a@corp.com", includes_inactive=False),
        _listing(),
    ], ids=["no-listing", "cut", "unidentified-row", "active-only", "no-rows"])
    async def test_an_unknown_presence_gets_nothing(self, listing: SourceUserListing | None) -> None:
        conn = self._connector()

        assert await self._gets(conn, listing) is False
        conn.config_service.get_config.assert_not_awaited()

    async def test_an_authenticator_once_listed_gets_nothing(self) -> None:
        conn = self._connector(marker=["other-key", "creator-key"])

        assert await self._gets(conn, _listing("a@corp.com")) is False

    async def test_the_marker_of_another_authenticator_does_not_withhold_the_view(self) -> None:
        conn = self._connector(marker=["creator-key"], authenticator={"_key": "new-key", "email": "admin@corp.com"})

        assert await self._gets(conn, _listing("a@corp.com")) is True

    @pytest.mark.parametrize("unreadable", [
        {"marker_error": RuntimeError("kv down")},
        {"marker": [None]},
        {"marker": "creator-key"},
    ], ids=["marker-read-fails", "marker-entry-null", "marker-not-a-list"])
    async def test_a_marker_that_cannot_be_checked_gives_nothing(self, unreadable: dict) -> None:
        conn = self._connector(**unreadable)

        assert await self._gets(conn, _listing("a@corp.com")) is False
        conn.logger.warning.assert_called_once()

    async def test_an_authenticator_that_cannot_be_read_gets_nothing(self) -> None:
        conn = self._connector(authenticator_error=RuntimeError("graph down"))

        assert await self._gets(conn, _listing("a@corp.com")) is False
        conn.config_service.get_config.assert_not_awaited()

    async def test_no_authenticator_email_gets_nothing(self) -> None:
        conn = self._connector(authenticator={"_key": "creator-key", "email": None})

        assert await self._gets(conn, _listing("a@corp.com")) is False


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
