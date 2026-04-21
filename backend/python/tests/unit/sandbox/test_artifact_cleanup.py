"""Tests for app.sandbox.artifact_cleanup."""

import asyncio
import os
import time
from unittest.mock import patch

import pytest

from app.sandbox.artifact_cleanup import (
    _cleanup_temp_directories,
    _get_interval_seconds,
    _get_ttl_seconds,
    start_cleanup_task,
    stop_cleanup_task,
)


class TestConfig:
    def test_default_ttl(self, monkeypatch):
        monkeypatch.delenv("ARTIFACT_TEMP_TTL_HOURS", raising=False)
        assert _get_ttl_seconds() == 3600  # 1 hour

    def test_custom_ttl(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_TEMP_TTL_HOURS", "2")
        assert _get_ttl_seconds() == 7200

    def test_fractional_ttl(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_TEMP_TTL_HOURS", "0.5")
        assert _get_ttl_seconds() == 1800

    def test_default_interval(self, monkeypatch):
        monkeypatch.delenv("ARTIFACT_CLEANUP_INTERVAL_MINUTES", raising=False)
        assert _get_interval_seconds() == 1800  # 30 min

    def test_custom_interval(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_CLEANUP_INTERVAL_MINUTES", "10")
        assert _get_interval_seconds() == 600


class TestConfigInvalidEnv:
    """Tests for invalid env variable handling."""

    def test_invalid_ttl_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_TEMP_TTL_HOURS", "not_a_number")
        assert _get_ttl_seconds() == 3600  # default 1 hour

    def test_invalid_interval_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_CLEANUP_INTERVAL_MINUTES", "abc")
        assert _get_interval_seconds() == 1800  # default 30 min

    def test_empty_string_ttl_falls_back(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_TEMP_TTL_HOURS", "")
        assert _get_ttl_seconds() == 3600

    def test_empty_string_interval_falls_back(self, monkeypatch):
        monkeypatch.setenv("ARTIFACT_CLEANUP_INTERVAL_MINUTES", "")
        assert _get_interval_seconds() == 1800


class TestCleanupTempDirectories:
    def test_nonexistent_root(self):
        removed = _cleanup_temp_directories("/nonexistent/path", 3600)
        assert removed == 0

    def test_empty_directory(self, tmp_path):
        removed = _cleanup_temp_directories(str(tmp_path), 3600)
        assert removed == 0

    def test_removes_old_dirs(self, tmp_path):
        old_dir = tmp_path / "old-exec"
        old_dir.mkdir()
        (old_dir / "file.txt").write_text("data")
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(str(old_dir), (old_time, old_time))

        removed = _cleanup_temp_directories(str(tmp_path), 3600)
        assert removed == 1
        assert not old_dir.exists()

    def test_keeps_recent_dirs(self, tmp_path):
        recent_dir = tmp_path / "recent-exec"
        recent_dir.mkdir()
        (recent_dir / "file.txt").write_text("data")

        removed = _cleanup_temp_directories(str(tmp_path), 3600)
        assert removed == 0
        assert recent_dir.exists()

    def test_ignores_files(self, tmp_path):
        (tmp_path / "stray_file.txt").write_text("data")
        old_time = time.time() - 7200
        os.utime(str(tmp_path / "stray_file.txt"), (old_time, old_time))

        removed = _cleanup_temp_directories(str(tmp_path), 3600)
        assert removed == 0

    def test_mixed_old_and_recent(self, tmp_path):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        old_time = time.time() - 7200
        os.utime(str(old_dir), (old_time, old_time))

        recent_dir = tmp_path / "recent"
        recent_dir.mkdir()

        removed = _cleanup_temp_directories(str(tmp_path), 3600)
        assert removed == 1
        assert not old_dir.exists()
        assert recent_dir.exists()


class TestStartStopCleanupTask:
    @pytest.mark.asyncio
    async def test_start_returns_task(self):
        task = start_cleanup_task()
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        stop_cleanup_task()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_idempotent_start(self):
        t1 = start_cleanup_task()
        t2 = start_cleanup_task()
        assert t1 is t2
        stop_cleanup_task()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_stop_cancels(self):
        task = start_cleanup_task()
        stop_cleanup_task()
        await asyncio.sleep(0.05)
        assert task.done() or task.cancelled()
