"""PR1-S2: the legacy ``coding_sandbox`` and ``database_sandbox`` toolsets run on the sandbox broker.

With ``SANDBOX_MODE=broker`` (what Compose, Helm and install.sh ship) the
legacy executor stack had no backend, so both toolsets were skipped and users
lost them against main (which ships docker). ``BrokerExecutor`` runs the same
commands as ``DockerExecutor`` in broker sandboxes: no network for the run, a
separate install sandbox with open egress (firewalled by the broker) for
packages, artifacts copied back from ``/output``.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from app.sandbox import manager
from app.sandbox.broker_executor import BrokerExecutor
from app.sandbox.models import SandboxLanguage, SandboxMode
from harness_core.sandbox.errors import BrokerUnavailable, PolicyUnenforceable
from harness_core.sandbox.types import EgressDefault, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FAKE_PROGRAMS, FakeSandboxBroker

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle


class RecordingBroker(FakeSandboxBroker):
    def __init__(self) -> None:
        super().__init__(clock=ManualClock())
        self.specs: list[SandboxSpec] = []
        self.runs: list[dict[str, Any]] = []

    async def acquire(self, spec: SandboxSpec, **kwargs: Any) -> FakeSandboxHandle:  # noqa: ANN401
        self.specs.append(spec)
        return await super().acquire(spec, **kwargs)


def _executor(broker: FakeSandboxBroker, tmp_path: Path) -> BrokerExecutor:
    async def source() -> FakeSandboxBroker:
        return broker

    return BrokerExecutor(broker=source, image="pipeshub/sandbox:latest", work_root=str(tmp_path))


def _sh(broker: RecordingBroker, *, stdout: bytes = b"ok\n", output: dict[str, bytes] | None = None, exit_code: int = 0):  # noqa: ANN202
    async def sh(context: FakeProcessContext) -> int:
        files = context.sandbox.files
        broker.runs.append(
            {"argv": tuple(context.argv), "env": dict(context.env), "files": {p: files[p].data for p in files}}
        )
        for path, data in (output or {}).items():
            context.write_file(path, data)
        await context.stdout.put(stdout)
        return exit_code

    return sh


class TestRuns:
    async def test_python_runs_offline_with_the_docker_command_and_brings_artifacts_back(self, tmp_path: Path) -> None:
        broker = RecordingBroker()
        broker.programs["sh"] = _sh(broker, output={"/output/chart.png": b"png"})
        result = await _executor(broker, tmp_path).execute("print(1)", SandboxLanguage.PYTHON.value)

        assert result.success and result.exit_code == 0 and result.stdout == "ok\n"
        [run] = broker.runs
        assert run["argv"] == ("sh", "-c", "python3 /src/main.py")
        assert run["files"]["/src/main.py"] == b"print(1)"
        assert run["env"]["OUTPUT_DIR"] == "/output"
        assert run["env"]["HOME"] == "/tmp"
        [spec] = broker.specs
        assert spec.egress.default is EgressDefault.DENY
        assert [m.target for m in spec.mounts] == ["/src", "/output"]
        [artifact] = result.artifacts
        assert artifact.file_name == "chart.png"
        with open(artifact.file_path, "rb") as f:
            assert f.read() == b"png"
        assert broker.sandboxes == {}

    async def test_sqlite_uses_the_same_command_as_docker(self, tmp_path: Path) -> None:
        broker = RecordingBroker()
        broker.programs["sh"] = _sh(broker, stdout=b"a,b\n1,2\n")
        result = await _executor(broker, tmp_path).execute("select 1;", SandboxLanguage.SQLITE.value)
        assert result.stdout == "a,b\n1,2\n"
        [run] = broker.runs
        assert run["argv"] == ("sh", "-c", "sqlite3 -header -csv /tmp/sandbox.db < /src/query.sql")
        assert run["files"]["/src/query.sql"] == b"select 1;"

    async def test_the_host_environment_never_reaches_the_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        broker = RecordingBroker()
        broker.programs["sh"] = _sh(broker)
        await _executor(broker, tmp_path).execute("x", "python", env={"DATABASE_URL": "postgres://x"})
        [run] = broker.runs
        assert "OPENAI_API_KEY" not in run["env"]
        assert run["env"]["DATABASE_URL"] == "postgres://x"

    async def test_a_failing_program_is_an_unsuccessful_result(self, tmp_path: Path) -> None:
        broker = RecordingBroker()
        broker.programs["sh"] = _sh(broker, exit_code=1)
        result = await _executor(broker, tmp_path).execute("x", "python")
        assert (result.success, result.exit_code) == (False, 1)

    async def test_a_timeout_matches_the_docker_executor(self, tmp_path: Path) -> None:
        broker = RecordingBroker()
        broker.programs["sh"] = FAKE_PROGRAMS["sleep"]
        result = await _executor(broker, tmp_path).execute("x", "python", timeout_seconds=0.2)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error == "Execution timed out after 0.2s"
        assert broker.sandboxes == {}


class TestPackages:
    async def test_install_runs_in_its_own_egress_sandbox_and_the_run_stays_offline(self, tmp_path: Path) -> None:
        broker = RecordingBroker()

        async def sh(context: FakeProcessContext) -> int:
            if "pip install" in context.argv[2]:
                context.write_file("/deps/pandas/__init__.py", b"")
                return 0
            broker.runs.append({"argv": tuple(context.argv), "env": dict(context.env),
                                "files": {p: f.data for p, f in context.sandbox.files.items()}})
            return 0

        broker.programs["sh"] = sh
        result = await _executor(broker, tmp_path).execute("import pandas", "python", packages=["pandas"])
        assert result.success
        install, run = broker.specs
        assert install.egress.default is EgressDefault.ALLOW
        assert [m.target for m in install.mounts] == ["/deps"]
        assert run.egress.default is EgressDefault.DENY
        assert [m.target for m in run.mounts] == ["/src", "/output", "/deps"]
        [ran] = broker.runs
        assert ran["env"]["PYTHONPATH"] == "/deps"
        assert "/deps/pandas/__init__.py" in ran["files"]

    async def test_cleanup_g6_each_sandbox_names_its_holder_and_a_lifetime_the_broker_reaps_it_by(
        self, tmp_path: Path
    ) -> None:
        """A query service that died mid-run leaves its sandboxes to the broker's lifetime sweep, not to nobody."""
        from app.sandbox.broker_executor import (
            _HOLDER,
            INSTALL_TIMEOUT_S,
            LIFETIME_GRACE_S,
        )
        from harness_core.sandbox.types import LIFETIME_LABEL

        broker = RecordingBroker()

        async def sh(context: FakeProcessContext) -> int:
            if "pip install" in context.argv[2]:
                context.write_file("/deps/pandas/__init__.py", b"")
            return 0

        broker.programs["sh"] = sh
        result = await _executor(broker, tmp_path).execute("x", "python", packages=["pandas"], timeout_seconds=90)
        assert result.success
        install, run = broker.specs
        assert install.labels == {
            "legacyExecutor": "install",
            "legacyExecutorHolder": _HOLDER,
            LIFETIME_LABEL: str(int(INSTALL_TIMEOUT_S) + LIFETIME_GRACE_S),
        }
        assert run.labels == {
            "legacyExecutor": "run",
            "legacyExecutorHolder": _HOLDER,
            LIFETIME_LABEL: str(90 + LIFETIME_GRACE_S),
        }

    async def test_a_package_off_the_allowlist_is_refused_before_any_sandbox(self, tmp_path: Path) -> None:
        broker = RecordingBroker()
        result = await _executor(broker, tmp_path).execute("x", "python", packages=["definitely-not-allowed-pkg"])
        assert result.success is False
        assert broker.specs == []

    async def test_an_open_egress_refusal_is_a_failed_result_with_its_reason(self, tmp_path: Path) -> None:
        class Refusing(RecordingBroker):
            async def acquire(self, spec: SandboxSpec, **kwargs: Any) -> FakeSandboxHandle:  # noqa: ANN401
                if spec.egress.default is EgressDefault.ALLOW:
                    raise PolicyUnenforceable("no rules", reason="egress_firewall_unavailable")
                return await super().acquire(spec, **kwargs)

        result = await _executor(Refusing(), tmp_path).execute("x", "python", packages=["pandas"])
        assert result.success is False
        assert result.error is not None and "egress_firewall_unavailable" in result.error


