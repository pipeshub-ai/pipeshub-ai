"""The disk watch fails closed, and sandbox container logs stay bounded.

Where the Docker daemon ignores the per-run disk quota, the app polls each
run's writable-layer size. A run whose size cannot be read (the reads fail,
hang, or answer without a size) must be stopped too, or a run could make
the reads slow on purpose and write unchecked. The other Docker runner
(``app.sandbox.docker_executor``) gets the same watch. Container logs use a
rotating driver so a run that prints without end cannot fill the daemon's
disk outside the quota.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, NoReturn
from unittest.mock import MagicMock, patch

import pytest

import app.agent_loop_lib.sandbox.coding.docker as docker_mod
import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.sandbox.docker_executor import DockerExecutor

from .test_docker_coding_sandbox import (  # noqa: F401  (fixtures)
    _fake_docker_module,
    _fresh_docker_provider,
    _make_tar,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app.sandbox.models import ExecutionResult

MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "_disk_watch_limit", None)
    monkeypatch.setattr(docker_mod, "_DISK_WATCH_INTERVAL_S", 0.02)
    monkeypatch.setattr(docker_mod, "_log_config_level", 0)
    monkeypatch.setattr(ef, "_storage_opt_usable", None)
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "1m")
    monkeypatch.setenv("SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S", "0.3")
    monkeypatch.delenv("SANDBOX_DISK_WATCHDOG", raising=False)
    monkeypatch.delenv("SANDBOX_DISK_WATCH_MAX_FAILURES", raising=False)
    monkeypatch.delenv("SANDBOX_LOG_MAX_SIZE", raising=False)
    monkeypatch.delenv("SANDBOX_LOG_MAX_FILES", raising=False)


def _client(read: Callable[[], Any]) -> tuple[MagicMock, MagicMock, threading.Event]:
    """A client on a daemon that ignores the quota, whose run container only
    exits once killed (or after 3 s). ``read()`` answers each size check:
    it returns the inspect body or raises."""
    killed = threading.Event()
    container = MagicMock()
    container.id = "c0ffee" * 10

    def _wait(timeout: float | None = None) -> dict:
        return {"StatusCode": 137} if killed.wait(3) else {"StatusCode": 0}

    container.wait.side_effect = _wait
    container.logs.side_effect = lambda **kw: b""
    container.get_archive.side_effect = lambda path: (iter([_make_tar({})]), {})

    client = MagicMock()
    client.containers.create.return_value = container
    client.images.get.return_value = MagicMock()
    client.info.return_value = {"Driver": "overlayfs"}
    client.api._get.side_effect = lambda *a, **kw: read()
    client.api._result.side_effect = lambda res, _json: res
    client.api.kill.side_effect = lambda _id: killed.set()
    return client, container, killed


def _failing() -> NoReturn:
    raise ConnectionError("daemon unreachable")


class TestTheWatchFailsClosed:
    async def test_size_reads_that_keep_failing_stop_the_run(self, tmp_path) -> None:
        client, _c, killed = _client(_failing)
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))

        assert killed.is_set()
        assert result.success is False
        assert "could not check this run's disk use" in result.stderr
        assert result.error_analysis is not None
        assert "disk" in result.error_analysis.message

    async def test_an_answer_without_a_size_is_a_failed_check(self, tmp_path) -> None:
        client, _c, killed = _client(lambda: {})
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))

        assert killed.is_set()
        assert "could not check this run's disk use" in result.stderr

    async def test_a_size_the_daemon_could_not_measure_is_a_failed_check(self, tmp_path) -> None:
        # Docker reports SizeRw=-1 when its storage driver cannot measure the layer.
        client, _c, killed = _client(lambda: {"SizeRw": -1})
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))

        assert killed.is_set()
        assert "could not check this run's disk use" in result.stderr

    def test_an_empty_writable_layer_is_a_good_reading(self) -> None:
        client, _c, _killed = _client(lambda: {"SizeRw": 0})
        assert docker_mod._container_size_rw(client, "c0ffee") == 0

    async def test_a_check_slower_than_the_timeout_stops_the_run_at_once(self, tmp_path) -> None:
        release = threading.Event()

        def _hang() -> dict:
            release.wait(10)
            return {"SizeRw": 0}

        client, _c, killed = _client(_hang)
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        try:
            with patch("docker.from_env", return_value=client):
                result = await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))
        finally:
            release.set()

        assert killed.is_set()
        assert "could not check this run's disk use" in result.stderr

    async def test_each_check_is_given_the_timeout(self, tmp_path) -> None:
        client, _c, _k = _client(lambda: {"SizeRw": 5 * MiB})
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))

        assert client.api._get.call_args.kwargs["timeout"] == pytest.approx(0.3)

    def test_one_failed_check_between_good_ones_does_not_stop_the_run(self) -> None:
        answers = iter([{"SizeRw": 10}, ConnectionError("blip"), {"SizeRw": 20}, ConnectionError("blip")]
                       + [{"SizeRw": 30}] * 1000)

        def _read() -> dict:
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        client, _c, killed = _client(_read)
        watch = docker_mod._DiskWatch.start(client, "c" * 64, MiB)
        assert watch is not None
        try:
            assert not killed.wait(0.4)
        finally:
            watch.stop()
        assert watch.unchecked_reason is None
        assert watch.exceeded_bytes is None

    def test_the_failure_count_is_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_DISK_WATCH_MAX_FAILURES", "1")
        client, _c, killed = _client(_failing)
        watch = docker_mod._DiskWatch.start(client, "c" * 64, MiB)
        assert watch is not None
        assert killed.wait(1)
        watch.stop()
        assert "1 size checks in a row failed" in (watch.unchecked_reason or "")

    async def test_an_install_whose_size_cannot_be_read_is_stopped(self, tmp_path) -> None:
        client, _c, killed = _client(_failing)
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))

        code, _out, err = sandbox._install_in_container(
            client, "egress", "/deps", str(tmp_path / "deps"), ["mpmath"], {"command": ["true"]},
        )

        assert killed.is_set()
        assert code != 0
        assert "Package installation was stopped because the server could not check" in err


class TestTheOtherDockerRunnerIsWatched:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch) -> DockerExecutor:
        monkeypatch.setattr("app.sandbox.docker_executor._sandbox_root", lambda: str(tmp_path / "root"))
        return DockerExecutor()

    async def _run(self, executor: DockerExecutor, client: MagicMock, tmp_path: Path) -> ExecutionResult:
        work = tmp_path / "work"
        (work / "src").mkdir(parents=True)
        (work / "output").mkdir()
        with patch("docker.from_env", return_value=client):
            return await executor._run_container(
                image="img", command=["sh", "-c", "true"], work_dir=str(work),
                src_dir=str(work / "src"), output_dir=str(work / "output"),
                env={"OUTPUT_DIR": "/output"}, timeout=30,
            )

    async def test_a_run_past_the_quota_is_stopped(self, executor, tmp_path) -> None:
        client, _c, killed = _client(lambda: {"SizeRw": 5 * MiB})
        result = await self._run(executor, client, tmp_path)

        assert killed.is_set()
        assert result.success is False
        assert "wrote more than 1m" in (result.error or "")

    async def test_a_run_whose_size_cannot_be_read_is_stopped(self, executor, tmp_path) -> None:
        client, _c, killed = _client(_failing)
        result = await self._run(executor, client, tmp_path)

        assert killed.is_set()
        assert result.success is False
        assert "could not check this run's disk use" in (result.error or "")

    async def test_a_run_under_the_quota_is_left_alone(self, executor, tmp_path) -> None:
        client, container, killed = _client(lambda: {"SizeRw": 100})
        container.wait.side_effect = lambda timeout=None: {"StatusCode": 0}
        result = await self._run(executor, client, tmp_path)

        assert not killed.is_set()
        assert result.success is True

    def test_an_install_past_the_quota_is_stopped(self, executor) -> None:
        client, _c, killed = _client(lambda: {"SizeRw": 5 * MiB})
        from app.sandbox.models import SandboxLanguage

        with pytest.raises(RuntimeError, match="Package installation was stopped"):
            executor._install_once(
                client, "egress", SandboxLanguage.PYTHON, "/deps", 30, {"command": ["true"]}, "t" * 32,
            )
        assert killed.is_set()

    async def test_its_containers_get_a_rotating_log(self, executor, tmp_path) -> None:
        client, container, _k = _client(lambda: {"SizeRw": 1})
        container.wait.side_effect = lambda timeout=None: {"StatusCode": 0}
        await self._run(executor, client, tmp_path)

        log_config = client.containers.create.call_args.kwargs["log_config"]
        assert log_config["type"] == "local"
        assert log_config["config"] == {"max-size": "10m", "max-file": "3"}


class TestContainerLogsAreBounded:
    @pytest.fixture(autouse=True)
    def _no_quota(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")

    async def test_run_containers_use_a_rotating_log_driver(self, tmp_path) -> None:
        client, container, _k = _client(lambda: {"SizeRw": 1})
        container.wait.side_effect = lambda timeout=None: {"StatusCode": 0}
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        log_config = client.containers.create.call_args.kwargs["log_config"]
        assert log_config == {"type": "local", "config": {"max-size": "10m", "max-file": "3"}}

    def test_the_rotation_is_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_LOG_MAX_SIZE", "2m")
        monkeypatch.setenv("SANDBOX_LOG_MAX_FILES", "2")
        client = MagicMock()
        docker_mod.create_bounded_container(client, image="img")
        assert client.containers.create.call_args.kwargs["log_config"]["config"] == {
            "max-size": "2m", "max-file": "2",
        }

    def test_refused_rotation_options_fall_back_to_the_driver_once(self, caplog) -> None:
        client = MagicMock()
        ok = MagicMock()

        def _create(**kwargs: object) -> MagicMock:
            if kwargs.get("log_config", {}).get("config"):
                raise RuntimeError('403 Forbidden ("HostConfig.LogConfig.Config is not permitted")')
            return ok

        client.containers.create.side_effect = _create
        with caplog.at_level(logging.INFO, logger=docker_mod.__name__):
            assert docker_mod.create_bounded_container(client, image="img") is ok
            assert docker_mod.create_bounded_container(client, image="img") is ok

        calls = [c.kwargs.get("log_config") for c in client.containers.create.call_args_list]
        assert calls[1] == {"type": "local", "config": {}}
        # The second container goes straight to what works.
        assert calls[2] == {"type": "local", "config": {}}
        assert len(calls) == 3
        assert sum("log rotation options were refused" in r.getMessage() for r in caplog.records) == 1

    def test_a_refused_driver_falls_back_to_no_log_config(self, caplog) -> None:
        client = MagicMock()
        ok = MagicMock()

        def _create(**kwargs: object) -> MagicMock:
            if "log_config" in kwargs:
                raise RuntimeError("log driver 'local' is not permitted")
            return ok

        client.containers.create.side_effect = _create
        with caplog.at_level(logging.WARNING, logger=docker_mod.__name__):
            assert docker_mod.create_bounded_container(client, image="img") is ok
        assert "log_config" not in client.containers.create.call_args.kwargs
        assert any("may not rotate" in r.getMessage() for r in caplog.records)

    def test_other_create_errors_are_not_swallowed(self) -> None:
        client = MagicMock()
        client.containers.create.side_effect = RuntimeError("no such image")
        with pytest.raises(RuntimeError, match="no such image"):
            docker_mod.create_bounded_container(client, image="img")
        assert client.containers.create.call_count == 1

