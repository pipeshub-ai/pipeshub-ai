"""Unit tests for app.utils.stage_timer."""

from unittest.mock import MagicMock

import pytest

from app.utils.stage_timer import StageTimer, timing_enabled


class TestTimingEnabled:
    def test_defaults_to_enabled_when_unset(self, monkeypatch):
        monkeypatch.delenv("PIPESHUB_CHAT_TIMING", raising=False)

        assert timing_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
    def test_recognizes_disabled_values(self, monkeypatch, value):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", value)

        assert timing_enabled() is False

    def test_empty_value_remains_enabled(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "")

        assert timing_enabled() is True


class TestStageTimer:
    def test_disabled_mark_does_not_record_a_stage(self):
        timer = StageTimer(enabled=False)

        timer.mark("parse")

        assert timer._marks == []

    def test_mark_records_elapsed_milliseconds(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.stage_timer.time.perf_counter",
            MagicMock(side_effect=[100.0, 100.25, 100.5]),
        )
        timer = StageTimer(enabled=True)

        timer.mark("parse")
        timer.mark("render")

        assert timer._marks == [("parse", 250.0), ("render", 250.0)]

    def test_disabled_emit_does_not_log(self):
        logger = MagicMock()

        StageTimer(enabled=False).emit(logger, "chat")

        logger.info.assert_not_called()

    def test_emit_logs_once_with_stages_and_total(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.stage_timer.time.perf_counter",
            MagicMock(side_effect=[100.0, 100.25, 100.5]),
        )
        logger = MagicMock()
        timer = StageTimer(enabled=True)
        timer.mark("parse")

        timer.emit(logger, "chat")
        timer.emit(logger, "chat")

        logger.info.assert_called_once()
        message = logger.info.call_args.args[0] % logger.info.call_args.args[1:]
        assert "parse" in message
        assert "total=" in message