@dataclass
class _Touches:
    """Filesystem calls on paths under ``root`` (Python audit events: open, mkdir, rmtree, listdir, ...)."""

    root: str
    loop_thread: int
    events: list[tuple[str, bool]] = field(default_factory=list[tuple[str, bool]])

    def on_loop(self) -> list[str]:
        return [event for event, on_loop in self.events if on_loop]


_TOUCHES: _Touches | None = None


def _audit(event: str, args: tuple[object, ...]) -> None:
    touches = _TOUCHES
    if touches is None:
        return
    for arg in args:
        if isinstance(arg, (str, bytes, os.PathLike)) and os.fsdecode(arg).startswith(touches.root):  # pyright: ignore[reportUnknownArgumentType]
            touches.events.append((event, threading.get_ident() == touches.loop_thread))
            return


# Process-wide and permanent (audit hooks cannot be removed); inert unless a test sets ``_TOUCHES``.
sys.addaudithook(_audit)


@contextlib.contextmanager
def _touches_under(root: Path) -> Iterator[_Touches]:
    global _TOUCHES  # noqa: PLW0603 - the audit hook reads it
    _TOUCHES = _Touches(str(root), threading.get_ident())
    try:
        yield _TOUCHES
    finally:
        _TOUCHES = None


async def test_m7_1_the_work_folder_is_written_tarred_read_and_removed_off_the_event_loop(tmp_path: Path) -> None:
    broker = RecordingBroker()

    async def sh(context: FakeProcessContext) -> int:
        if "pip install" in context.argv[2]:
            context.write_file("/deps/pandas/__init__.py", b"")
            return 0
        context.write_file("/output/chart.png", b"png")
        return 0

    broker.programs["sh"] = sh
    with _touches_under(tmp_path) as touches:
        result = await _executor(broker, tmp_path).execute("import pandas", "python", packages=["pandas"])

    assert result.success and [a.file_name for a in result.artifacts] == ["chart.png"]
    assert touches.events, "the audit hook saw nothing under the work root"
    assert touches.on_loop() == []


