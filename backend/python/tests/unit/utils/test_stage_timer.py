"""Unit tests for app.utils.stage_timer."""

from unittest.mock import MagicMock, patch

from app.utils.stage_timer import timing_enabled, StageTimer


class TestTimingEnabled:
    def test_env_is_unset(self, monkeypatch):
        monkeypatch.delenv("PIPESHUB_CHAT_TIMING", raising=False)
        assert timing_enabled() is True

    def test_env_is_false(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "false")
        assert timing_enabled() is False

    def test_env_is_FALSE(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "FALSE")
        assert timing_enabled() is False
        
    def test_env_is_0(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "0")
        assert timing_enabled() is False

    def test_env_is_no(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "no")
        assert timing_enabled() is False

    def test_env_is_empty(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "")
        assert timing_enabled() is True


class TestStageTimerMark:
    def test_enabled_is_false_and_marks_is_empty(self):
        timer = StageTimer(enabled=False)
        timer.mark("parse")
        assert timer._marks == []

    def test_perf_counter_mark_intervals(self):
        with patch(
            "app.utils.stage_timer.time.perf_counter",
            side_effect=[100.0, 100.25, 100.5]
        ):
            timer = StageTimer(enabled=True)
            timer.mark("parse")
            assert timer._marks[0] == ("parse", 250.0)
            
            timer.mark("render")
            assert timer._marks[1] == ("render", 250.0)
            

class TestStageTimerEmit:
    def test_logger_not_called(self):
        timer = StageTimer(enabled=False)
        mock_logger = MagicMock()
        timer.emit(mock_logger, "req")
        mock_logger.info.assert_not_called()

    def test_logger_called_once(self):
        timer = StageTimer(enabled=True)
        mock_logger = MagicMock()

        timer.mark("parse")
        timer.emit(mock_logger, "top")
        mock_logger.info.assert_called_once()

        args = mock_logger.info.call_args.args
        message = args[0] % args[1:]
        assert "parse" in message
        assert "total=" in message
        timer.emit(mock_logger, "top")
        mock_logger.info.assert_called_once()