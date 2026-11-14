"""Unit tests for app.utils.stage_timer."""

from unittest.mock import MagicMock, patch

from app.utils.stage_timer import StageTimer, timing_enabled


class TestTimingEnabled:
    """Use different values for PIPESHUB_CHAT_TIMING to check that timing_enabled returns the expected result"""

    def test_env_is_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_CHAT_TIMING", raising=False)
        assert timing_enabled() is True

    def test_env_is_false(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "false")
        assert timing_enabled() is False

    def test_env_is_FALSE(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "FALSE")
        assert timing_enabled() is False

    def test_env_is_0(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "0")
        assert timing_enabled() is False

    def test_env_is_no(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "no")
        assert timing_enabled() is False

    def test_env_is_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "")
        assert timing_enabled() is True


class TestStageTimerMark:
    """With the timer disabled, check that _marks remains empty.
    With the timer enabled and the clock starting at 100.0, check that
    each mark records a 250 ms interval and the correct stage name.
    """

    def test_enabled_is_false_and_marks_is_empty(self) -> None:
        timer = StageTimer(enabled=False)
        timer.mark("parse")
        assert timer._marks == []

    def test_perf_counter_mark_intervals(self) -> None:
        with patch(
            "app.utils.stage_timer.time.perf_counter",
            side_effect=[100.0, 100.25, 100.5],
        ):
            timer = StageTimer(enabled=True)
            timer.mark("parse")
            assert timer._marks[0] == ("parse", 250.0)
            timer.mark("render")
            assert timer._marks[1] == ("render", 250.0)


class TestStageTimerEmit:
    """With StageTimer disabled, check that emit does not call the logger.
    With StageTimer enabled, check that logger.info is called once and
    contains the stage name and total duration. Check that calling emit
    again does not call the logger a second time.
    """

    def test_logger_not_called(self) -> None:
        timer = StageTimer(enabled=False)
        mock_logger = MagicMock()
        timer.emit(mock_logger, "req")
        mock_logger.info.assert_not_called()

    def test_logger_called_once(self) -> None:
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
