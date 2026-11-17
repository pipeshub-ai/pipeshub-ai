"""One request (one ``SandboxManager``: a chat turn and its sub-agents) holds
at most ``SANDBOX_MAX_PER_REQUEST`` governor slots at once.

Without it, one chat's parallel ``run_code`` calls took every slot of the
container, and every other chat waited and was refused. The extra calls of
that request wait in arrival order without holding a governor slot, and are
refused after ``slot_wait_s`` like a full governor.
"""

from __future__ import annotations

import asyncio
import logging
import random

import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.sandbox.capacity import DEFAULT_MAX_PER_REQUEST, max_per_request
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxCallStopped,
    SandboxManager,
    SandboxType,
)


class _PerCall:
    class capabilities:
        is_metered = False

    def __init__(self) -> None:
        self.sandbox_id = f"pc-{id(self)}"

    async def provision(self) -> None:
        await asyncio.sleep(0)

    async def destroy(self) -> None:
        await asyncio.sleep(0)


async def _chat(governor: SandboxResourceGovernor, **kwargs: object) -> tuple[SandboxManager, str]:
    kwargs.setdefault("max_slots_per_request", "env")  # as the chat path builds it
    manager = SandboxManager(governor=governor, **kwargs)  # type: ignore[arg-type]
    manager.register_backend_factory(SandboxType.CODING, _PerCall)
    sid, _ = await manager.get_or_create(SandboxType.CODING)
    return manager, sid


class _Peak:
    def __init__(self) -> None:
        self.running = 0
        self.peak = 0

    async def run(self, manager: SandboxManager, sid: str, hold: asyncio.Event | float) -> None:
        async with manager.slot(SandboxType.CODING, sid):
            self.running += 1
            self.peak = max(self.peak, self.running)
            try:
                if isinstance(hold, asyncio.Event):
                    await hold.wait()
                else:
                    await asyncio.sleep(hold)
            finally:
                self.running -= 1


async def test_one_chat_cannot_take_every_slot() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    attacker, sid = await _chat(governor, slot_wait_s=30.0, max_slots_per_request=2)
    peak = _Peak()
    release = asyncio.Event()
    calls = [asyncio.ensure_future(peak.run(attacker, sid, release)) for _ in range(8)]
    for _ in range(20):
        await asyncio.sleep(0)
    assert governor.held_total() == 2
    # Another chat still gets a slot at once.
    victim, victim_sid = await _chat(governor, slot_wait_s=0.0)
    async with victim.slot(SandboxType.CODING, victim_sid):
        assert governor.held_total() == 3
    release.set()
    await asyncio.gather(*calls)
    assert peak.peak == 2
    assert governor.held_total() == 0


async def test_the_default_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_MAX_PER_REQUEST", "3")
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    manager, sid = await _chat(governor, slot_wait_s=30.0)
    assert manager.request_slot_limit() == 3
    monkeypatch.delenv("SANDBOX_MAX_PER_REQUEST")
    assert manager.request_slot_limit() == DEFAULT_MAX_PER_REQUEST


async def test_never_above_the_ceiling() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=3))
    manager, _ = await _chat(governor, max_slots_per_request=50)
    assert manager.request_slot_limit() == 3


@pytest.mark.parametrize(
    ("raw", "want"),
    [(None, 2), ("", 2), ("0", 2), ("-4", 2), ("abc", 2), ("1", 1), ("5", 5), ("99999", 1024)],
)
def test_parsing(raw: str | None, want: int) -> None:
    env = {} if raw is None else {"SANDBOX_MAX_PER_REQUEST": raw}
    assert max_per_request(env) == want


async def test_an_extra_call_is_refused_after_the_wait_without_holding_a_slot() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    manager, sid = await _chat(governor, slot_wait_s=0.05, max_slots_per_request=1)
    release = asyncio.Event()
    first = asyncio.ensure_future(_Peak().run(manager, sid, release))
    await asyncio.sleep(0)
    with pytest.raises(SandboxLimitExceeded, match="this chat already has") as caught:
        async with manager.slot(SandboxType.CODING, sid):
            pytest.fail("the second call ran")
    assert caught.value.reason is None
    assert governor.held_total() == 1
    release.set()
    await first
    assert governor.held_total() == 0
    async with manager.slot(SandboxType.CODING, sid):  # the turn came back
        pass


