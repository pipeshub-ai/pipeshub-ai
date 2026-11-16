"""Unit tests for app.connectors.core.base.connector.connector_service.BaseConnector.

Covers accessor methods, notify(), and _suppress_notification() backoff dedupe.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import (
    INITIAL_NOTIFICATION_BACKOFF,
    MAX_NOTIFICATION_BACKOFF,
    BaseConnector,
    NotificationOutcome,
)
from app.services.notification.types import (
    NotificationRecipientRole,
    NotificationSeverity,
    NotificationType,
)

# ---------------------------------------------------------------------------
# Concrete subclass (BaseConnector is ABC)
# ---------------------------------------------------------------------------

class ConcreteConnector(BaseConnector):
    async def init(self):
        return True
    def test_connection_and_access(self):
        return True
    def get_signed_url(self, record):
        return None
    def stream_record(self, record, user_id=None, convertTo=None):
        return None
    def run_sync(self):
        pass
    def run_incremental_sync(self):
        pass
    def handle_webhook_notification(self, notification):
        pass
    async def cleanup(self):
        pass
    async def reindex_records(self, record_results):
        pass
    @classmethod
    async def create_connector(cls, logger, data_store_provider, config_service, connector_id):
        return None
    async def get_filter_options(self, filter_key, page=1, limit=20, search=None, cursor=None):
        raise NotImplementedError


class TestBaseConnectorAccessors:
    """Tests for the concrete accessor methods on BaseConnector."""

    def _make_connector(self):
        app = MagicMock()
        app.get_app_name.return_value = "googledrive"
        app.get_app_group.return_value = "google"
        app.get_app_group_name.return_value = "Google Workspace"
        logger = MagicMock()
        dep = MagicMock()
        dep.org_id = "org-1"
        cs = MagicMock()
        return ConcreteConnector(
            app=app, logger=logger, data_entities_processor=dep,
            data_store_provider=dep, config_service=cs, connector_id="conn-1",
            scope="team", created_by="test-user-id",
        )

    def test_get_app(self):
        c = self._make_connector()
        assert c.get_app() is c.app

    def test_get_app_group(self):
        c = self._make_connector()
        result = c.get_app_group()
        c.app.get_app_group.assert_called_once()
        assert result == "google"

    def test_get_app_name(self):
        c = self._make_connector()
        assert c.get_app_name() == "googledrive"

    def test_get_app_group_name(self):
        c = self._make_connector()
        assert c.get_app_group_name() == "Google Workspace"

    def test_get_connector_id(self):
        c = self._make_connector()
        assert c.get_connector_id() == "conn-1"


class TestBaseConnectorDisplayName:
    """display_name feeds user-facing error messages that tell the user to go
    to Connector Settings, so it must match the name shown there."""

    def _make(self, metadata=None, connector_name=None):
        c = TestBaseConnectorAccessors()._make_connector()
        if metadata is not None:
            type(c)._connector_metadata = metadata
        elif hasattr(type(c), "_connector_metadata"):
            del type(c)._connector_metadata
        if connector_name is not None:
            c.connector_name = connector_name
        return c

    def teardown_method(self):
        if hasattr(ConcreteConnector, "_connector_metadata"):
            del ConcreteConnector._connector_metadata

    def test_prefers_the_connector_builder_name(self):
        c = self._make(metadata={"name": "Dropbox Personal"})
        assert c.display_name == "Dropbox Personal"

    def test_instance_name_wins_over_the_type_name(self):
        """Collections has one connector instance per knowledge base, so the
        type name alone cannot say which one failed."""
        c = self._make(metadata={"name": "Collections"})
        c.instance_name = "Engineering Docs"
        assert c.display_name == "Engineering Docs"

    def test_factory_instance_name_wins_over_the_type_name(self) -> None:
        """Sync paths only have the name ConnectorFactory sets (PP-120)."""
        c = self._make(metadata={"name": "S3"})
        c.connector_instance_name = "Finance S3"
        assert c.display_name == "Finance S3"
        assert c._notification_title("Access denied") == "Finance S3: Access denied"

    def test_falls_back_to_type_name_when_instance_name_is_unset(self):
        c = self._make(metadata={"name": "Collections"})
        assert c.instance_name is None
        assert c.display_name == "Collections"

    def test_blank_instance_name_does_not_shadow_the_type_name(self):
        c = self._make(metadata={"name": "Box"})
        c.instance_name = ""
        assert c.display_name == "Box"

    def test_resolves_per_subclass(self):
        """S3 and MinIO share a base class; each must report its own name."""
        c = self._make(metadata={"name": "MinIO"})
        assert c.display_name == "MinIO"

    def test_falls_back_to_enum_value_not_repr(self):
        """`Connectors` is not a str-Enum, so str() would yield 'Connectors.S3'."""
        from app.config.constants.arangodb import Connectors

        c = self._make(metadata=None, connector_name=Connectors.S3)
        assert c.display_name == "S3"
        assert "Connectors." not in c.display_name

    def test_falls_back_to_plain_string_connector_name(self):
        c = self._make(metadata=None, connector_name="Github")
        assert c.display_name == "Github"

    def test_never_raises_on_a_half_built_connector(self):
        """display_name is called from error handlers; if it raised it would
        mask the failure being reported (as it did for connectors constructed
        without BaseConnector.__init__)."""
        bare = ConcreteConnector.__new__(ConcreteConnector)
        assert bare.display_name == "the source"


class TestBaseConnectorNotifyError:
    @pytest.mark.asyncio
    async def test_notify_error_schedules_publish_error(self):
        c = TestBaseConnectorAccessors()._make_connector()
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        c.data_entities_processor.org_id = "org-xyz"

        payload = {
            "title": "Sync failed",
            "message": "something failed",
            "connectorId": "conn-1",
            "errorCode": "E1",
        }
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Sync failed",
            message="something failed",
            payload=payload,
        )
        await asyncio.sleep(0)

        mock_svc.publish_notification.assert_awaited_once()
        kwargs = mock_svc.publish_notification.await_args.kwargs
        assert kwargs["recipient_user_ids"] == ["test-user-id"]
        assert kwargs["org_id"] == "org-xyz"
        assert kwargs["type"] is NotificationType.CONNECTOR_SYNC_ERROR
        assert kwargs["payload"]["connectorId"] == "conn-1"
        assert kwargs["message"] == "something failed"
        assert kwargs["severity"] is NotificationSeverity.ERROR
        assert kwargs["payload"]["errorCode"] == "E1"

    @pytest.mark.asyncio
    async def test_notify_error_no_op_without_service(self):
        c = TestBaseConnectorAccessors()._make_connector()
        c._notification_service = None
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="x",
            message="x",
            payload={},
        )
        # no crash

    @pytest.mark.asyncio
    async def test_notify_error_no_op_without_created_by(self):
        app = MagicMock()
        app.get_app_name.return_value = "googledrive"
        app.get_app_group.return_value = "google"
        app.get_app_group_name.return_value = "Google Workspace"
        logger = MagicMock()
        dep = MagicMock()
        cs = MagicMock()
        c = ConcreteConnector(
            app=app,
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=dep,
            config_service=cs,
            connector_id="conn-1",
            scope="team",
            created_by="",
        )
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="x",
            message="x",
            payload={},
        )
        await asyncio.sleep(0)
        mock_svc.publish_notification.assert_not_called()


@pytest.fixture(autouse=True)
def clear_notification_cache():
    BaseConnector._notification_cache.clear()
    yield
    BaseConnector._notification_cache.clear()


class TestBaseConnectorSuppressNotification:
    TITLE = "Website not accessible"
    MESSAGE = "Website https://example.com is not accessible."

    def _key(self, message: str = MESSAGE) -> str:
        return TestBaseConnectorAccessors()._make_connector()._notification_key(self.TITLE, message, "user:test-user-id")

    def test_info_and_success_never_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        for severity in (NotificationSeverity.INFO, NotificationSeverity.SUCCESS):
            assert c._suppress_notification(self._key(), self.TITLE, severity) is False
        assert BaseConnector._notification_cache == {}

    def test_first_error_not_suppressed_and_seeds_cache(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 1_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is False

        key = self._key()
        assert BaseConnector._notification_cache[key] == (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

    def test_duplicate_within_backoff_is_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 1_000_000
        key = self._key()
        BaseConnector._notification_cache[key] = (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now + 1,
        ):
            assert c._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is True

    def test_after_backoff_window_doubles_backoff(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 10_000_000
        key = self._key()
        next_allowed = now - 1_000
        BaseConnector._notification_cache[key] = (next_allowed, INITIAL_NOTIFICATION_BACKOFF)

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is False

        expected_backoff = INITIAL_NOTIFICATION_BACKOFF * 2
        assert BaseConnector._notification_cache[key] == (
            now + expected_backoff,
            expected_backoff,
        )

    def test_after_max_backoff_resets_to_initial(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 100_000_000
        key = self._key()
        next_allowed = now - MAX_NOTIFICATION_BACKOFF - 1
        BaseConnector._notification_cache[key] = (next_allowed, MAX_NOTIFICATION_BACKOFF)

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is False

        assert BaseConnector._notification_cache[key] == (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

    def test_cache_shared_across_connector_instances(self):
        first = TestBaseConnectorAccessors()._make_connector()
        second = TestBaseConnectorAccessors()._make_connector()
        now = 2_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert first._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is False
            assert second._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is True

    def test_different_messages_are_independent(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 3_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(self._key(), self.TITLE, NotificationSeverity.ERROR) is False
            assert c._suppress_notification(self._key("other message"), self.TITLE, NotificationSeverity.ERROR) is False

        assert len(BaseConnector._notification_cache) == 2

    def test_the_same_notice_to_another_person_is_not_a_repeat(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=5_000_000,
        ):
            warn = lambda who: c._unsuppressed_recipients(  # noqa: E731
                self.TITLE, self.MESSAGE, NotificationSeverity.WARNING, who, None)
            assert warn(["u1"]) == (["u1"], [])
            assert warn(["u2"]) == (["u2"], [])
            assert warn(["u1"]) == ([], [])

    def test_the_repeat_key_holds_the_org(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        other_org = TestBaseConnectorAccessors()._make_connector()
        other_org.data_entities_processor.org_id = "org-2"
        assert other_org._notification_key(self.TITLE, self.MESSAGE, "user:u1") != c._notification_key(
            self.TITLE, self.MESSAGE, "user:u1")

    @pytest.mark.asyncio
    async def test_two_people_get_the_same_notice_and_a_repeat_to_one_is_held_back(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        published = AsyncMock(return_value=True)
        c._notification_service = MagicMock(publish_notification=published)
        send = lambda who: c.notify_and_wait(  # noqa: E731
            type=NotificationType.CONNECTOR_WARNING, severity=NotificationSeverity.WARNING,
            title=self.TITLE, message=self.MESSAGE, recipient_user_ids=[who])
        assert [await send("u1"), await send("u2"), await send("u1")] == [
            NotificationOutcome.SENT, NotificationOutcome.SENT, NotificationOutcome.SUPPRESSED]
        assert [call.kwargs["recipient_user_ids"] for call in published.await_args_list] == [["u1"], ["u2"]]

    @pytest.mark.asyncio
    async def test_each_recipient_has_its_own_backoff(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        published = AsyncMock(return_value=True)
        c._notification_service = MagicMock(publish_notification=published)
        clock = "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms"

        async def send(at: int, who: list[str], roles: list | None = None) -> NotificationOutcome:
            with patch(clock, return_value=at):
                return await c.notify_and_wait(
                    type=NotificationType.CONNECTOR_WARNING, severity=NotificationSeverity.WARNING,
                    title=self.TITLE, message=self.MESSAGE, recipient_user_ids=who, recipient_roles=roles)

        assert await send(1_000, ["A", "B"]) == NotificationOutcome.SENT
        assert await send(2_000, ["B", "C"]) == NotificationOutcome.SENT
        assert await send(3_000, ["C"]) == NotificationOutcome.SUPPRESSED
        assert await send(1_000 + INITIAL_NOTIFICATION_BACKOFF, ["A"]) == NotificationOutcome.SENT
        assert [call.kwargs["recipient_user_ids"] for call in published.await_args_list] == [["A", "B"], ["C"], ["A"]]

        # A role is a recipient too: B is still in backoff, the admins are not.
        assert await send(4_000, ["B"], [NotificationRecipientRole.ADMIN]) == NotificationOutcome.SENT
        assert published.await_args.kwargs["recipient_user_ids"] == []
        assert published.await_args.kwargs["recipient_roles"] == [NotificationRecipientRole.ADMIN]

    @pytest.mark.asyncio
    async def test_whoever_last_synced_is_told_once_the_creator_already_was(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        published = AsyncMock(return_value=True)
        c._notification_service = MagicMock(publish_notification=published)
        send = lambda: c.notify_and_wait(  # noqa: E731
            type=NotificationType.CONNECTOR_WARNING, severity=NotificationSeverity.WARNING,
            title=self.TITLE, message=self.MESSAGE)
        assert await send() == NotificationOutcome.SENT
        c.last_synced_by = "clicker"
        assert await send() == NotificationOutcome.SENT
        assert await send() == NotificationOutcome.SUPPRESSED
        assert [call.kwargs["recipient_user_ids"] for call in published.await_args_list] == [["test-user-id"], ["clicker"]]

    @pytest.mark.asyncio
    async def test_a_failed_publish_frees_every_recipient_it_reserved(self) -> None:
        c = TestBaseConnectorAccessors()._make_connector()
        published = AsyncMock(side_effect=[True, False, True])
        c._notification_service = MagicMock(publish_notification=published)
        send = lambda who: c.notify_and_wait(  # noqa: E731
            type=NotificationType.CONNECTOR_WARNING, severity=NotificationSeverity.WARNING,
            title=self.TITLE, message=self.MESSAGE, recipient_user_ids=who)
        assert await send(["A"]) == NotificationOutcome.SENT
        held = dict(BaseConnector._notification_cache)
        assert await send(["A", "B", "C"]) == NotificationOutcome.FAILED
        assert BaseConnector._notification_cache == held, "A keeps its backoff, B and C are free again"
        assert await send(["A", "B", "C"]) == NotificationOutcome.SENT
        assert published.await_args.kwargs["recipient_user_ids"] == ["B", "C"]

    @pytest.mark.asyncio
    async def test_notify_skips_publish_when_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        c.data_entities_processor.org_id = "org-xyz"
        now = 4_000_000
        key = c._notification_key(self.TITLE, self.MESSAGE, "user:test-user-id")
        BaseConnector._notification_cache[key] = (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now + 1,
        ):
            await c.notify(
                type=NotificationType.CONNECTOR_NOT_ACCESSIBLE,
                severity=NotificationSeverity.ERROR,
                title=self.TITLE,
                message=self.MESSAGE,
            )
            await asyncio.sleep(0)

        mock_svc.publish_notification.assert_not_called()
