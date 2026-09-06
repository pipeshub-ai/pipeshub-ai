"""Unit tests for app.core.celery_app module."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.celery_app import CeleryApp
from app.services.messaging.config import RedisConfig


def _make_config_service(host="redis-host", port=6380, password=None, db=1):
    """Mock config_service whose `get_redis_config()` resolves to a typed
    `RedisConfig` -- the shape `configure_app()` actually consumes now that
    Celery's broker URL is built from the connection provider (R7)."""
    config_service = MagicMock()
    config_service.get_redis_config = AsyncMock(
        return_value=RedisConfig(host=host, port=port, password=password, db=db)
    )
    return config_service


# ---------------------------------------------------------------------------
# CeleryApp.__init__
# ---------------------------------------------------------------------------
class TestCeleryAppInit:
    """Tests for CeleryApp initialization."""

    def test_init_sets_attributes(self):
        logger = MagicMock()
        config_service = MagicMock()
        app = CeleryApp(logger=logger, config_service=config_service)
        assert app.logger is logger
        assert app.config_service is config_service
        assert app.app is not None  # module-level celery instance

    def test_init_uses_module_level_celery(self):
        from app.core.celery_app import celery as module_celery

        app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        assert app.app is module_celery


# ---------------------------------------------------------------------------
# CeleryApp.get_app
# ---------------------------------------------------------------------------
class TestGetApp:
    """Tests for get_app()."""

    def test_returns_celery_instance(self):
        app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        result = app.get_app()
        assert result is app.app


# ---------------------------------------------------------------------------
# CeleryApp.task
# ---------------------------------------------------------------------------
class TestTask:
    """Tests for task() decorator delegation."""

    def test_delegates_to_celery_app(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        celery_app.app = MagicMock()

        celery_app.task("my_task", bind=True)
        celery_app.app.task.assert_called_once_with("my_task", bind=True)


# ---------------------------------------------------------------------------
# CeleryApp.configure_app
#
# kombu has no Redis Cluster transport (R7): `configure_app()` either honours
# an explicit CELERY_BROKER_URL / CELERY_RESULT_BACKEND override, or derives
# the broker URL from the standalone connection provider and fails fast when
# the provider is cluster-mode and no override was given.
# ---------------------------------------------------------------------------
class TestConfigureApp:
    """Tests for configure_app()."""

    @pytest.mark.asyncio
    async def test_configure_app_success_from_redis_config(self, monkeypatch):
        monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
        monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
        monkeypatch.delenv("REDIS_MODE", raising=False)

        config_service = _make_config_service(
            host="redis-host", port=6380, password=None, db=1
        )
        logger = MagicMock()
        celery_app = CeleryApp(logger=logger, config_service=config_service)
        celery_app.app = MagicMock()

        with (
            patch.object(celery_app, "start_worker") as mock_worker,
            patch.object(celery_app, "start_beat") as mock_beat,
        ):
            await celery_app.configure_app()

        celery_app.app.conf.update.assert_called_once()
        conf_dict = celery_app.app.conf.update.call_args[0][0]
        assert conf_dict["broker_url"] == "redis://redis-host:6380/1"
        assert conf_dict["result_backend"] == "redis://redis-host:6380/1"
        assert conf_dict["task_serializer"] == "json"
        assert conf_dict["timezone"] == "UTC"
        assert conf_dict["enable_utc"] is True
        mock_worker.assert_called_once()
        mock_beat.assert_called_once()

    @pytest.mark.asyncio
    async def test_configure_app_applies_redis_key_namespace(self, monkeypatch):
        """REDIS_KEY_NAMESPACE (R9) becomes kombu's `global_keyprefix` --
        never a client-level ioredis-style prefix, which kombu's Redis
        transport does not support."""
        monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
        monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
        monkeypatch.delenv("REDIS_MODE", raising=False)
        monkeypatch.setenv("REDIS_KEY_NAMESPACE", "tenant-a")

        config_service = _make_config_service(
            host="redis-host", port=6380, password=None, db=1
        )
        celery_app = CeleryApp(logger=MagicMock(), config_service=config_service)
        celery_app.app = MagicMock()

        with (
            patch.object(celery_app, "start_worker"),
            patch.object(celery_app, "start_beat"),
        ):
            await celery_app.configure_app()

        conf_dict = celery_app.app.conf.update.call_args[0][0]
        assert conf_dict["broker_transport_options"] == {
            "global_keyprefix": "tenant-a:"
        }
        assert conf_dict["result_backend_transport_options"] == {
            "global_keyprefix": "tenant-a:"
        }

    @pytest.mark.asyncio
    async def test_configure_app_no_namespace_omits_transport_options(self, monkeypatch):
        monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
        monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
        monkeypatch.delenv("REDIS_MODE", raising=False)
        monkeypatch.delenv("REDIS_KEY_NAMESPACE", raising=False)

        config_service = _make_config_service()
        celery_app = CeleryApp(logger=MagicMock(), config_service=config_service)
        celery_app.app = MagicMock()

        with (
            patch.object(celery_app, "start_worker"),
            patch.object(celery_app, "start_beat"),
        ):
            await celery_app.configure_app()

        conf_dict = celery_app.app.conf.update.call_args[0][0]
        assert "broker_transport_options" not in conf_dict
        assert "result_backend_transport_options" not in conf_dict

    @pytest.mark.asyncio
    async def test_configure_app_prefers_explicit_broker_overrides(self, monkeypatch):
        """An explicit override must win, and must skip the config lookup
        entirely -- this is the escape hatch for REDIS_MODE=cluster."""
        monkeypatch.setenv("CELERY_BROKER_URL", "redis://standalone-broker:6379/0")
        monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://standalone-broker:6379/1")

        config_service = MagicMock()
        config_service.get_redis_config = AsyncMock(
            side_effect=AssertionError("must not be called when overrides are set")
        )
        celery_app = CeleryApp(logger=MagicMock(), config_service=config_service)
        celery_app.app = MagicMock()

        with (
            patch.object(celery_app, "start_worker"),
            patch.object(celery_app, "start_beat"),
        ):
            await celery_app.configure_app()

        conf_dict = celery_app.app.conf.update.call_args[0][0]
        assert conf_dict["broker_url"] == "redis://standalone-broker:6379/0"
        assert conf_dict["result_backend"] == "redis://standalone-broker:6379/1"
        config_service.get_redis_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_configure_app_cluster_mode_without_override_raises(self, monkeypatch):
        monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
        monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
        monkeypatch.setenv("REDIS_MODE", "cluster")

        config_service = _make_config_service()
        celery_app = CeleryApp(logger=MagicMock(), config_service=config_service)
        celery_app.app = MagicMock()

        with pytest.raises(ValueError, match="REDIS_MODE=cluster"):
            await celery_app.configure_app()

    @pytest.mark.asyncio
    async def test_configure_app_propagates_exception(self, monkeypatch):
        monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
        monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)

        config_service = MagicMock()
        config_service.get_redis_config = AsyncMock(
            side_effect=RuntimeError("config error")
        )
        logger = MagicMock()
        celery_app = CeleryApp(logger=logger, config_service=config_service)

        with pytest.raises(RuntimeError, match="config error"):
            await celery_app.configure_app()