async def test_without_a_wait_the_extra_call_is_refused_at_once() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    manager, sid = await _chat(governor, slot_wait_s=0.0, max_slots_per_request=1)
    async with manager.slot(SandboxType.CODING, sid):
        with pytest.raises(SandboxLimitExceeded, match="this chat already has a code run"):
            async with manager.slot(SandboxType.CODING, sid):
                pass


async def test_waiting_calls_run_in_arrival_order_and_show_one_note() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    notes: list[tuple[str, bool]] = []

    async def note(tool: str, waiting: bool) -> None:
        notes.append((tool, waiting))

    manager = SandboxManager(
        governor=governor, slot_wait_s=10.0, on_slot_wait=note, max_slots_per_request=1,
    )
    manager.register_backend_factory(SandboxType.CODING, _PerCall)
    sid, _ = await manager.get_or_create(SandboxType.CODING)
    order: list[int] = []
    gate = asyncio.Event()

    async def call(n: int) -> None:
        async with manager.slot(SandboxType.CODING, sid, tool=f"t{n}"):
            order.append(n)
            if n == 0:
                await gate.wait()

    tasks = [asyncio.ensure_future(call(n)) for n in range(4)]
    for _ in range(10):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2, 3]
    for n in (1, 2, 3):
        assert notes.count((f"t{n}", True)) == 1
        assert notes.count((f"t{n}", False)) == 1
    assert ("t0", True) not in notes


async def test_the_request_wait_and_the_governor_wait_share_one_deadline() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
    other, other_sid = await _chat(governor)
    manager, sid = await _chat(governor, slot_wait_s=0.2, max_slots_per_request=1)
    loop = asyncio.get_running_loop()
    async with manager.slot(SandboxType.CODING, sid):
        started = loop.time()
        with pytest.raises(SandboxLimitExceeded):
            async with manager.slot(SandboxType.CODING, sid):
                pass
    assert loop.time() - started < 1.0
    del other, other_sid


async def test_cancelled_and_stopped_waiters_give_every_turn_back() -> None:
    rng = random.Random(7)
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=4))
    token = CancellationToken()
    manager, sid = await _chat(
        governor, slot_wait_s=5.0, max_slots_per_request=2, stop_token=token,
    )
    peak = _Peak()
    outcomes: dict[str, int] = {}

    async def call() -> None:
        try:
            await manager.unless_stopped(lambda: peak.run(manager, sid, rng.uniform(0, 0.01)), tool="run_code")
            key = "ran"
        except SandboxCallStopped:
            key = "stopped"
        except SandboxLimitExceeded:
            key = "refused"
        outcomes[key] = outcomes.get(key, 0) + 1

    tasks = [asyncio.ensure_future(call()) for _ in range(60)]
    for _ in range(15):
        await asyncio.sleep(rng.uniform(0, 0.005))
        rng.choice(tasks).cancel()
    asyncio.get_running_loop().call_later(0.03, token.cancel)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(r is None or isinstance(r, asyncio.CancelledError) for r in results), results
    assert peak.peak <= 2
    assert governor.held_total() == 0
    assert manager._request_slots.held == 0
    assert outcomes.get("ran", 0) > 0


async def test_a_metered_sandbox_keeps_its_slot_for_life_outside_the_request_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Metered(_PerCall):
        class capabilities:
            is_metered = True

    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    manager = SandboxManager(governor=governor, max_slots_per_request=1)
    manager.register_backend_factory(SandboxType.CODING, _Metered)
    with caplog.at_level(logging.INFO):
        ids = [(await manager.get_or_create(SandboxType.CODING))[0] for _ in range(3)]
    assert governor.held_total() == 3
    async with manager.slot(SandboxType.CODING, ids[0]):  # a no-op: it already holds one
        pass
    await manager.destroy_all()
    assert governor.held_total() == 0
