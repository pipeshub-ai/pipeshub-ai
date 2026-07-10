"""Unit tests for app.telemetry.metrics_refresh — the off-loop graph refresher."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from app.telemetry.metrics_refresh import GraphMetricsRefresher


def wait_until(predicate, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def make_refresher(interval_s=1):
    logger = MagicMock()
    refresher = GraphMetricsRefresher(
        logger=logger, config_service=MagicMock(), interval_s=interval_s
    )
    return refresher, logger


class TestGraphMetricsRefresher:
    def test_refreshes_connector_gauge_off_the_request_thread(self):
        provider = AsyncMock()
        provider.count_active_connectors_by_type = AsyncMock(
            return_value={("gmail", "acme.io"): 3}
        )
        refresher, _ = make_refresher()

        with (
            patch(
                "app.telemetry.metrics_refresh.GraphDBProviderFactory.create_provider",
                AsyncMock(return_value=provider),
            ),
            patch("app.telemetry.metrics_refresh.set_connector_active") as set_mock,
        ):
            refresher.start()
            assert wait_until(lambda: set_mock.called), "gauge was never refreshed"
            refresher.stop()

        set_mock.assert_called_with({("gmail", "acme.io"): 3})
        provider.disconnect.assert_awaited()

    def test_start_is_idempotent(self):
        provider = AsyncMock()
        provider.count_active_connectors_by_type = AsyncMock(return_value={})
        refresher, _ = make_refresher()

        with patch(
            "app.telemetry.metrics_refresh.GraphDBProviderFactory.create_provider",
            AsyncMock(return_value=provider),
        ):
            refresher.start()
            thread = refresher._thread
            assert thread is not None
            refresher.start()
            assert refresher._thread is thread
            refresher.stop()

    def test_connect_failure_logs_and_exits_quietly(self):
        refresher, logger = make_refresher()

        with patch(
            "app.telemetry.metrics_refresh.GraphDBProviderFactory.create_provider",
            AsyncMock(side_effect=RuntimeError("arango down")),
        ):
            refresher.start()
            thread = refresher._thread
            assert thread is not None
            assert wait_until(lambda: not thread.is_alive())

        assert any(
            "could not connect" in str(call) for call in logger.warning.call_args_list
        )

    def test_query_failure_does_not_kill_the_loop(self):
        provider = AsyncMock()
        provider.count_active_connectors_by_type = AsyncMock(
            side_effect=RuntimeError("query failed")
        )
        refresher, logger = make_refresher()

        with (
            patch(
                "app.telemetry.metrics_refresh.GraphDBProviderFactory.create_provider",
                AsyncMock(return_value=provider),
            ),
            patch("app.telemetry.metrics_refresh.set_connector_active") as set_mock,
        ):
            refresher.start()
            thread = refresher._thread
            assert thread is not None
            assert wait_until(
                lambda: any(
                    "connector_active" in str(call)
                    for call in logger.warning.call_args_list
                )
            )
            assert thread.is_alive()
            refresher.stop()

        set_mock.assert_not_called()

    def test_stop_joins_the_thread(self):
        provider = AsyncMock()
        provider.count_active_connectors_by_type = AsyncMock(return_value={})
        refresher, _ = make_refresher(interval_s=60)

        with patch(
            "app.telemetry.metrics_refresh.GraphDBProviderFactory.create_provider",
            AsyncMock(return_value=provider),
        ):
            refresher.start()
            assert wait_until(lambda: provider.count_active_connectors_by_type.called)
            thread = refresher._thread
            assert thread is not None
            refresher.stop()
            assert not thread.is_alive()
