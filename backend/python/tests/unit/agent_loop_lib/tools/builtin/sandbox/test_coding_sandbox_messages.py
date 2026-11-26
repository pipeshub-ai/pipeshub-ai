"""What a sandbox call tells the model when it is stopped or refused a slot.

A call stopped while it still waits for a slot never ran, so it must not
say that files may be incomplete. A call refused a per-call slot gains
nothing from reusing a sandbox, so it must not be told to; a call refused
a new sandbox of a backend that keeps its slot for life still is.
"""

from __future__ import annotations

import asyncio
import uuid

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    InstallResult,
)
from app.agent_loop_lib.sandbox.governor import GovernorLimits, SandboxResourceGovernor
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    InstallPackagesTool,
    sandbox_limit_guidance,
    stopped_output,
)

_REUSE_ADVICE = "Reuse an existing sandbox_id"


class _Backend:
    """Not metered: takes a slot per call. `execute` and `install_packages`
    block until cancelled when `block` is set."""

    metered = False

    def __init__(self, block: bool = True) -> None:
        self.sandbox_id = f"fake-{uuid.uuid4()}"
        self.block = block
        self.started = asyncio.Event()

        class _Caps:
            is_metered = type(self).metered

        self.capabilities = _Caps()

    async def provision(self) -> None:
        return None

    async def upload_file(self, path: str, content: bytes) -> None:
        return None

    async def _run(self) -> None:
        self.started.set()
        if self.block:
            await asyncio.sleep(60)

    async def execute(self, request: CodeRequest) -> CodeResult:
        await self._run()
        return CodeResult(stdout="ok", stderr="", exit_code=0, language=request.language, duration_ms=1.0)

    async def install_packages(self, packages: list[str], language: str) -> InstallResult:
        await self._run()
        return InstallResult(success=True, installed=list(packages))

    async def destroy(self) -> None:
        return None


class _MeteredBackend(_Backend):
    metered = True


def _manager(backend_cls: type = _Backend, *, block: bool = True, slot_wait_s: float = 5) -> tuple:
    token = CancellationToken()
    gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
    backends: list[_Backend] = []

    def make() -> _Backend:
        backends.append(backend_cls(block=block))
        return backends[-1]

    manager = SandboxManager(governor=gov, stop_token=token, slot_wait_s=slot_wait_s)
    manager.register_backend_factory(SandboxType.CODING, make)
    return manager, token, backends


async def _until(condition) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


class TestStoppedMessages:
    def test_wording(self) -> None:
        assert stopped_output().error.endswith("files it was writing may be incomplete.")
        assert stopped_output(started=False).error == (
            "Stopped by the user before this call started. Nothing ran."
        )

    async def test_stop_while_waiting_for_a_slot_says_nothing_ran(self) -> None:
        manager, token, backends = _manager()
        tool = CodingSandboxTool(manager)
        holder = asyncio.ensure_future(tool.execute(code="while True: pass", language="python"))
        await _until(lambda: backends and backends[0].started.is_set())
        waiter = asyncio.ensure_future(tool.execute(code="print(1)", language="python"))
        await _until(lambda: len(backends) == 2)
        await asyncio.sleep(0.05)
        token.cancel()
        held, waited = await asyncio.gather(holder, waiter)

        assert "may be incomplete" in held.error
        assert waited.error == "Stopped by the user before this call started. Nothing ran."
        assert not backends[1].started.is_set()

    async def test_install_stopped_while_waiting_for_a_slot_says_nothing_ran(self) -> None:
        manager, token, backends = _manager()
        run = CodingSandboxTool(manager)
        install = InstallPackagesTool(manager)
        holder = asyncio.ensure_future(run.execute(code="while True: pass", language="python"))
        await _until(lambda: backends and backends[0].started.is_set())
        waiter = asyncio.ensure_future(install.execute(packages=["pandas"], language="python"))
        await _until(lambda: len(backends) == 2)
        await asyncio.sleep(0.05)
        token.cancel()
        _held, waited = await asyncio.gather(holder, waiter)

        assert waited.error == "Stopped by the user before this call started. Nothing ran."

    async def test_stop_before_the_sandbox_exists_says_nothing_ran(self) -> None:
        manager, token, _backends = _manager()
        token.cancel()
        result = await CodingSandboxTool(manager).execute(code="print(1)", language="python")
        assert result.error == "Stopped by the user before this call started. Nothing ran."


class TestRefusalAdvice:
    def test_wording(self) -> None:
        error = RuntimeError("the only code sandbox on this server is busy (1/1)")
        assert _REUSE_ADVICE in sandbox_limit_guidance(error)
        per_call = sandbox_limit_guidance(error, reuse_helps=False)
        assert _REUSE_ADVICE not in per_call
        assert per_call.endswith(
            "Every call needs a free slot while it runs, even on an existing sandbox, "
            "so retry this call shortly and run one call at a time."
        )

    async def test_a_per_call_slot_refusal_does_not_suggest_reusing_a_sandbox(self) -> None:
        manager, _token, backends = _manager(slot_wait_s=0.05)
        tool = CodingSandboxTool(manager)
        holder = asyncio.ensure_future(tool.execute(code="while True: pass", language="python"))
        await _until(lambda: backends and backends[0].started.is_set())
        refused = await tool.execute(code="print(1)", language="python")
        holder.cancel()

        assert refused.success is False
        assert "the only code sandbox on this server is busy" in refused.error
        assert _REUSE_ADVICE not in refused.error
        assert "even on an existing sandbox" in refused.error

    async def test_a_refused_new_sandbox_that_holds_its_slot_for_life_suggests_reuse(self) -> None:
        manager, _token, _backends = _manager(_MeteredBackend, block=False, slot_wait_s=0.05)
        tool = CodingSandboxTool(manager)
        first = await tool.execute(code="print(1)", language="python")
        assert first.success is True
        refused = await tool.execute(code="print(2)", language="python")

        assert refused.success is False
        assert _REUSE_ADVICE in refused.error
