"""R33JGAPS-FU-2: code the sandbox's memory limit killed is named as out of memory on every path that runs code.

* OOM-R34-1: a person's ``/run`` launcher reports a line a signal ended as a shell does (128 + the signal), so a
  SIGKILL is 137, the exit every container backend reads with the sandbox's own OOM report; Python's own exit for it
  was 247, which no backend could read as the memory limit and the answer said "Exit code 247."
* OOM-R34-2: the ``/run`` answer names the limit from run_code's result.
* OOM-R34-3: the one reading (``memory_limit_end``): exit 137 with the sandbox's report is out of memory; a kill
  without the report, or another exit, is the code's own failure.
* OOM-R34-4: the Docker-socket backend reads its container's ``OOMKilled`` as the broker backend does, and its
  ``run_code`` result names the limit.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.coding.base import (
    OUT_OF_MEMORY_EXIT,
    CodeOutOfMemory,
    CodeRequest,
    ErrorCategory,
    SandboxContext,
    memory_limit_end,
)
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.harness.assembly.command_run import CALL_ID, launcher, summary
from harness_core.kernel.durable.ports import TurnResult

if TYPE_CHECKING:
    from pathlib import Path


def test_oom_r34_1_the_launcher_reports_a_killed_line_as_a_shell_does(tmp_path: Path) -> None:
    killed = subprocess.run([sys.executable, "-c", launcher("kill -9 $$")], cwd=tmp_path, check=False)
    assert killed.returncode == OUT_OF_MEMORY_EXIT
    failed = subprocess.run([sys.executable, "-c", launcher("exit 3")], cwd=tmp_path, check=False)
    assert failed.returncode == 3


def test_oom_r34_2_the_run_answer_names_the_memory_limit() -> None:
    result = CodeOutOfMemory(512, stderr="Killed").result("python", 10.0)
    content = {**result.model_dump(), "sandbox_id": "s1"}
    answer = summary([TurnResult("run_code", CALL_ID, "ok", content)])
    assert answer == "The command ran out of memory (limit 512 MB) and was stopped."
    assert summary([TurnResult("run_code", CALL_ID, "ok", {"exit_code": 137})]) == "Exit code 137."


@pytest.mark.parametrize(
    ("exit_code", "oom_killed", "named"),
    [(OUT_OF_MEMORY_EXIT, True, True), (OUT_OF_MEMORY_EXIT, False, False), (1, True, False), (0, False, False)],
)
def test_oom_r34_3_one_reading_of_a_runs_end(exit_code: int, oom_killed: bool, named: bool) -> None:
    end = memory_limit_end(exit_code, oom_killed=oom_killed, memory_mb=1024, stdout="o", stderr="Killed")
    assert (end is not None) is named
    if end is not None:
        assert (end.memory_mb, end.stdout, end.stderr) == (1024, "o", "Killed")
        assert str(end) == "The code ran out of memory (limit 1 GB) and was stopped."


class _Container:
    """A docker SDK container whose program the kernel killed; ``oom`` is what the daemon's ``State`` says."""

    def __init__(self, *, oom: bool) -> None:
        self.id = "c" * 64
        self.attrs: dict[str, Any] = {}
        self._oom = oom

    def put_archive(self, *_: object) -> bool:
        return True

    def start(self) -> None:
        return None

    def wait(self, timeout: float) -> dict[str, int]:
        return {"StatusCode": OUT_OF_MEMORY_EXIT}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        return b"Killed\n" if stderr else b""

    def reload(self) -> None:
        self.attrs = {"State": {"OOMKilled": self._oom}}

    def get_archive(self, path: str) -> Any:  # noqa: ANN401
        raise FileNotFoundError(path)

    def remove(self, *, force: bool) -> None:
        return None


@pytest.mark.parametrize("oom", [True, False])
async def test_oom_r34_4_the_docker_socket_backend_names_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, oom: bool,
) -> None:
    import app.agent_loop_lib.sandbox.coding.docker as docker_backend

    sandbox = DockerCodingSandbox(
        working_dir=str(tmp_path / "wd"), image="pipeshub/sandbox:latest",
        context=SandboxContext(org_id="org-1", conversation_id="conv-1"), memory_limit_mb=2048,
    )
    container = _Container(oom=oom)
    monkeypatch.setattr(docker_backend, "create_sandbox_container", lambda _client, **_kw: container)
    monkeypatch.setattr(docker_backend, "_extract_container_dir", lambda *_a: None)

    async def prepare(**_kw: object) -> None:
        return None

    async def blocking(fn: Any, *args: object) -> Any:  # noqa: ANN401
        return fn(*args)

    monkeypatch.setattr(sandbox, "_prepare_container", prepare)
    monkeypatch.setattr(sandbox._provider, "run_blocking", blocking)
    monkeypatch.setattr(sandbox._provider, "_client", SimpleNamespace(), raising=False)
    result = await sandbox.execute(CodeRequest(code="b = []\nwhile True: b.append(b'x' * 2**26)", language="python"))
    assert result.exit_code == OUT_OF_MEMORY_EXIT
    if oom:
        assert result.out_of_memory_mb == 2048
        assert "ran out of memory (limit 2 GB)" in result.stderr
        assert result.error_analysis is not None and result.error_analysis.category == ErrorCategory.MEMORY
    else:
        assert result.out_of_memory_mb is None
        assert "ran out of memory" not in result.stderr