async def test_an_unreachable_broker_is_a_failed_result(tmp_path: Path) -> None:
    async def source() -> FakeSandboxBroker:
        raise BrokerUnavailable("down")

    result = await BrokerExecutor(broker=source, image="pipeshub/sandbox:latest", work_root=str(tmp_path)).execute("x", "python")
    assert result.success is False
    assert result.error is not None and "sandbox broker unavailable" in result.error


class TestManager:
    def test_broker_mode_builds_the_broker_executor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "broker")
        manager.reset_executor()
        try:
            assert manager.get_sandbox_mode() is SandboxMode.BROKER
            assert isinstance(manager.get_executor(), BrokerExecutor)
        finally:
            manager.reset_executor()

    def test_the_sandbox_toolsets_are_offered_under_broker_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.agent_loop.tool_loader import _sandbox_executor_available

        monkeypatch.setenv("SANDBOX_MODE", "broker")
        assert _sandbox_executor_available() is True


class _Chat(dict):
    pass


@pytest.fixture
def broker_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RecordingBroker:
    broker = RecordingBroker()
    monkeypatch.setattr(manager, "_executor_instance", _executor(broker, tmp_path))
    return broker


def _no_upload(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:  # noqa: ANN401
    for name in ("_schedule_artifact_upload", "_schedule_csv_export"):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, lambda *a, **k: None)


class TestToolsetsOnTheBroker:
    async def test_execute_python(self, broker_executor: RecordingBroker, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        broker_executor.programs["sh"] = _sh(broker_executor, stdout=b"42\n")
        toolset = CodingSandbox(_Chat())
        _no_upload(monkeypatch, toolset)
        ok, payload = await toolset.execute_python(code="print(42)")
        assert ok, payload
        assert json.loads(payload)["stdout"].strip() == "42"
        assert broker_executor.runs[0]["argv"] == ("sh", "-c", "python3 /src/main.py")

    async def test_execute_sqlite(self, broker_executor: RecordingBroker, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        broker_executor.programs["sh"] = _sh(broker_executor, stdout=b"n\n1\n")
        toolset = DatabaseSandbox(_Chat())
        _no_upload(monkeypatch, toolset)
        ok, payload = await toolset.execute_sqlite(query="select 1 as n;")
        assert ok, payload
        assert json.loads(payload)["data"] == [{"n": "1"}]

