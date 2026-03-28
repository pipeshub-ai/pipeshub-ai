"""Unit tests for app.core.celery_app module."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.celery_app import CeleryApp


_SENTINEL = object()


def _make_config_service(redis_config=_SENTINEL):
    """Create a mock config_service.

    Pass explicit None to make get_config return None.
    Omit argument to get a default valid Redis config.
    """
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value=redis_config
        if redis_config is not _SENTINEL
        else {"host": "localhost", "port": 6379}
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
# ---------------------------------------------------------------------------
class TestConfigureApp:
    """Tests for configure_app()."""

    @pytest.mark.asyncio
    async def test_configure_app_success(self):
        config_service = _make_config_service(
            {"host": "redis-host", "port": 6380, "db": 1}
        )
        logger = MagicMock()
        celery_app = CeleryApp(logger=logger, config_service=config_service)
        celery_app.app = MagicMock()

        with (
            patch("app.core.celery_app.build_redis_url", return_value="redis://redis-host:6380/1"),
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
    async def test_configure_app_raises_on_none_config(self):
        config_service = _make_config_service(None)
        logger = MagicMock()
        celery_app = CeleryApp(logger=logger, config_service=config_service)

        with pytest.raises(ValueError, match="Redis configuration not found"):
            await celery_app.configure_app()

    @pytest.mark.asyncio
    async def test_configure_app_raises_on_non_dict_config(self):
        config_service = _make_config_service("not-a-dict")
        logger = MagicMock()
        celery_app = CeleryApp(logger=logger, config_service=config_service)

        with pytest.raises(ValueError, match="Redis configuration not found"):
            await celery_app.configure_app()

    @pytest.mark.asyncio
    async def test_configure_app_propagates_exception(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("config error"))
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
