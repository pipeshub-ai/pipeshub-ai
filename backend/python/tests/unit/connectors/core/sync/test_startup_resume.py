"""Boot resume when sync execution lives in another process.

Two things a naive "just republish everything" would lose, and which these
tests pin: the MANUAL sync strategy, and a warm connectors_map.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_dispatcher import SubmitResult

_LOG = logging.getLogger("test")


def _config_service(strategy: str | None) -> AsyncMock:
    cs = AsyncMock()
    cs.get_config = AsyncMock(
        return_value={"sync": {"selectedStrategy": strategy}} if strategy else {}
    )
    return cs


class TestManualStrategy:
    @pytest.mark.asyncio
    async def test_manual_is_detected(self) -> None:
        assert await ConnectorFactory.is_manual_sync_strategy(
            _config_service("MANUAL"), "c1"
        ) is True

    @pytest.mark.asyncio
    async def test_scheduled_is_not_manual(self) -> None:
        assert await ConnectorFactory.is_manual_sync_strategy(
            _config_service("SCHEDULED"), "c1"
        ) is False

    @pytest.mark.asyncio
    async def test_missing_config_is_not_manual(self) -> None:
        """Absent config must not silently stop a connector syncing."""
        assert await ConnectorFactory.is_manual_sync_strategy(
            _config_service(None), "c1"
        ) is False

    @pytest.mark.asyncio
    async def test_unreadable_config_is_not_manual(self) -> None:
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
        assert await ConnectorFactory.is_manual_sync_strategy(cs, "c1") is False


class TestStartupPublisher:
    @pytest.mark.asyncio
    async def test_publishes_for_a_scheduled_connector(self) -> None:
        from app.connectors_main import _publish_startup_resync

        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(return_value=SubmitResult.ACCEPTED)

        with patch(
            "app.connectors_main.get_dispatcher", return_value=dispatcher
        ):
            await _publish_startup_resync(
                AsyncMock(), _config_service("SCHEDULED"), _LOG,
                connector_name="gmail", connector_id="c1", org_id="o1",
            )

        spec = dispatcher.submit.call_args[0][0]
        assert spec.connector_id == "c1"
        assert spec.org_id == "o1"

    @pytest.mark.asyncio
    async def test_skips_a_manual_connector(self) -> None:
        """The regression a plain republish would introduce: MANUAL connectors
        force-synced on every API restart."""
        from app.connectors_main import _publish_startup_resync

        dispatcher = AsyncMock()

        with patch("app.connectors_main.get_dispatcher", return_value=dispatcher):
            await _publish_startup_resync(
                AsyncMock(), _config_service("MANUAL"), _LOG,
                connector_name="gmail", connector_id="c1", org_id="o1",
            )

        dispatcher.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_break_startup(self) -> None:
        """One connector failing to resume must not abort the whole boot."""
        from app.connectors_main import _publish_startup_resync

        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(side_effect=RuntimeError("broker down"))

        with patch("app.connectors_main.get_dispatcher", return_value=dispatcher):
            await _publish_startup_resync(
                AsyncMock(), _config_service("SCHEDULED"), _LOG,
                connector_name="gmail", connector_id="c1", org_id="o1",
            )

    @pytest.mark.asyncio
    async def test_no_dispatcher_is_survivable(self) -> None:
        from app.connectors_main import _publish_startup_resync

        with patch("app.connectors_main.get_dispatcher", return_value=None):
            await _publish_startup_resync(
                AsyncMock(), _config_service("SCHEDULED"), _LOG,
                connector_name="gmail", connector_id="c1", org_id="o1",
            )


class TestStartSyncFlag:
    @pytest.mark.asyncio
    async def test_start_sync_false_returns_without_spawning(self) -> None:
        """The API keeps a warm connector for its own routes but runs nothing.

        Without the warm object, _ensure_connector_initialized pays a full init
        plus a live connection test on the first record stream after a restart.
        """
        connector = AsyncMock()

        with patch.object(
            ConnectorFactory, "initialize_connector",
            new_callable=AsyncMock, return_value=connector,
        ), patch(
            "app.connectors.core.factory.connector_factory.get_coordinator"
        ) as get_coord:
            stm = get_coord.return_value
            result = await ConnectorFactory.create_and_start_sync(
                name="gmail",
                logger=_LOG,
                data_store_provider=AsyncMock(),
                config_service=_config_service("SCHEDULED"),
                connector_id="c1",
                scope="personal",
                created_by="u1",
                start_sync=False,
            )

        assert result is connector
        stm.spawn.assert_not_called()
