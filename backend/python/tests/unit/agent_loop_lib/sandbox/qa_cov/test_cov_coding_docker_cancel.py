"""Cancel and failure paths of the Docker coding sandbox, the local process
kill and the settings parser that the main suites do not reach."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding import docker as docker_mod
from app.agent_loop_lib.sandbox.coding import environment as env_mod
from app.agent_loop_lib.sandbox.coding import settings as settings_mod
from app.agent_loop_lib.sandbox.coding.docker import (
    DockerCodingSandbox,
    _RunCancelled,
    _RunHandle,
)

from ..test_docker_coding_sandbox import _fake_container


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)


def _install(sandbox: DockerCodingSandbox, client: MagicMock, tmp_path, handle: _RunHandle) -> tuple:
    return sandbox._install_in_container(
        client, "egress", "/deps", str(tmp_path / "deps"), ["mpmath"], {"command": ["true"]}, handle,
    )


class TestInstallCancelPoints:
    """A cancel seen by the install thread at each step goes straight to
    removing the container, without starting it or reading its logs."""

    def _client(self, container: MagicMock) -> MagicMock:
        client = MagicMock()
        client.containers.create.return_value = container
        return client

    def test_cancelled_before_the_upload(self, tmp_path) -> None:
        container = _fake_container()
        handle = _RunHandle()
        handle.cancelled.set()
        with pytest.raises(_RunCancelled):
            _install(DockerCodingSandbox(working_dir=str(tmp_path)), self._client(container), tmp_path, handle)
        container.put_archive.assert_not_called()
        container.start.assert_not_called()
        container.remove.assert_called_once_with(force=True)
        assert handle.container_id == container.id

    def test_cancelled_during_the_upload(self, tmp_path) -> None:
        container = _fake_container()
        handle = _RunHandle()
        container.put_archive.side_effect = lambda *a, **k: handle.cancelled.set()
        with pytest.raises(_RunCancelled):
            _install(DockerCodingSandbox(working_dir=str(tmp_path)), self._client(container), tmp_path, handle)
        container.start.assert_not_called()
        container.remove.assert_called_once_with(force=True)

    def test_cancel_that_breaks_the_wait_is_not_a_timeout(self, tmp_path) -> None:
        container = _fake_container()
        handle = _RunHandle()

        def wait(**_k: object) -> dict:
            handle.cancelled.set()
            raise ConnectionError("container removed")

        container.wait.side_effect = wait
        with pytest.raises(_RunCancelled):
            _install(DockerCodingSandbox(working_dir=str(tmp_path)), self._client(container), tmp_path, handle)
        container.kill.assert_not_called()
        container.logs.assert_not_called()

    def test_cancelled_after_the_wait(self, tmp_path) -> None:
        container = _fake_container()
        handle = _RunHandle()

        def wait(**_k: object) -> dict:
            handle.cancelled.set()
            return {"StatusCode": 137}

        container.wait.side_effect = wait
        with pytest.raises(_RunCancelled):
            _install(DockerCodingSandbox(working_dir=str(tmp_path)), self._client(container), tmp_path, handle)
        container.logs.assert_not_called()
        container.remove.assert_called_once_with(force=True)

    def test_a_host_side_timeout_kills_and_says_so_even_if_kill_fails(self, tmp_path) -> None:
        container = _fake_container(stderr=b"partial")
        container.wait.side_effect = TimeoutError("read timed out")
        container.kill.side_effect = RuntimeError("already gone")
        container.remove.side_effect = RuntimeError("already gone")
        code, _out, err = _install(
            DockerCodingSandbox(working_dir=str(tmp_path)), self._client(container), tmp_path, _RunHandle(),
        )
        assert code == -1
        assert err == "[sandbox] Package installation timed out after 300s.\npartial"
        assert "read timed out" not in err


class _Provider:
    def __init__(self, remove: object) -> None:
        self.client = SimpleNamespace(api=SimpleNamespace(remove_container=MagicMock()))
        self._remove = remove

    async def run_control(self, fn: object, *args: object, **kwargs: object) -> None:
        if isinstance(self._remove, BaseException):
            raise self._remove
        if isinstance(self._remove, float):
            await asyncio.sleep(self._remove)  # a removal stuck behind a busy control pool


class TestStopRun:
    async def test_a_failed_remove_is_logged_and_the_wait_still_runs(
        self, tmp_path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path))
        sandbox._provider = _Provider(RuntimeError("daemon 500"))  # type: ignore[assignment]
        handle = _RunHandle(container_id="c0ffee000000aaaa")
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(0.02, fut.set_result, None)
        with caplog.at_level(logging.DEBUG, logger=docker_mod.logger.name):
            await sandbox._stop_run(handle, fut)
        assert handle.cancelled.is_set()
        assert "removing cancelled sandbox container c0ffee000000 failed: daemon 500" in caplog.text
        assert "sandbox run cancelled: container c0ffee000000 removed" in caplog.text

    async def test_a_second_cancel_during_the_remove_is_re_raised_after_the_wait(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path))
        sandbox._provider = _Provider(0.05)  # type: ignore[assignment]
        handle = _RunHandle(container_id="c0ffee000000aaaa")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        loop.call_later(0.08, fut.set_result, None)
        stop = asyncio.ensure_future(sandbox._stop_run(handle, fut))
        loop.call_later(0.02, stop.cancel)  # cancelled again while the remove runs
        with pytest.raises(asyncio.CancelledError):
            await stop
        # The worker finished before the cancel was passed on.
        assert fut.done()

    async def test_a_removal_that_hangs_counts_against_the_cleanup_bound(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(docker_mod, "_CANCEL_CLEANUP_S", 0.1)
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path))
        sandbox._provider = _Provider(5.0)  # type: ignore[assignment]
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with caplog.at_level(logging.WARNING, logger=docker_mod.logger.name):
            await asyncio.wait_for(sandbox._stop_run(_RunHandle(container_id="c0ffee000000aaaa"), fut), 2)
        assert loop.time() - started < 1.0
        assert "did not stop within 0.1s of cancel" in caplog.text
        fut.set_result(None)

    async def test_a_run_that_never_stops_releases_after_the_cleanup_bound(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(docker_mod, "_CANCEL_CLEANUP_S", 0.05)
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path))
        sandbox._provider = _Provider(None)  # type: ignore[assignment]
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        with caplog.at_level(logging.WARNING, logger=docker_mod.logger.name):
            await sandbox._stop_run(_RunHandle(), fut)
        assert "did not stop within 0.05s of cancel" in caplog.text
        fut.set_exception(RuntimeError("late"))  # retrieved by the callback, not logged as lost


class TestKillProcessTree:
    async def test_a_process_that_will_not_be_reaped_does_not_hang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        killed: list[int] = []
        monkeypatch.setattr(env_mod.os, "killpg", lambda pid, sig: killed.append(pid))

        async def wait() -> int:
            raise asyncio.TimeoutError

        proc = SimpleNamespace(pid=424242, wait=wait)
        await env_mod._kill_process_tree(proc)  # type: ignore[arg-type]
        assert killed == [424242]

    async def test_a_group_already_gone_is_fine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def killpg(pid: int, sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(env_mod.os, "killpg", killpg)

        async def wait() -> int:
            return 0

        await env_mod._kill_process_tree(SimpleNamespace(pid=1, wait=wait))  # type: ignore[arg-type]


class TestEnvFloatWarnsOnce:
    @pytest.mark.parametrize("raw", ["nan", "-1", "inf", "abc"])
    def test_one_warning_per_value(
        self, raw: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(settings_mod, "_warned_float_values", set())
        monkeypatch.setenv("QA_COV_SECONDS", raw)
        with caplog.at_level(logging.WARNING, logger=settings_mod.logger.name):
            assert settings_mod._env_float("QA_COV_SECONDS", 7.0) == 7.0
            assert settings_mod._env_float("QA_COV_SECONDS", 7.0) == 7.0
        assert caplog.text.count("Ignoring QA_COV_SECONDS=") == 1

    @pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), (" 2.5 ", 2.5), ("", 7.0), ("  ", 7.0)])
    def test_valid_and_blank(self, raw: str, expected: float, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QA_COV_SECONDS", raw)
        assert settings_mod._env_float("QA_COV_SECONDS", 7.0) == expected

