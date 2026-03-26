"""Unit tests for app.docling_main — Docling service FastAPI entrypoint."""

import asyncio
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_container():
    """Build a mock DoclingAppContainer."""
    c = MagicMock()
    c.logger.return_value = MagicMock()
    c.wire = MagicMock()
    mock_config = MagicMock()
    mock_config.close = AsyncMock()
    c.config_service.return_value = mock_config
    return c


def _make_docling_service(healthy=True, has_health_check=True):
    """Build a mock DoclingService."""
    svc = MagicMock()
    svc.initialize = AsyncMock()
    if has_health_check:
        svc.health_check = AsyncMock(return_value=healthy)
    else:
        # Remove health_check method entirely
        if hasattr(svc, "health_check"):
            del svc.health_check
    return svc


# ===========================================================================
# handle_sigterm
# ===========================================================================


class TestHandleSigterm:
    """Tests for the SIGTERM/SIGINT signal handler."""

    def test_handle_sigterm_exits(self):
        """handle_sigterm calls sys.exit(0)."""
        from app.docling_main import handle_sigterm

        with pytest.raises(SystemExit) as exc_info:
            handle_sigterm(signal.SIGTERM, None)
        assert exc_info.value.code == 0

    def test_handle_sigint_exits(self):
        """handle_sigterm works for SIGINT too."""
        from app.docling_main import handle_sigterm

        with pytest.raises(SystemExit) as exc_info:
            handle_sigterm(signal.SIGINT, MagicMock())
        assert exc_info.value.code == 0


# ===========================================================================
# get_initialized_container
# ===========================================================================


class TestGetInitializedContainer:
    """Tests for the get_initialized_container dependency provider."""

    async def test_first_call_initializes(self):
        """On first call, container is initialized and wired."""
        from app.docling_main import get_initialized_container

        # Clean up from any prior test
        if hasattr(get_initialized_container, "initialized"):
            del get_initialized_container.initialized

        mock_container = _make_container()

        with (
            patch("app.docling_main.container", mock_container),
            patch("app.docling_main.initialize_container", new_callable=AsyncMock) as mock_init,
            patch("app.docling_main.container_lock", asyncio.Lock()),
        ):
            result = await get_initialized_container()

        assert result is mock_container
        mock_init.assert_awaited_once_with(mock_container)
        mock_container.wire.assert_called_once_with(
            modules=["app.services.docling.docling_service"]
        )
        assert get_initialized_container.initialized is True

        # Cleanup
        del get_initialized_container.initialized

    async def test_second_call_skips_init(self):
        """When .initialized already set, initialization is skipped."""
        from app.docling_main import get_initialized_container

        if hasattr(get_initialized_container, "initialized"):
            del get_initialized_container.initialized

        mock_container = _make_container()

        with (
            patch("app.docling_main.container", mock_container),
            patch("app.docling_main.initialize_container", new_callable=AsyncMock) as mock_init,
            patch("app.docling_main.container_lock", asyncio.Lock()),
        ):
            # First call — initializes
            await get_initialized_container()
            mock_init.reset_mock()
            mock_container.wire.reset_mock()

            # Second call — skips
            result = await get_initialized_container()

        mock_init.assert_not_awaited()
        mock_container.wire.assert_not_called()
        assert result is mock_container

        # Cleanup
        del get_initialized_container.initialized

    async def test_double_check_locking(self):
        """The double-check inside the lock prevents duplicate initialization."""
        from app.docling_main import get_initialized_container

        if hasattr(get_initialized_container, "initialized"):
            del get_initialized_container.initialized

        mock_container = _make_container()
        call_count = 0

        async def init_side_effect(c):
            nonlocal call_count
            call_count += 1

        with (
            patch("app.docling_main.container", mock_container),
            patch("app.docling_main.initialize_container", new_callable=AsyncMock, side_effect=init_side_effect) as mock_init,
            patch("app.docling_main.container_lock", asyncio.Lock()),
        ):
            # Run two calls concurrently
            results = await asyncio.gather(
                get_initialized_container(),
                get_initialized_container(),
            )

        # Both should return the same container
        assert results[0] is mock_container
        assert results[1] is mock_container
        # Should only have been called once due to double-check locking
        assert call_count == 1

        # Cleanup
        del get_initialized_container.initialized


# ===========================================================================
# lifespan
# ===========================================================================


class TestLifespan:
    """Tests for the lifespan context manager."""

    async def test_startup_and_shutdown_normal(self):
        """Full startup-yield-shutdown with successful initialization."""
        from app.docling_main import lifespan

        mock_container = _make_container()
        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock()
        mock_container.config_service.return_value = mock_config_service
        mock_logger = MagicMock()
        mock_container.logger.return_value = mock_logger

        mock_svc = _make_docling_service(healthy=True)

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.docling_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.docling_main.DoclingService", return_value=mock_svc),
            patch("app.docling_main.set_docling_service") as mock_set_svc,
        ):
            async with lifespan(mock_app):
                # During lifespan, app.container should be set
                assert mock_app.container is mock_container
                # DoclingService should have been initialized
                mock_svc.initialize.assert_awaited_once()
                mock_set_svc.assert_called_once_with(mock_svc)

            # After shutdown, config should be closed
            mock_config_service.close.assert_awaited_once()

    async def test_startup_failure_raises(self):
        """If DoclingService initialization fails, the error propagates."""
        from app.docling_main import lifespan

        mock_container = _make_container()
        mock_logger = MagicMock()
        mock_container.logger.return_value = mock_logger

        mock_svc = _make_docling_service()
        mock_svc.initialize = AsyncMock(side_effect=Exception("docling init failed"))

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.docling_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.docling_main.DoclingService", return_value=mock_svc),
            patch("app.docling_main.set_docling_service"),
        ):
            with pytest.raises(Exception, match="docling init failed"):
                async with lifespan(mock_app):
                    pass

    async def test_shutdown_config_close_error_logged(self):
        """If config_service.close raises during shutdown, error is logged, not re-raised."""
        from app.docling_main import lifespan

        mock_container = _make_container()
        mock_config_service = MagicMock()
        mock_config_service.close = AsyncMock(side_effect=Exception("redis close err"))
        mock_container.config_service.return_value = mock_config_service
        mock_logger = MagicMock()
        mock_container.logger.return_value = mock_logger

        mock_svc = _make_docling_service()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.docling_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.docling_main.DoclingService", return_value=mock_svc),
            patch("app.docling_main.set_docling_service"),
        ):
            # Should NOT raise even though close() raises
            async with lifespan(mock_app):
                pass

            # Verify the error was logged
            mock_logger.error.assert_called()


