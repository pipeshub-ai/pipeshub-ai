"""QA31F: a sandbox that cannot be made is a capacity condition, not a crash.

Found by QA-31's Claude Code rows under load: the broker refused a sandbox (its egress pool looked exhausted), the
engine kernel released the attempt as a lost turn, the next three claims came within two seconds and the run was
quarantined ``crash_loop``. It now yields with a bounded backoff and, once the bound is spent, fails with a typed
reason the user may retry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.controller import EngineController
from harness_core.kernel.durable.capability import (
    SANDBOX_RECHECK_S,
    SANDBOX_UNAVAILABLE,
    SANDBOX_WAIT,
)
from harness_core.sandbox.errors import BrokerUnavailable, SandboxNotFound
from harness_core.testing.engines.durable import EngineWorld

if TYPE_CHECKING:
    import pytest

RUN = "run-sw"


def _refuse(monkeypatch: pytest.MonkeyPatch, times: int | None, error: BaseException | None = None) -> list[int]:
    """The broker refuses the first ``times`` turns (every turn when ``None``)."""
    run_turn = EngineController.run_turn
    calls = [0]

    async def refusing(self: EngineController, *args: object, **kwargs: object) -> object:
        calls[0] += 1
        if times is None or calls[0] <= times:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [error or BrokerUnavailable("pool exhausted")])
        return await run_turn(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EngineController, "run_turn", refusing)
    return calls


async def test_qa31f_4_a_broker_that_recovers_completes_the_run_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More refusals than the crash-loop bound allows attempts: waits never count as crashes."""
    world = EngineWorld()
    world.admit(RUN, "say hello")
    refusals = len(SANDBOX_RECHECK_S)
    _ = _refuse(monkeypatch, refusals)
    seen = []
    for delay in SANDBOX_RECHECK_S:
        disposition = await world.attempt(RUN)
        seen.append((disposition.status, disposition.reason))
        assert world.journal.status(RUN) == "queued"
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN)
    assert seen == [("yielded", SANDBOX_WAIT)] * refusals
    assert final.status == "completed", final
    types = [e.type for e in world.events(RUN)]
    assert "run.quarantined" not in types
    yields = [e.data for e in world.events(RUN) if e.type == "run.yielded"]
    assert [y["reason"] for y in yields] == [SANDBOX_WAIT] * refusals


async def test_qa31f_5_a_broker_that_never_recovers_fails_typed_after_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = EngineWorld()
    world.admit(RUN, "say hello")
    _ = _refuse(monkeypatch, None)
    for delay in SANDBOX_RECHECK_S:
        disposition = await world.attempt(RUN)
        assert (disposition.status, disposition.reason) == ("yielded", SANDBOX_WAIT)
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", SANDBOX_UNAVAILABLE)
    assert [e.data for e in world.events(RUN) if e.type == "run.failed"] == [{"errorType": SANDBOX_UNAVAILABLE}]
    assert "run.quarantined" not in [e.type for e in world.events(RUN)]


async def test_qa31f_6_a_sandbox_that_dies_under_a_turn_keeps_the_crash_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = EngineWorld()
    world.admit(RUN, "say hello")
    _ = _refuse(monkeypatch, 1, SandboxNotFound("sandbox gone"))
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("released", "engine_turn_lost")


async def test_qa31f_9_a_missing_image_fails_at_once_with_the_admin_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not capacity: no wait, the run fails ``engine_image_missing`` (its wording tells the user an admin must act)."""
    from harness_core.sandbox.errors import SandboxRefusal

    world = EngineWorld()
    world.admit(RUN, "say hello")
    _ = _refuse(monkeypatch, None, SandboxRefusal("no such image", reason="engine_image_missing"))
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "engine_image_missing")
    assert [e.type for e in world.events(RUN) if e.type == "run.yielded"] == []
