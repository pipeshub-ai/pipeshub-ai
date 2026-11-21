"""LG-2..LG-5: `run_code` through the sandbox broker keeps DockerCodingSandbox's behaviour (fake broker)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodingSandboxError,
    SandboxContext,
)
from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
from harness_core.sandbox.errors import IsolationUnavailable
from harness_core.sandbox.types import EgressDefault, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FAKE_PROGRAMS, FakeSandboxBroker

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.testing.sandbox.fake import (
        FakeProcessContext,
        FakeProgram,
        FakeSandboxHandle,
    )


class RecordingBroker(FakeSandboxBroker):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(clock=ManualClock(), **kwargs)  # type: ignore[arg-type]
        self.specs: list[SandboxSpec] = []
        self.seen: dict[str, object] = {}

    async def acquire(self, spec: SandboxSpec, **kwargs: object) -> FakeSandboxHandle:  # type: ignore[override]
        self.specs.append(spec)
        return await super().acquire(spec, **kwargs)  # type: ignore[arg-type]


def _sandbox(broker: FakeSandboxBroker, tmp_path: Path, **options: object) -> BrokerCodingSandbox:
    async def source() -> FakeSandboxBroker:
        return broker

    return BrokerCodingSandbox(
        broker=source,
        working_dir=str(tmp_path / "wd"),
        image="pipeshub/sandbox:latest",
        context=SandboxContext(org_id="org-1", conversation_id="conv-1"),
        **options,  # type: ignore[arg-type]
    )


def _program(broker: RecordingBroker) -> FakeProgram:
    async def sh(context: FakeProcessContext) -> int:
        files = context.sandbox.files
        broker.seen.update(
            argv=context.argv,
            env=context.env,
            entry=files["/src/main.py"].data,
            staged_mode=files.get("/src/input/artifacts/a.csv") and files["/src/input/artifacts/a.csv"].mode,
        )
        context.write_file("/output/report.txt", b"report")
        await context.stdout.put(b"ran\n")
        await context.stderr.put(b"warn\n")
        return 0

    return sh


async def test_a_run_matches_the_docker_backend(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.programs["sh"] = _program(broker)
    sandbox = _sandbox(broker, tmp_path)
    await sandbox.upload_file("input/artifacts/a.csv", b"x,y\n")

    result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

    assert (result.stdout, result.stderr, result.exit_code) == ("ran\n", "warn\n", 0)
    assert result.artifacts == ["output/report.txt"]
    assert (tmp_path / "wd" / "output" / "report.txt").read_bytes() == b"report"
    assert broker.seen["argv"] == ["sh", "-c", "python3 main.py"]
    assert broker.seen["entry"] == b"print(1)"
    assert broker.seen["staged_mode"] == 0o444
    env = broker.seen["env"]
    assert isinstance(env, dict)
    assert env["OUTPUT_DIR"] == "/output"
    assert env["HOME"] == "/tmp"
    [spec] = broker.specs
    assert [m.target for m in spec.mounts] == ["/src", "/output"]
    assert spec.egress.default is EgressDefault.DENY
    assert spec.labels == {"codingSandboxId": sandbox.sandbox_id, "orgId": "org-1", "conversationId": "conv-1"}
    assert broker.sandboxes == {}


async def test_a_timeout_is_exit_minus_one_with_the_legacy_message(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.programs["sh"] = FAKE_PROGRAMS["sleep"]
    result = await _sandbox(broker, tmp_path).execute(CodeRequest(code="x", language="python", timeout=0.2))
    assert result.exit_code == -1
    assert result.stderr.startswith("Execution timed out after 0.2s")
    assert result.error_analysis is not None
    assert broker.sandboxes == {}


async def test_network_follows_the_backend_and_the_request(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.programs["sh"] = _program(broker)
    sandbox = _sandbox(broker, tmp_path, allow_network=True)
    await sandbox.execute(CodeRequest(code="x", language="python", allow_network=True))
    await sandbox.execute(CodeRequest(code="x", language="python", allow_network=False))
    assert [s.egress.default for s in broker.specs] == [EgressDefault.ALLOW, EgressDefault.DENY]


async def test_install_runs_with_egress_and_brings_deps_back(tmp_path: Path) -> None:
    broker = RecordingBroker()

    async def pip(context: FakeProcessContext) -> int:
        context.write_file("/deps/pkg/__init__.py", b"")
        return 0

    broker.programs["sh"] = pip
    sandbox = _sandbox(broker, tmp_path)
    result = await sandbox.install_packages(["requests"], "python")
    assert result.success
    [spec] = broker.specs
    assert spec.egress.default is EgressDefault.ALLOW
    assert [m.target for m in spec.mounts] == ["/deps"]
    assert (tmp_path / "wd" / "deps_python" / "pkg" / "__init__.py").exists()
    assert broker.sandboxes == {}


async def test_a_failed_install_is_data_and_releases(tmp_path: Path) -> None:
    broker = RecordingBroker()

    async def failing(context: FakeProcessContext) -> int:
        await context.stderr.put(b"no such package")
        return 1

    broker.programs["sh"] = failing
    result = await _sandbox(broker, tmp_path).install_packages(["requests"], "python")
    assert (result.success, result.stderr) == (False, "no such package")
    assert broker.sandboxes == {}


async def test_a_broker_refusal_is_an_infrastructure_error(tmp_path: Path) -> None:
    broker = RecordingBroker()

    async def refuse(*_: object, **__: object) -> None:
        raise IsolationUnavailable("no gvisor", reason="mode_unavailable")

    broker.acquire = refuse  # type: ignore[method-assign]
    with pytest.raises(CodingSandboxError):
        await _sandbox(broker, tmp_path).execute(CodeRequest(code="x", language="python"))


async def test_sandbox_mode_broker_builds_the_broker_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_loop_lib.sandbox.coding.settings import EnvSandboxSettingsLoader

    monkeypatch.setenv("SANDBOX_MODE", "broker")
    monkeypatch.setenv("SANDBOX_DOCKER_IMAGE", "pipeshub/sandbox:test")
    settings = await EnvSandboxSettingsLoader().load(SandboxContext())
    assert settings.backend == "broker"
    assert settings.backend_options["broker"]["image"] == "pipeshub/sandbox:test"


async def test_the_bridge_builds_a_broker_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxType
    from app.agents.agent_loop.sandbox_bridge import build_coding_sandbox_manager

    monkeypatch.setenv("SANDBOX_MODE", "broker")
    manager = await build_coding_sandbox_manager()
    sandbox_id, backend = await asyncio.wait_for(manager.get_or_create(SandboxType.CODING), 30)
    assert isinstance(backend, BrokerCodingSandbox)
    assert sandbox_id == backend.sandbox_id
    await manager.destroy_all()


async def test_x1_8_a_hundred_runs_of_every_ending_leave_no_broker_sandbox(tmp_path: Path) -> None:
    """X1-8: success, failure, a program that raises, a timeout and a cancellation mid-run: none leaks."""
    broker = RecordingBroker()
    started = asyncio.Event()

    async def ok(context: FakeProcessContext) -> int:
        await context.stdout.put(b"ok")
        return 0

    async def failing(context: FakeProcessContext) -> int:
        return 3

    async def crashing(context: FakeProcessContext) -> int:
        raise RuntimeError("the program crashed")

    async def hanging(context: FakeProcessContext) -> int:
        started.set()
        _ = await asyncio.Event().wait()
        return 0

    sandbox = _sandbox(broker, tmp_path)
    endings = [ok, failing, crashing, hanging, hanging]
    for n in range(100):
        program = endings[n % len(endings)]
        broker.programs["sh"] = program
        started.clear()
        request = CodeRequest(code="x", language="python", timeout=0.05 if n % 5 == 3 else 30)
        if n % 5 == 4:
            task = asyncio.create_task(sandbox.execute(request))
            _ = await asyncio.wait_for(started.wait(), 5)
            _ = task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            _ = await sandbox.execute(request)
        assert broker.sandboxes == {}, f"run {n} ({program.__name__}) left a sandbox"
        assert broker.ledger.active() == 0


async def test_rc5_a_durable_runs_sandbox_carries_its_run_and_attempt(tmp_path: Path) -> None:
    """P5.11 RC-5: the run sweeper reaps by these labels; a legacy run (above) has none of them."""
    broker = RecordingBroker()
    broker.programs["sh"] = _program(broker)

    async def source() -> FakeSandboxBroker:
        return broker

    sandbox = BrokerCodingSandbox(
        broker=source,
        working_dir=str(tmp_path / "wd"),
        image="pipeshub/sandbox:latest",
        context=SandboxContext(org_id="org-1", conversation_id="conv-1", run_id="run-1", attempt_id="att-2"),
    )
    _ = await sandbox.execute(CodeRequest(code="print(1)", language="python"))
    [spec] = broker.specs
    assert spec.labels == {
        "codingSandboxId": sandbox.sandbox_id,
        "orgId": "org-1",
        "conversationId": "conv-1",
        "runId": "run-1",
        "attemptId": "att-2",
    }


async def test_rc5_the_bridge_passes_the_run_ids_to_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxType
    from app.agents.agent_loop.sandbox_bridge import build_coding_sandbox_manager

    monkeypatch.setenv("SANDBOX_MODE", "broker")
    manager = await build_coding_sandbox_manager(allow_network=False, run_id="run-1", attempt_id="att-2")
    _sandbox_id, backend = await manager.get_or_create(SandboxType.CODING, None)
    context = backend._context  # pyright: ignore[reportAttributeAccessIssue]
    assert (context.run_id, context.attempt_id) == ("run-1", "att-2")
    legacy = await build_coding_sandbox_manager(allow_network=False)
    _sandbox_id, plain = await legacy.get_or_create(SandboxType.CODING, None)
    assert (plain._context.run_id, plain._context.attempt_id) == (None, None)  # pyright: ignore[reportAttributeAccessIssue]
    await manager.destroy_all()
    await legacy.destroy_all()