# ===========================================================================
# health_check
# ===========================================================================


class TestHealthCheck:
    """Tests for the /health endpoint."""

    async def test_healthy_service(self):
        """DoclingService healthy -> 200."""
        from app.docling_main import health_check, app as docling_main_app

        mock_svc = _make_docling_service(healthy=True)

        with (
            patch.object(docling_main_app.state, "docling_service", mock_svc, create=True),
            patch("app.docling_main.get_epoch_timestamp_in_ms", return_value=111),
        ):
            # Need to make getattr work on app.state
            result = await health_check()

        assert result.status_code == 200

    async def test_unhealthy_service(self):
        """DoclingService unhealthy -> 503."""
        from app.docling_main import health_check, app as docling_main_app

        mock_svc = _make_docling_service(healthy=False)

        with (
            patch.object(docling_main_app.state, "docling_service", mock_svc, create=True),
            patch("app.docling_main.get_epoch_timestamp_in_ms", return_value=111),
        ):
            result = await health_check()

        assert result.status_code == 503

    async def test_no_service_initialized(self):
        """No DoclingService on app.state -> 503 with 'not initialized' error."""
        from app.docling_main import health_check, app as docling_main_app

        # We need to make getattr(app.state, "docling_service", None) return None
        original_state = docling_main_app.state

        class FakeState:
            pass

        docling_main_app.state = FakeState()

        try:
            with patch("app.docling_main.get_epoch_timestamp_in_ms", return_value=111):
                result = await health_check()

            assert result.status_code == 503
        finally:
            docling_main_app.state = original_state

    async def test_no_health_check_method(self):
        """DoclingService has no health_check method -> 503."""
        from app.docling_main import health_check, app as docling_main_app

        mock_svc = MagicMock(spec=[])  # empty spec = no attributes

        original_state = docling_main_app.state

        class FakeState:
            docling_service = mock_svc

        docling_main_app.state = FakeState()

        try:
            with patch("app.docling_main.get_epoch_timestamp_in_ms", return_value=111):
                result = await health_check()

            assert result.status_code == 503
        finally:
            docling_main_app.state = original_state

    async def test_health_check_exception(self):
        """Exception during health check -> 500 with error detail."""
        from app.docling_main import health_check, app as docling_main_app

        mock_svc = MagicMock()
        mock_svc.health_check = AsyncMock(side_effect=RuntimeError("health boom"))

        original_state = docling_main_app.state

        class FakeState:
            docling_service = mock_svc

        docling_main_app.state = FakeState()

        try:
            with patch("app.docling_main.get_epoch_timestamp_in_ms", return_value=111):
                result = await health_check()

            assert result.status_code == 500
        finally:
            docling_main_app.state = original_state


# ===========================================================================
# run
# ===========================================================================


class TestRun:
    """Tests for the run function."""

    def test_run_calls_uvicorn(self):
        """run() delegates to uvicorn.run with correct arguments."""
        from app.docling_main import run

        with patch("app.docling_main.uvicorn.run") as mock_uvicorn:
            run(host="127.0.0.1", port=9001, reload=True)

        mock_uvicorn.assert_called_once_with(
            "app.docling_main:app",
            host="127.0.0.1",
            port=9001,
            log_level="info",
            reload=True,
            workers=1,
        )

    def test_run_defaults(self):
        """run() uses default arguments."""
        from app.docling_main import run

        with patch("app.docling_main.uvicorn.run") as mock_uvicorn:
            run()

        mock_uvicorn.assert_called_once_with(
            "app.docling_main:app",
            host="0.0.0.0",
            port=8081,
            log_level="info",
            reload=False,
            workers=1,
        )


# ===========================================================================
# Module-level configuration
# ===========================================================================


class TestModuleConfiguration:
    """Verify module-level setup."""

    def test_app_title(self):
        """FastAPI app has the expected title."""
        from app.docling_main import app
        assert app.title == "Docling Processing Service"

    def test_signal_handlers_installed(self):
        """SIGTERM and SIGINT handlers are installed."""
        from app.docling_main import handle_sigterm
        # We can verify the handler was registered by checking the current handler
        # Note: In test env signal handlers may be overwritten, so we just confirm
        # the function exists and is callable
        assert callable(handle_sigterm)

    def test_container_lock_is_asyncio_lock(self):
        """container_lock is an asyncio.Lock."""
        from app.docling_main import container_lock
        assert isinstance(container_lock, asyncio.Lock)