# ---------------------------------------------------------------------------
# CeleryApp.setup_app
# ---------------------------------------------------------------------------
class TestSetupApp:
    """Tests for setup_app()."""

    @pytest.mark.asyncio
    async def test_setup_app_calls_configure(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        with patch.object(celery_app, "configure_app", new_callable=AsyncMock) as mock_conf:
            await celery_app.setup_app()
            mock_conf.assert_awaited_once()


# ---------------------------------------------------------------------------
# CeleryApp.start_worker
# ---------------------------------------------------------------------------
class TestStartWorker:
    """Tests for start_worker()."""

    def test_starts_daemon_thread(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        celery_app.app = MagicMock()

        with patch("app.core.celery_app.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            celery_app.start_worker()

            mock_thread_cls.assert_called_once()
            call_kwargs = mock_thread_cls.call_args
            assert call_kwargs.kwargs.get("daemon") is True or call_kwargs[1].get("daemon") is True
            mock_thread.start.assert_called_once()

    def test_worker_thread_calls_worker_main(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        celery_app.app = MagicMock()

        threads = []

        def capture_thread(*args, **kwargs):
            t = MagicMock()
            t._target = kwargs.get("target")
            threads.append(t)
            return t

        with patch("app.core.celery_app.threading.Thread", side_effect=capture_thread):
            celery_app.start_worker()

        assert len(threads) == 1
        target = threads[0]._target
        # Call the target function to test it invokes worker_main
        target()
        celery_app.app.worker_main.assert_called_once()
        call_args = celery_app.app.worker_main.call_args[0][0]
        assert "worker" in call_args
        assert "--pool=solo" in call_args


# ---------------------------------------------------------------------------
# CeleryApp.start_beat
# ---------------------------------------------------------------------------
class TestStartBeat:
    """Tests for start_beat()."""

    def test_starts_daemon_thread(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        celery_app.app = MagicMock()

        with patch("app.core.celery_app.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            celery_app.start_beat()

            mock_thread_cls.assert_called_once()
            call_kwargs = mock_thread_cls.call_args
            assert call_kwargs.kwargs.get("daemon") is True or call_kwargs[1].get("daemon") is True
            mock_thread.start.assert_called_once()

    def test_beat_thread_calls_beat_run(self):
        celery_app = CeleryApp(logger=MagicMock(), config_service=MagicMock())
        celery_app.app = MagicMock()

        mock_beat_instance = MagicMock()
        celery_app.app.Beat.return_value = mock_beat_instance

        threads = []

        def capture_thread(*args, **kwargs):
            t = MagicMock()
            t._target = kwargs.get("target")
            threads.append(t)
            return t

        with patch("app.core.celery_app.threading.Thread", side_effect=capture_thread):
            celery_app.start_beat()

        assert len(threads) == 1
        target = threads[0]._target
        target()
        celery_app.app.Beat.assert_called_once_with(
            app=celery_app.app, loglevel="INFO"
        )
        mock_beat_instance.run.assert_called_once()
