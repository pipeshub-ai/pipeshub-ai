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


class TestCleanupEntryStatError:
    def test_skips_entry_when_stat_raises(self, tmp_path):
        """If os.scandir yields an entry whose stat() fails, the cleanup loop
        must move on without crashing."""
        good_old = tmp_path / "good_old"
        good_old.mkdir()
        old_time = time.time() - 7200
        os.utime(str(good_old), (old_time, old_time))

        bad_dir = tmp_path / "bad_dir"
        bad_dir.mkdir()

        real_scandir = os.scandir
        root = str(tmp_path)

        class _BrokenStatEntry:
            def __init__(self, entry):
                self._entry = entry
                self.path = entry.path
                self.name = entry.name

            def is_dir(self):
                return self._entry.is_dir()

            def stat(self):
                raise OSError("perm denied")

        def _wrapped_scandir(path):
            # Only intercept the top-level cleanup scan; defer to the real
            # os.scandir for everything else (including the fd-based calls
            # made by shutil.rmtree).
            if isinstance(path, str) and os.path.realpath(path) == os.path.realpath(root):
                return [
                    _BrokenStatEntry(e) if e.name == "bad_dir" else e
                    for e in real_scandir(path)
                ]
            return real_scandir(path)

        with patch("app.sandbox.artifact_cleanup.os.scandir", _wrapped_scandir):
            # Should not raise; only the good_old dir is removed.
            removed = _cleanup_temp_directories(root, 3600)

        assert removed == 1
        assert not good_old.exists()
        assert bad_dir.exists()


class TestCleanupLoop:
    """Cover the _cleanup_loop coroutine, including the docker-executor
    ImportError swallow branch."""

    @pytest.mark.asyncio
    async def test_loop_runs_one_tick_and_cancels(self, monkeypatch):
        import app.sandbox.artifact_cleanup as mod

        monkeypatch.setattr(mod, "_get_interval_seconds", lambda: 0)
        monkeypatch.setattr(mod, "_get_ttl_seconds", lambda: 0)

        call_roots: list[str] = []
        tick_event = asyncio.Event()

        def _fake_cleanup(root, ttl):
            call_roots.append(root)
            tick_event.set()
            return 2  # exercises the "total_removed > 0" log branch

        monkeypatch.setattr(mod, "_cleanup_temp_directories", _fake_cleanup)

        task = mod.start_cleanup_task()
        try:
            await asyncio.wait_for(tick_event.wait(), timeout=1.0)
        finally:
            mod.stop_cleanup_task()
            # give the task loop a chance to observe the cancellation
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # The local sandbox root must always be visited; the docker root is
        # included when its module imports cleanly.
        assert any("pipeshub_sandbox" in root for root in call_roots)

    @pytest.mark.asyncio
    async def test_loop_swallows_docker_import_error(self, monkeypatch):
        """If ``from app.sandbox.docker_executor import _SANDBOX_ROOT`` raises
        ImportError, the loop must swallow it and keep running."""
        import app.sandbox.artifact_cleanup as mod

        monkeypatch.setattr(mod, "_get_interval_seconds", lambda: 0)
        monkeypatch.setattr(mod, "_get_ttl_seconds", lambda: 0)

        local_calls: list[str] = []
        tick_event = asyncio.Event()

        def _fake_cleanup(root, ttl):
            local_calls.append(root)
            tick_event.set()
            return 0

        monkeypatch.setattr(mod, "_cleanup_temp_directories", _fake_cleanup)

        real_import = __import__

        def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Only raise for the *docker_executor* import the loop does.
            # Do NOT pop the module from sys.modules -- doing so would leave
            # test_docker_executor.py holding a reference to a stale module
            # object and break its monkeypatching of _SANDBOX_ROOT.
            if name == "app.sandbox.docker_executor" and fromlist and "_SANDBOX_ROOT" in fromlist:
                raise ImportError("no docker")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_patched_import):
            task = mod.start_cleanup_task()
            try:
                await asyncio.wait_for(tick_event.wait(), timeout=1.0)
            finally:
                mod.stop_cleanup_task()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert len(local_calls) >= 1


class TestStartStopCleanupTask:
    @pytest.mark.asyncio
    async def test_start_returns_task(self, monkeypatch):
        # Keep the real loop from churning in tests: slow interval.
        import app.sandbox.artifact_cleanup as mod
        monkeypatch.setattr(mod, "_get_interval_seconds", lambda: 3600)
        task = start_cleanup_task()
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        stop_cleanup_task()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_idempotent_start(self, monkeypatch):
        import app.sandbox.artifact_cleanup as mod
        monkeypatch.setattr(mod, "_get_interval_seconds", lambda: 3600)
        t1 = start_cleanup_task()
        t2 = start_cleanup_task()
        assert t1 is t2
        stop_cleanup_task()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_stop_cancels(self, monkeypatch):
        import app.sandbox.artifact_cleanup as mod
        monkeypatch.setattr(mod, "_get_interval_seconds", lambda: 3600)
        task = start_cleanup_task()
        stop_cleanup_task()
        await asyncio.sleep(0.05)
        assert task.done() or task.cancelled()

    def test_stop_when_no_task_is_noop(self):
        """Calling stop when no task is running must not raise."""
        stop_cleanup_task()
        stop_cleanup_task()  # idempotent
