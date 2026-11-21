"""R32SECCOMP-FU-1 and FU-4: an out-of-memory end reads plainly with its limit, wherever it shows.

* OM-1: ``run_code``'s program killed by the memory limit (runc: exit 137, the container's OOMKilled) is a failed run
  whose words, analysis and step summary name the limit; the tool still answers (no tool failure, no run end).
* OM-2: under gVisor the whole sandbox ends, which reads as the sandbox gone: asked, it is the same failed run.
* OM-3: a kill or a loss the memory limit did not cause stays what it was.
* OM-4: a coding-engine job that ran out of memory: its step line names the limit, as its card does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.agent_loop_lib.sandbox.coding.base import (
    OUT_OF_MEMORY_EXIT,
    CodeRequest,
    ErrorCategory,
    SandboxContext,
)
from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
from harness_core.sandbox.errors import SandboxError, memory_limit_text
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from harness_core.testing.sandbox.fake import FakeProcessContext


def _sandbox(broker: FakeSandboxBroker, tmp_path: Path) -> BrokerCodingSandbox:
    async def source() -> FakeSandboxBroker:
        return broker

    return BrokerCodingSandbox(
        broker=source,
        working_dir=str(tmp_path / "wd"),
        image="pipeshub/sandbox:latest",
        context=SandboxContext(org_id="org-1", conversation_id="conv-1"),
        memory_limit_mb=4096,
    )


def _killed(*, by_memory: bool) -> Any:  # noqa: ANN401 - a FakeProgram
    async def sh(context: FakeProcessContext) -> int:
        context.write_file("/output/partial.csv", b"a,b\n")
        await context.stdout.put(b"loading\n")
        context.sandbox.out_of_memory = by_memory
        return OUT_OF_MEMORY_EXIT

    return sh


def test_om_0_a_limit_reads_as_a_person_reads_it() -> None:
    assert (memory_limit_text(4096), memory_limit_text(1536)) == ("4 GB", "1536 MB")


async def test_om_1_runc_a_program_the_memory_limit_killed_is_a_failed_run_naming_the_limit(tmp_path: Path) -> None:
    from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
        CodingSandboxTool,
    )

    broker = FakeSandboxBroker(clock=ManualClock())
    broker.programs["sh"] = _killed(by_memory=True)
    result = await _sandbox(broker, tmp_path).execute(CodeRequest(code="x = [0] * 10**12", language="python"))

    assert result.exit_code == OUT_OF_MEMORY_EXIT and result.out_of_memory_mb == 4096
    assert result.stdout == "loading\n"
    assert result.stderr.startswith("The code ran out of memory (limit 4 GB) and was stopped.")
    assert result.error_analysis is not None and result.error_analysis.category is ErrorCategory.MEMORY
    assert "smaller pieces" in (result.error_analysis.suggestion or "")
    # What it wrote before the limit is kept, as any failed run's is.
    assert result.artifacts == ["output/partial.csv"]

    class _Result:
        content = result.model_dump()

    tool = CodingSandboxTool.__new__(CodingSandboxTool)
    assert tool.summarize_result({}, cast("Any", _Result())) == "Ran out of memory (limit 4 GB)"
    assert tool.result_failed({}, cast("Any", _Result()))


async def test_om_2_gvisor_the_sandbox_gone_by_its_memory_limit_is_the_same_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_loop_lib.sandbox.coding.broker as module

    async def gone(handle: Any, *_: object) -> tuple[int, str, str]:  # noqa: ANN401
        cast("Any", handle)._state.out_of_memory = True
        raise SandboxError("sandbox is gone")

    monkeypatch.setattr(module, "run_command", gone)
    result = await _sandbox(FakeSandboxBroker(clock=ManualClock()), tmp_path).execute(CodeRequest(code="x", language="python"))
    assert result.exit_code == OUT_OF_MEMORY_EXIT and result.out_of_memory_mb == 4096
    assert result.stderr == "The code ran out of memory (limit 4 GB) and was stopped."


async def test_om_3_a_kill_or_a_loss_the_limit_did_not_cause_stays_what_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_loop_lib.sandbox.coding.broker as module

    broker = FakeSandboxBroker(clock=ManualClock())
    broker.programs["sh"] = _killed(by_memory=False)
    killed = await _sandbox(broker, tmp_path).execute(CodeRequest(code="x", language="python"))
    assert killed.exit_code == OUT_OF_MEMORY_EXIT and killed.out_of_memory_mb is None
    assert "ran out of memory" not in killed.stderr

    async def gone(*_: object) -> tuple[int, str, str]:
        raise SandboxError("sandbox is gone")

    monkeypatch.setattr(module, "run_command", gone)
    lost = await _sandbox(FakeSandboxBroker(clock=ManualClock()), tmp_path).execute(CodeRequest(code="x", language="python"))
    assert lost.out_of_memory_mb is None and "ran out of memory" not in lost.stderr


async def test_om_4_a_job_that_ran_out_of_memory_says_its_limit_on_its_step() -> None:
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    tools = CodeTaskDelegation()
    document = {"error": "out_of_memory", "memoryLimitMb": 4096, "output": "", "success": False}
    outcome = await tools.outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    frame = next(e for e in outcome.events if e.type == RESULT_PRESENTED)
    assert cast("dict[str, Any]", frame.data)["resultSummary"] == (
        "The coding engine ran out of memory (limit 4 GB) and was stopped."
    )
    other = await tools.outcome(ToolCall(id="c2", name="code_task", arguments={}),
                                {"error": "crash_loop", "output": "", "success": False})
    assert cast("dict[str, Any]", next(e for e in other.events if e.type == RESULT_PRESENTED).data)["resultSummary"] == (
        "The coding engine stopped with an error before it finished."
    )
