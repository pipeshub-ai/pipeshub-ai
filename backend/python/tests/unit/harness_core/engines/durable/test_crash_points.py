"""EK-14, EK-15, EK-18, EK-19 (E1): a worker killed at every step boundary of a turn; the run resumes, no
effect runs twice, nothing is journaled twice. The same points the G4 matrix crashes on the real binary."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections import Counter
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.engines.controller import RESUME_NOTE
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.engines.durable.kernel import INTERRUPTED_NOTE
from harness_core.journal.types import TERMINAL_STATUSES, NewEvent
from harness_core.skills.types import SkillPin, SkillRef
from harness_core.testing.engines.durable import (
    ORG,
    PROGRESS,
    SESSION,
    EngineWorld,
    RecordingPresenter,
    ScriptedEngineAdapter,
)
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import ProgressStamp

RUN = "run-cp"
# One file before the effects (the save before them commits it), then enough writes for an autosave.
PROMPT = (
    "write in/first.md start\n"
    'tool create {"name": "ticket"}\n'
    'tool lookup {"q": "churn"}\n'
    "write out/report.md first draft\n"
    "write notes/a.md a\n"
    "write notes/b.md b\n"
    "write out/report.md final\n"
    "say finished"
)
# Every boundary of a turn: the controller's steps, the effect window, the kernel's commits.
POINTS = (
    "engine.kernel.before_turn_start",
    "engine.turn.after_acquire",
    "engine.turn.after_materialize",
    "engine.turn.after_skills",
    "engine.turn.after_restore",
    "engine.turn.after_autosave",
    "engine.turn.after_save_before_effect",
    "worker.tool.after_intent",
    "worker.tool.after_effect",
    "engine.turn.after_publish",
    "engine.turn.after_save_turn_end",
    "engine.turn.before_checkpoint",
    "engine.turn.after_checkpoint",
    "engine.kernel.before_turn_commit",
    "engine.kernel.after_turn_commit",
)


async def world_with_skill() -> EngineWorld:
    world = EngineWorld(approvals={"create": "allow"})
    world.config = dataclasses.replace(world.config, autosave_changes=2)
    world.skills.publish(ORG, "report", {"SKILL.md": b"---\nname: report\ndescription: d\n---\nbody\n"})
    from harness_core.skills.builder import StoredSkillBundleBuilder

    builder = StoredSkillBundleBuilder(source=world.skills, objects=world.objects, workspaces=world.store)
    bundle = await builder.build(ObjectScope(ORG, f"ws:{SESSION}"), SkillRef("report"))
    world.skill_pins = (SkillPin("report", bundle.digest),)
    return world


async def finish(world: EngineWorld, run_id: str, *, limit: int = 6) -> str:
    for _ in range(limit):
        if world.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if world.journal.status(run_id) == "running":
            world.expire()
        _ = await world.attempt(run_id)
    return world.journal.status(run_id)


def assert_well_formed(world: EngineWorld, run_id: str) -> None:
    events = world.events(run_id)
    completed = Counter(e.data["turn"] for e in events if e.type == "turn.completed")
    assert all(n == 1 for n in completed.values()), completed
    terminal = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled", "run.quarantined")]
    assert len(terminal) == 1, [e.type for e in terminal]
    lines = [(e.data["turnId"], e.data["seq"]) for e in events if e.type == PROGRESS]
    assert len(lines) == len(set(lines))
    finals = [e for e in events if e.type == "message.completed" and e.data.get("isFinal")]
    assert len(finals) == 1


@pytest.mark.parametrize("point", POINTS)
async def test_ek14_a_worker_killed_at_each_boundary_resumes_without_duplicates(point: str) -> None:
    world = await world_with_skill()
    # The turn's last save commits something only when the engine wrote after the last autosave.
    world.admit(RUN, PROMPT if point != "engine.turn.after_save_turn_end" else "write out/report.md x\nsay done")
    assert await world.crash_at(RUN, point), f"the turn never reached {point}"
    assert await finish(world, RUN) == "completed"
    assert world.duplicates() == {}
    assert_well_formed(world, RUN)
    files = await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    assert "out/report.md" in files.files


@pytest.mark.parametrize("point", ["engine.turn.after_autosave", "worker.tool.after_effect", "engine.turn.after_checkpoint"])
async def test_ek14_killed_twice_the_third_attempt_completes(point: str) -> None:
    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    assert await world.crash_at(RUN, point)
    _ = await world.crash_at(RUN, "engine.turn.after_acquire")
    assert await finish(world, RUN) == "completed"
    assert world.duplicates() == {}
    assert_well_formed(world, RUN)


async def two_turns(world: EngineWorld) -> None:
    """Turn 1 says hello; queued input makes turn 2, which writes a file."""
    world.admit(RUN, "say first")
    _ = world.journal.queue_input(RUN, "write second.md two\nsay second")


async def test_ek12_the_redriven_turn_restores_the_last_checkpoint_and_is_told_it_was_interrupted() -> None:
    world = await world_with_skill()
    await two_turns(world)
    assert await world.crash_at(RUN, "engine.turn.after_save_turn_end")
    assert await finish(world, RUN) == "completed"
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    assert adapter.restored[-1] is not None
    prompts = [m.content for m in world.gateway.model_calls[-1].messages if m.role == "user"]
    assert INTERRUPTED_NOTE in prompts[-1]
    assert_well_formed(world, RUN)


@pytest.mark.parametrize("point", ["engine.turn.after_acquire", "engine.turn.after_restore"])
async def test_ek12_a_redriven_follow_up_turn_keeps_its_own_input(point: str) -> None:
    """Found by G4: the follow-up's consumed input preceded its `turn.started`, so the re-driven turn ran
    the run's first prompt instead."""
    world = await world_with_skill()
    await two_turns(world)
    assert await world.crash_at(RUN, point, nth=2)
    assert await finish(world, RUN) == "completed"
    prompts = [m.content for m in world.gateway.model_calls[-1].messages if m.role == "user"]
    assert "write second.md two" in prompts[-1]
    assert "second.md" in (await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files
    assert_well_formed(world, RUN)


async def test_ek13_no_state_at_all_resumes_from_the_workspace_flagged() -> None:
    world = await world_with_skill()
    world.admit(RUN, "write kept.md x\nwrite kept2.md y\nwrite kept3.md z\nsay done")
    assert await world.crash_at(RUN, "engine.turn.after_autosave")
    # The autosave reached the store before the worker died: the next sandbox starts from it.
    assert "kept.md" in (await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files
    assert await finish(world, RUN) == "completed"
    assert RESUME_NOTE in world.gateway.model_calls[-1].system


async def test_ek13_ek21_state_that_is_gone_resumes_from_the_workspace_flagged() -> None:
    world = await world_with_skill()
    await two_turns(world)
    assert await world.crash_at(RUN, "engine.turn.after_save_turn_end")
    state = fold_engine_run(world.journal.events(SESSION), run_id=RUN)
    assert state.checkpoint is not None
    # Erasing the session shreds its key: the state's bytes can no longer be read.
    del world.objects._objects[(ORG, state.checkpoint.content.object_id)]  # pyright: ignore[reportPrivateUsage]
    assert await finish(world, RUN) == "completed"
    system = world.gateway.model_calls[-1].system
    assert RESUME_NOTE in system
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    assert adapter.restored[-1] is None


async def test_ek15_an_engine_that_ends_its_turn_failed_fails_the_run_with_its_code() -> None:
    world = await world_with_skill()
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append("token_expired")
    world.admit(RUN, "say x")
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "token_expired")


async def test_ek6_a_budget_refusal_from_the_gateway_ends_the_run_cut_short() -> None:
    """BUDGETEND: the engine's turn the gateway stopped for budget is the result, flagged, never a bare failure."""
    world = await world_with_skill()
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append("budget_exhausted")
    world.admit(RUN, "say x")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    completed = [e for e in world.journal.events(SESSION) if e.type == "run.completed"]
    assert [e.data for e in completed] == [{"outcome": "answered", "truncatedBy": "budget"}]


async def test_ek15_an_engine_that_vanished_mid_turn_is_resumed_not_failed() -> None:
    world = await world_with_skill()
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append(None)
    world.admit(RUN, "write a.md x\nsay done")
    disposition = await world.attempt(RUN)
    assert disposition.status == "released"
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_ek18_a_drain_mid_turn_yields_and_the_next_attempt_resumes() -> None:
    world = await world_with_skill()
    world.admit(RUN, "write a.md x\nhold\nsay done")
    kernel = world.kernel()
    attempt = asyncio.ensure_future(world.attempt(RUN, kernel=kernel))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    kernel.request_drain()
    disposition = await asyncio.wait_for(attempt, 10)
    world.gateway.release()
    assert disposition.status == "yielded"
    assert "engine.checkpointed" in world.types(RUN)
    assert "turn.completed" not in world.types(RUN)
    world.clock.advance(1)
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_ek19_a_lost_lease_mid_turn_commits_nothing_more() -> None:
    world = await world_with_skill()
    world.admit(RUN, "write a.md x\nhold\nsay done")
    attempt = asyncio.ensure_future(world.attempt(RUN))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    world.expire()
    seen = len(world.events(RUN))
    disposition = await asyncio.wait_for(attempt, 10)
    world.gateway.release()
    assert disposition.status == "lost"
    after = world.events(RUN)[seen:]
    assert [e.type for e in after if e.writer == "worker"] == []
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_ek15_a_sandbox_gone_under_a_gateway_call_is_a_lost_turn_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-6 (G4): the dead sandbox surfaces from the MCP task group as an ExceptionGroup."""
    from harness_core.engines.controller import EngineController
    from harness_core.sandbox.errors import SandboxNotFound

    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    run_turn = EngineController.run_turn
    calls = 0

    async def dies_once(self: EngineController, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [SandboxNotFound("sandbox is gone")])
        return await run_turn(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EngineController, "run_turn", dies_once)
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("released", "engine_turn_lost")
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_p9_fb3_a_broker_gone_under_a_turn_is_a_lost_turn_with_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P9 FM-2 (FB-3): the broker service killed mid-turn surfaced as ``engine_kernel_error``. QA31F: the run waits for
    the broker (a bounded ``sandbox_wait``), never counted as a crash."""
    from harness_core.engines.controller import EngineController
    from harness_core.sandbox.errors import BrokerUnavailable

    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    run_turn = EngineController.run_turn
    calls = 0

    async def broker_dies_once(self: EngineController, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [BrokerUnavailable("broker unreachable")])
        return await run_turn(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EngineController, "run_turn", broker_dies_once)
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("yielded", "sandbox_wait")
    _ = world.clock.advance(60)
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_p9_fb5_an_engine_gone_behind_its_clients_own_error_is_a_lost_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P9 FM-10 (FB-5): opencode's transport re-raises "nothing listens" as ``httpcore.ConnectError`` from the
    sandbox's ``SandboxConnectError``; the kernel released it ``engine_kernel_error``."""
    import httpcore

    from harness_core.engines.controller import EngineController
    from harness_core.sandbox.errors import SandboxConnectError

    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    run_turn = EngineController.run_turn
    calls = 0

    async def engine_gone_once(self: EngineController, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            connect = httpcore.ConnectError("nothing listens on port 4096")
            connect.__cause__ = SandboxConnectError("nothing listens on port 4096")
            raise ExceptionGroup("unhandled errors in a TaskGroup", [connect])
        return await run_turn(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EngineController, "run_turn", engine_gone_once)
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("released", "engine_turn_lost")
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


def _engine_dies(shape: str, error: BaseException) -> BaseException:
    if shape == "alone":
        return error
    if shape == "group":
        return ExceptionGroup("unhandled errors in a TaskGroup", [error])
    wrapper = RuntimeError("the adapter's own error")
    wrapper.__cause__ = error
    return wrapper


@pytest.mark.parametrize("shape", ["alone", "group", "cause"])
@pytest.mark.parametrize(
    ("kind", "reason"), [("crashed", "engine_crashed"), ("unhealthy", "engine_unhealthy")]
)
async def test_h1_eg1_eg2_a_dead_engine_is_a_lost_turn_with_its_typed_reason(
    monkeypatch: pytest.MonkeyPatch, shape: str, kind: str, reason: str
) -> None:
    """H1 EG-1, EG-2 (P9 FB-5 remainder): an adapter's typed engine death releases the attempt with its own reason."""
    from harness_core.engines.controller import EngineController
    from harness_core.engines.errors import EngineCrashed, EngineUnhealthy

    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    run_turn = EngineController.run_turn
    calls = 0
    error = EngineCrashed("opencode export exited with 137") if kind == "crashed" else EngineUnhealthy("no answer")

    async def dies_once(self: EngineController, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _engine_dies(shape, error)
        return await run_turn(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EngineController, "run_turn", dies_once)
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("released", reason)
    assert await finish(world, RUN) == "completed"
    assert_well_formed(world, RUN)


async def test_h1_eg3_an_engine_dead_in_every_attempt_is_quarantined_never_a_kernel_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness_core.engines.controller import EngineController
    from harness_core.engines.errors import EngineCrashed

    world = await world_with_skill()
    world.admit(RUN, PROMPT)

    async def always_dies(self: EngineController, *args: object, **kwargs: object) -> object:
        raise EngineCrashed("the app-server stopped before answering turn/start")

    monkeypatch.setattr(EngineController, "run_turn", always_dies)
    reasons: list[str | None] = []
    for _ in range(6):
        if world.journal.status(RUN) in TERMINAL_STATUSES:
            break
        disposition = await world.attempt(RUN)
        reasons.append(disposition.reason if disposition.status == "released" else disposition.status)
    assert world.journal.status(RUN) == "quarantined"
    assert reasons[:-1] and set(reasons[:-1]) == {"engine_crashed"}
    assert "engine_kernel_error" not in reasons


class _RecallingPresenter(RecordingPresenter):
    """Journals a line with turn 1's start, as PipesHub's presenter journals ``x.pipeshub.memory.recalled``."""

    def started(self, stamp: ProgressStamp, goal: str) -> list[NewEvent]:
        return [*super().started(stamp, goal), self._line(stamp, "recalled", "")]


@pytest.mark.parametrize("point", POINTS)
async def test_mr1_what_turn_one_journals_at_its_start_is_journaled_once(point: str) -> None:
    """H2 MR-1 (P7.3 open item): a re-driven first turn does not start again, so the recalled event is not
    journaled twice; only an attempt that died before the start committed journals it, once."""
    world = await world_with_skill()
    world.presenter = _RecallingPresenter()
    world.admit(RUN, PROMPT if point != "engine.turn.after_save_turn_end" else "write out/report.md x\nsay done")
    assert await world.crash_at(RUN, point)
    assert await finish(world, RUN) == "completed"
    lines = [e.data["kind"] for e in world.events(RUN) if e.type == PROGRESS]
    assert lines.count("recalled") == 1 and lines.count("started") == 1


async def test_ek20_kernel_logs_carry_the_attempt_and_no_content(caplog: pytest.LogCaptureFixture) -> None:
    world = await world_with_skill()
    world.admit(RUN, PROMPT)
    with caplog.at_level(logging.INFO, logger="harness_core.engines.durable"):
        assert await world.crash_at(RUN, "worker.tool.after_effect")
        assert await finish(world, RUN) == "completed"
    lines = [r for r in caplog.records if getattr(r, "harness_event", "").startswith("engine.kernel.")]
    assert {str(getattr(r, "harness_event", "")) for r in lines} >= {"engine.kernel.effect_recovered"}
    for record in lines:
        fields = cast("dict[str, object]", getattr(record, "harness", {}))
        assert {"orgId", "sessionId", "runId", "attemptId", "fence"} <= fields.keys(), record.getMessage()
        for content in ("ticket", "churn", "first draft", "finished"):
            assert content not in record.getMessage()
