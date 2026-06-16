"""Unit tests for app.utils.logger module."""

import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.utils.logger import ColoredFormatter, create_logger


# ---------------------------------------------------------------------------
# ColoredFormatter
# ---------------------------------------------------------------------------
class TestColoredFormatter:
    """Tests for ColoredFormatter."""

    def _make_record(self, level, msg="test message"):
        record = logging.LogRecord(
            name="test",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return record

    def test_warning_gets_yellow(self):
        formatter = ColoredFormatter("%(message)s")
        record = self._make_record(logging.WARNING)
        output = formatter.format(record)
        assert ColoredFormatter.YELLOW in output
        assert ColoredFormatter.RESET in output
        assert "test message" in output

    def test_error_gets_red(self):
        formatter = ColoredFormatter("%(message)s")
        record = self._make_record(logging.ERROR)
        output = formatter.format(record)
        assert ColoredFormatter.RED in output
        assert ColoredFormatter.RESET in output

    def test_critical_gets_red(self):
        formatter = ColoredFormatter("%(message)s")
        record = self._make_record(logging.CRITICAL)
        output = formatter.format(record)
        assert ColoredFormatter.RED in output
        assert ColoredFormatter.RESET in output

    def test_info_no_color(self):
        formatter = ColoredFormatter("%(message)s")
        record = self._make_record(logging.INFO)
        output = formatter.format(record)
        assert ColoredFormatter.YELLOW not in output
        assert ColoredFormatter.RED not in output
        assert ColoredFormatter.RESET not in output
        assert "test message" in output

    def test_debug_no_color(self):
        formatter = ColoredFormatter("%(message)s")
        record = self._make_record(logging.DEBUG)
        output = formatter.format(record)
        assert ColoredFormatter.YELLOW not in output
        assert ColoredFormatter.RED not in output

    def test_format_preserves_message(self):
        formatter = ColoredFormatter("%(levelname)s - %(message)s")
        record = self._make_record(logging.WARNING, "detailed warning")
        output = formatter.format(record)
        assert "detailed warning" in output
        assert "WARNING" in output

    def test_colors_constants(self):
        assert ColoredFormatter.YELLOW == "\033[33m"
        assert ColoredFormatter.RED == "\033[31m"
        assert ColoredFormatter.RESET == "\033[0m"

    def test_colors_dict_mapping(self):
        assert ColoredFormatter.COLORS[logging.WARNING] == ColoredFormatter.YELLOW
        assert ColoredFormatter.COLORS[logging.ERROR] == ColoredFormatter.RED
        assert ColoredFormatter.COLORS[logging.CRITICAL] == ColoredFormatter.RED
        assert logging.INFO not in ColoredFormatter.COLORS
        assert logging.DEBUG not in ColoredFormatter.COLORS


# ---------------------------------------------------------------------------
# create_logger
# ---------------------------------------------------------------------------
class TestCreateLogger:
    """Tests for create_logger()."""

    def test_returns_logger(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_basic")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_service_basic"
        # Clean up
        logger.handlers.clear()

    def test_logger_has_handlers(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_handlers")
        assert len(logger.handlers) >= 2  # file + console
        handler_types = [type(h) for h in logger.handlers]
        assert logging.FileHandler in handler_types
        assert logging.StreamHandler in handler_types
        logger.handlers.clear()

    def test_logger_propagate_false(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_propagate")
        assert logger.propagate is False
        logger.handlers.clear()

    def test_debug_log_level(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=False):
            logger = create_logger("test_service_debug")
        assert logger.level == logging.DEBUG
        logger.handlers.clear()

    def test_info_log_level_default(self):
        env = os.environ.copy()
        env.pop("LOG_LEVEL", None)
        with patch.dict(os.environ, env, clear=True):
            logger = create_logger("test_service_info_default")
        assert logger.level == logging.INFO
        logger.handlers.clear()

    def test_info_log_level_explicit(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "info"}, clear=False):
            logger = create_logger("test_service_info_explicit")
        assert logger.level == logging.INFO
        logger.handlers.clear()

    def test_non_debug_log_level_falls_to_info(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "warning"}, clear=False):
            logger = create_logger("test_service_warning_fallback")
        # Only debug sets DEBUG, everything else gets INFO
        assert logger.level == logging.INFO
        logger.handlers.clear()

    def test_no_duplicate_handlers(self):
        """Calling create_logger twice for same service should not duplicate handlers."""
        name = "test_service_no_dup"
        # Ensure clean state
        existing = logging.getLogger(name)
        existing.handlers.clear()

        with patch.dict(os.environ, {}, clear=False):
            logger1 = create_logger(name)
            count1 = len(logger1.handlers)
            logger2 = create_logger(name)
            count2 = len(logger2.handlers)

        assert logger1 is logger2  # same logger instance
        assert count1 == count2
        logger1.handlers.clear()

    def test_file_handler_uses_utf8(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_utf8")
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) >= 1
        assert file_handlers[0].encoding == "utf-8"
        logger.handlers.clear()

    def test_console_handler_uses_colored_formatter(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_colored")
        stream_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1
        assert isinstance(stream_handlers[0].formatter, ColoredFormatter)
        logger.handlers.clear()

    def test_file_handler_path(self):
        with patch.dict(os.environ, {}, clear=False):
            logger = create_logger("test_service_path")
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) >= 1
        assert "test_service_path.log" in file_handlers[0].baseFilename
        logger.handlers.clear()


# ---------------------------------------------------------------------------
# Module-level code: neo4j data_store branch
# ---------------------------------------------------------------------------
class TestModuleLevelNeo4jBranch:
    """Cover the neo4j logger suppression at module import time (lines 48-50)."""

    def test_neo4j_data_store_suppresses_notifications(self):
        """When DATA_STORE=neo4j, the neo4j.notifications logger is set to ERROR."""
        import importlib
        import app.utils.logger as logger_mod

        with patch.dict(os.environ, {"DATA_STORE": "neo4j"}, clear=False):
            importlib.reload(logger_mod)

        neo4j_logger = logging.getLogger("neo4j.notifications")
        assert neo4j_logger.level == logging.ERROR

        # Reload with default to restore original state
        with patch.dict(os.environ, {"DATA_STORE": "arangodb"}, clear=False):
            importlib.reload(logger_mod)

    def test_non_neo4j_data_store_skips_suppression(self):
        """When DATA_STORE is not neo4j, neo4j logger is not touched."""
        import importlib
        import app.utils.logger as logger_mod

        # Reset the neo4j logger to INFO before reloading
        neo4j_logger = logging.getLogger("neo4j.notifications")
        neo4j_logger.setLevel(logging.INFO)

        with patch.dict(os.environ, {"DATA_STORE": "arangodb"}, clear=False):
            importlib.reload(logger_mod)

        # The level should remain INFO since it was not changed by the module
        assert neo4j_logger.level == logging.INFO


# ---------------------------------------------------------------------------
# Module-level code: Windows platform branch
# ---------------------------------------------------------------------------
class TestModuleLevelWindowsBranch:
    """Cover the Windows-specific console setup (lines 32-37)."""

    def test_windows_platform_branch(self):
        """When sys.platform is win32, ctypes and reconfigure are called."""
        import importlib
        import app.utils.logger as logger_mod

        mock_ctypes = MagicMock()
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        mock_kernel32.GetStdHandle.return_value = -11

        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        with patch.object(sys, "platform", "win32"), \
             patch.dict("sys.modules", {"ctypes": mock_ctypes}), \
             patch.object(sys, "stdout", mock_stdout), \
             patch.object(sys, "stderr", mock_stderr):
            importlib.reload(logger_mod)

        mock_kernel32.SetConsoleMode.assert_called_once()
        mock_stdout.reconfigure.assert_called_once_with(encoding="utf-8")
        mock_stderr.reconfigure.assert_called_once_with(encoding="utf-8")

        # Reload to restore normal state
        importlib.reload(logger_mod)
