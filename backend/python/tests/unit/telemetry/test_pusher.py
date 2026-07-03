"""Unit tests for app.telemetry.pusher — the metrics push loop."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.telemetry.event_buffer import event_buffer
from app.telemetry.pusher import (
    DEFAULT_PUSH_INTERVAL_MS,
    DEFAULT_SERVER_URL,
    METRICS_CONFIG_KEY,
    MetricsPusher,
    _as_bool,
    _as_dict,
)


def make_pusher(config: dict | None = None) -> MetricsPusher:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=config or {})
    config_service.set_config = AsyncMock()
    return MetricsPusher(config_service, "query_service", MagicMock(), version="1.2.3")


class TestAsDict:
    def test_passes_dict_through(self):
        assert _as_dict({"a": 1}) == {"a": 1}

    def test_parses_json_string(self):
        assert _as_dict('{"a": 1}') == {"a": 1}

    def test_invalid_json_and_non_dict_values_return_empty(self):
        assert _as_dict("not-json") == {}
        assert _as_dict("[1, 2]") == {}
        assert _as_dict(None) == {}
        assert _as_dict("") == {}


class TestAsBool:
    def test_bool_passthrough(self):
        assert _as_bool(True) is True
        assert _as_bool(False) is False

    def test_truthy_strings(self):
        for value in ("1", "true", "TRUE", " yes ", "on"):
            assert _as_bool(value) is True

    def test_falsy_strings(self):
        for value in ("0", "false", "off", "no", "random"):
            assert _as_bool(value) is False

    def test_non_string_non_bool_uses_default(self):
        assert _as_bool(None) is True
        assert _as_bool(42, default=False) is False


class TestHasSamples:
    def test_comments_only_is_false(self):
        pusher = make_pusher()
        assert pusher._has_samples("# HELP x y\n# TYPE x counter\n\n") is False

    def test_sample_line_is_true(self):
        pusher = make_pusher()
        assert pusher._has_samples('# HELP x y\nx{a="b"} 1.0\n') is True


class TestCollectorUrl:
    def test_replaces_collect_metrics_suffix(self):
        url = MetricsPusher._collector_url(
            "https://collector.io/collect-metrics", "collect-events"
        )
        assert url == "https://collector.io/collect-events"

    def test_builds_root_path_when_no_collect_metrics(self):
        url = MetricsPusher._collector_url("https://collector.io/ingest", "collect-events")
        assert url == "https://collector.io/collect-events"


class TestHeaders:
    def test_includes_request_id_and_version(self):
        headers = MetricsPusher._headers({"token": ""})
        assert headers["X-Metrics-Version"] == "2"
        assert headers["x-request-id"].startswith("sys-")
        assert "Authorization" not in headers

    def test_bearer_token_when_configured(self):
        headers = MetricsPusher._headers({"token": "secret"})
        assert headers["Authorization"] == "Bearer secret"


class TestLoadConfig:
    async def test_defaults_when_store_is_empty(self):
        pusher = make_pusher({"installId": "install-1"})

        cfg = await pusher._load_config()

        assert cfg["url"] == DEFAULT_SERVER_URL
        assert cfg["token"] == ""
        assert cfg["interval_s"] == DEFAULT_PUSH_INTERVAL_MS / 1000
        assert cfg["enabled"] is True
        assert cfg["install_id"] == "install-1"

    async def test_reads_shared_config_values(self):
        pusher = make_pusher(
            {
                "serverUrl": "https://collector.io/collect-metrics",
                "apiKey": "k",
                "pushIntervalMs": "5000",
                "enableMetricCollection": "false",
                "installId": "install-2",
            }
        )

        cfg = await pusher._load_config()

        assert cfg["url"] == "https://collector.io/collect-metrics"
        assert cfg["token"] == "k"
        assert cfg["interval_s"] == 5.0
        assert cfg["enabled"] is False

    async def test_reads_config_from_shared_key(self):
        pusher = make_pusher({"installId": "i"})
        await pusher._load_config()

        pusher._config_service.get_config.assert_called_once_with(
            METRICS_CONFIG_KEY, default={}
        )


class TestInstallId:
    async def test_generates_and_persists_missing_install_id(self):
        pusher = make_pusher({"serverUrl": "u"})

        install_id = await pusher._get_or_create_install_id({"serverUrl": "u"})

        assert install_id
        pusher._config_service.set_config.assert_called_once()
        _, merged = pusher._config_service.set_config.call_args.args
        assert merged["installId"] == install_id
        assert merged["serverUrl"] == "u"

    async def test_reuses_cached_id_without_touching_store(self):
        pusher = make_pusher()
        first = await pusher._get_or_create_install_id({})
        pusher._config_service.set_config.reset_mock()

        second = await pusher._get_or_create_install_id({})

        assert second == first
        pusher._config_service.set_config.assert_not_called()

    async def test_persist_failure_falls_back_to_in_memory_id(self):
        pusher = make_pusher()
        pusher._config_service.set_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        install_id = await pusher._get_or_create_install_id({})

        assert install_id
        pusher._logger.warning.assert_called_once()


class TestRunLoop:
    async def test_pushes_and_ships_when_enabled(self):
        pusher = make_pusher({"installId": "i", "enableMetricCollection": "true"})
        pusher._push = AsyncMock()
        pusher._ship_events = AsyncMock()

        with (
            patch("app.telemetry.pusher.set_metric_collection_enabled") as consent_mock,
            patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            try:
                await pusher._run()
            except asyncio.CancelledError:
                pass

        consent_mock.assert_called_once_with("query_service", True)
        pusher._push.assert_called_once()
        pusher._ship_events.assert_called_once()

    async def test_does_not_transmit_when_consent_is_off(self):
        pusher = make_pusher({"installId": "i", "enableMetricCollection": "false"})
        pusher._push = AsyncMock()
        pusher._ship_events = AsyncMock()

        with (
            patch("app.telemetry.pusher.set_metric_collection_enabled") as consent_mock,
            patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            try:
                await pusher._run()
            except asyncio.CancelledError:
                pass

        consent_mock.assert_called_once_with("query_service", False)
        pusher._push.assert_not_called()
        pusher._ship_events.assert_not_called()

    async def test_config_error_is_swallowed_and_loop_continues(self):
        pusher = make_pusher()
        pusher._config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)):
            try:
                await pusher._run()
            except asyncio.CancelledError:
                pass

        pusher._logger.warning.assert_called_once()


class TestStartStop:
    async def test_start_creates_task_and_stop_cancels_it(self):
        pusher = make_pusher({"installId": "i", "enableMetricCollection": "false"})

        await pusher.start()
        assert pusher._task is not None
        assert not pusher._task.done()

        await pusher.stop()
        assert pusher._task.done()

    async def test_start_is_idempotent_while_running(self):
        pusher = make_pusher({"installId": "i", "enableMetricCollection": "false"})

        await pusher.start()
        task = pusher._task
        await pusher.start()

        assert pusher._task is task
        await pusher.stop()


class TestShipEvents:
    async def test_skips_post_when_buffer_is_empty(self):
        event_buffer.drain()
        pusher = make_pusher()

        with patch("aiohttp.ClientSession") as session_mock:
            await pusher._ship_events(
                {"url": DEFAULT_SERVER_URL, "token": "", "install_id": "i"}
            )

        session_mock.assert_not_called()
