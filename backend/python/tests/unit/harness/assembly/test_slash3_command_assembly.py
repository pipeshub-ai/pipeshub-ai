"""SLASH3 SB3a through the real ``PipesHubRuntimeAssembler`` and ``RunDriver``: a ``/run`` run's ``run_code`` runs on
the local backend (the ``host`` test sandbox), with no model call; and the flagged risk, workspace path parity.

The durable loop's ``run_code`` used to see none of the session workspace: what it wrote was lost, and an engine's
files were not there. Now each call gets the head under ``workspace/`` and saves what it left there as a revision,
so a file ``/run`` writes as ``out.txt`` is the manifest path an engine materialises at ``/workspace/out.txt``.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.run_code_workspace import RunCodeWorkspace
from app.harness.contracts.state import RunSandboxCommand
from harness_core.driver import RunDriver
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.failpoints import registry as failpoints
from harness_core.testing import ManualClock, ModelScript, ScriptedModel
from harness_core.testing.contracts.workspace_sync import acquire, commit
from harness_core.testing.durable import ScriptedModelPort
from harness_core.workspace.sync import TAR
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.unit.harness_core.workspace.sync.support import SESSION, FakeSyncHarness

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.assembly.context import RunContext
    from app.harness.journal_node import NodeClaim

LINE = "cat data.csv && echo hi > out.txt && mkdir -p .claude && echo x > .claude/settings.json"


class _Died(BaseException):
    """A worker killed mid-call: not an ``Exception``, so no handler runs."""


@pytest.fixture
def executions() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def _deployment(executions: list[str]) -> Iterator[None]:
    original = LocalCodingSandbox.execute

    async def counted(self: LocalCodingSandbox, request: Any) -> Any:  # noqa: ANN401
        executions.append(request.code)
        return await original(self, request)

    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1")))
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        stack.enter_context(patch.object(LocalCodingSandbox, "execute", counted))
        yield
    failpoints.reset()


def _command_spec(sandbox: str = "agent-code") -> Any:  # noqa: ANN401
    return w.pinned_spec(tool_ids=[], sandbox=sandbox).model_copy(update={"run_kind": "command"})


def _no_model() -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="none", turns=[])))


class _CommandClaims:
    """Node's claim of a ``/run`` run: its input carries ``sandboxCommand``."""

    def __init__(self, inner: PipesHubRuntimeAssembler) -> None:
        self._inner = inner

    async def build(self, claim: Any) -> Any:  # noqa: ANN401
        node = w.node_claim(claim)
        run = node.run
        assert run is not None and run.input is not None
        commanded = run.model_copy(
            update={"input": run.input.model_copy(update={"sandbox_command": RunSandboxCommand(timeoutS=60)})}
        )
        return await self._inner.build(type(node)(**{**node.__dict__, "run": commanded}))


def _assembler(h: FakeSyncHarness, port: ScriptedModelPort) -> PipesHubRuntimeAssembler:
    async def workspace(claim: NodeClaim, run: RunContext) -> RunCodeWorkspace:
        return RunCodeWorkspace(store=h.store, objects=h.objects, scope=h.object_scope, workspace_id=SESSION)

    return PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port), run_code_workspace=workspace)


async def _run(
    h: FakeSyncHarness, port: ScriptedModelPort, *, crash_at: str | None = None, sandbox: str = "agent-code"
) -> tuple[Any, Any]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=_command_spec(sandbox), input_text=f"/run {LINE}")
    driver = RunDriver(journal=journal, assembler=_CommandClaims(_assembler(h, port)), clock=clock)
    if crash_at is not None:
        failpoints.arm(crash_at, Arm(Raise(_Died), Nth(1)))
        with pytest.raises(_Died):
            _ = await driver.run_claimed(await w.claim_next(journal))
        failpoints.reset()
        _ = clock.advance(31)
        _ = journal.sweep()
    done = await driver.run_claimed(await w.claim_next(journal))
    return done, journal


async def test_as1_run_sees_the_head_writes_a_revision_and_an_engine_reads_it_at_the_same_path(executions: list[str]) -> None:
    h = FakeSyncHarness(TAR)
    _ = await commit(h, {"data.csv": b"a,b\n1,2\n", ".claude/agent.md": b"hidden"})
    port = _no_model()
    done, journal = await _run(h, port)
    assert done.status == "completed"
    assert port.calls == []
    assert len(executions) == 1
    (outcome,) = [e for e in journal.events("ses-1") if e.type == "tool.outcome"]
    assert outcome.data["status"] == "ok"
    head = await h.store.head(h.object_scope, SESSION)
    assert head.revision == 2
    assert sorted(head.files) == [".claude/agent.md", "data.csv", "out.txt"]
    # The hidden plug-in path was neither staged (cat shows only the CSV) nor written back from the sandbox.
    assert await h.objects.get(h.object_scope, head.files["out.txt"].content) == b"hi\n"
    assert await h.objects.get(h.object_scope, head.files[".claude/agent.md"].content) == b"hidden"
    # Path parity: an engine's turn materialises the same manifest path under its /workspace.
    sandbox = await acquire(h)
    try:
        _ = await h.sync.materialize(sandbox, head, root="/workspace")
        assert sandbox.files["/workspace/out.txt"] == b"hi\n"  # type: ignore[attr-defined]
        assert sandbox.files["/workspace/data.csv"] == b"a,b\n1,2\n"  # type: ignore[attr-defined]
    finally:
        await h.broker.release(sandbox)


async def test_as2_a_crash_after_the_command_started_never_runs_it_again(executions: list[str]) -> None:
    h = FakeSyncHarness(TAR)
    done, journal = await _run(h, _no_model(), crash_at="worker.run_code.after_started")
    assert done.status == "completed"
    assert executions == []
    (outcome,) = [e for e in journal.events("ses-1") if e.type == "tool.outcome"]
    assert outcome.data["status"] == "error"


async def test_as3_a_chat_without_the_code_tool_is_refused_and_nothing_runs(executions: list[str]) -> None:
    h = FakeSyncHarness(TAR)
    port = _no_model()
    done, journal = await _run(h, port, sandbox="none")
    assert done.status != "completed"
    failed = [e for e in journal.events("ses-1") if e.type == "run.failed"]
    assert [e.data.get("errorType") for e in failed] == ["tool_not_granted"]
    assert executions == [] and port.calls == []


async def test_as4_the_reply_is_the_exit_code_and_the_activity_shows_the_typed_line() -> None:
    """QAFIX3 bug 3 and W1 through the real assembler: the saved answer is "Exit code 0." with no confidence (not the
    empty-answer fallback with a Low chip), and the command record shows what the person typed, not the launcher."""
    h = FakeSyncHarness(TAR)
    _ = await commit(h, {"data.csv": b"a,b\n"})
    done, journal = await _run(h, _no_model())
    assert done.status == "completed"
    events = journal.events("ses-1")
    (finalized,) = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert finalized.data["answer"] == "Exit code 0."
    assert finalized.data.get("confidence") is None
    (record,) = [e for e in events if e.type == "x.pipeshub.command.finished"]
    assert (record.data["command"], record.data["language"]) == (LINE, "bash")
