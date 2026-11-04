"""Image slots, least-served-first waiters and the budgets."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.image_admission import ImageAdmission


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def hold(slot, release: asyncio.Event, started: list, name: str) -> None:
    async with slot.running():
        started.append(name)
        await release.wait()


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_fifth_image_push_is_503_after_task_1() -> None:
    a = ImageAdmission()
    slots = [a.reserve(f"u{i}", "o") for i in range(4)]
    with pytest.raises(PushRefusal) as e:
        a.reserve("u5", "o")
    assert (e.value.status, e.value.reason) == (503, "PUSH_BUSY") and 1 <= e.value.extra["retryAfter"] <= 3
    slots[0].release()
    a.reserve("u5", "o")  # a slot came back


async def test_run_without_a_slot_raises() -> None:
    a = ImageAdmission()
    slot = a.reserve("u", "o")
    slot.release()
    with pytest.raises(RuntimeError):
        async with slot.running():
            pass


async def test_waiter_gives_up_after_the_wait_and_is_busy() -> None:
    a = ImageAdmission(wait_s=0.05)
    release, started = asyncio.Event(), []
    s = [a.reserve(f"u{i}", f"o{i}") for i in range(3)]
    tasks = [asyncio.create_task(hold(s[0], release, started, "a")), asyncio.create_task(hold(s[1], release, started, "b"))]
    await settle()
    with pytest.raises(PushRefusal) as e:
        async with s[2].running():
            pass
    assert e.value.reason == "PUSH_BUSY"
    release.set()
    await asyncio.gather(*tasks)


async def test_least_served_user_served_first() -> None:
    clock = Clock()
    a = ImageAdmission(running=1, slots=8, clock=clock)
    # u1 has already used 20 s; u2 none.
    first = a.reserve("u1", "o")
    async with first.running():
        clock.t += 20
    release, started = asyncio.Event(), []
    blocker = a.reserve("u3", "o")
    t0 = asyncio.create_task(hold(blocker, release, started, "blocker"))
    await settle()
    heavy, light = a.reserve("u1", "o"), a.reserve("u2", "o")
    t1 = asyncio.create_task(hold(heavy, release, started, "heavy"))
    await settle()
    t2 = asyncio.create_task(hold(light, release, started, "light"))
    await settle()
    release.set()
    await asyncio.gather(t0, t1, t2)
    assert started == ["blocker", "light", "heavy"]  # arrival order was heavy first


async def test_user_budget_30s_binds_one_60s_push_then_429() -> None:
    clock = Clock()
    a = ImageAdmission(clock=clock)
    slot = a.reserve("u", "o")
    async with slot.running():
        clock.t += 60
    slot.release()
    with pytest.raises(PushRefusal) as e:
        a.reserve("u", "o")
    assert (e.value.status, e.value.reason) == (429, "PUSH_BUSY")
    clock.t += 61  # the window rolls
    a.reserve("u", "o")
    a.reserve("other", "o")  # another user is unaffected


async def test_org_share_only_while_another_org_waits() -> None:
    clock = Clock()
    a = ImageAdmission(running=2, slots=8, clock=clock)
    # orgB's user has already used 20 s, so by fairness alone orgA's fresh user would be served before it.
    heavy = a.reserve("ub", "orgB")
    async with heavy.running():
        clock.t += 20
    heavy.release()
    # Without another org waiting, one org may hold the whole pool.
    release, started = asyncio.Event(), []
    first, second = a.reserve("u1", "orgA"), a.reserve("u2", "orgA")
    t1 = asyncio.create_task(hold(first, release, started, "a1"))
    t2 = asyncio.create_task(hold(second, release, started, "a2"))
    await settle()
    assert started == ["a1", "a2"]
    gate_b, gate_a, started2 = asyncio.Event(), asyncio.Event(), []
    b = a.reserve("ub", "orgB")
    a3 = a.reserve("u3", "orgA")
    tb = asyncio.create_task(hold(b, gate_b, started2, "b"))
    await settle()
    ta3 = asyncio.create_task(hold(a3, gate_a, started2, "a3"))
    await settle()
    release.set()  # orgA's first two finish: one position frees at a time
    await asyncio.gather(t1, t2)
    await settle()
    assert started2[0] == "b"  # orgA already holds its share while orgB waits
    gate_b.set()
    gate_a.set()
    await asyncio.gather(tb, ta3)


async def test_4_attackers_on_4_accounts_one_text_victim_succeeds_within_lease_wait() -> None:
    """Image pushes are bounded to 4 slots, so a text-only push (which never reaches this module) is untouched."""
    a = ImageAdmission()
    slots = [a.reserve(f"atk{i}", "o") for i in range(4)]
    with pytest.raises(PushRefusal):
        a.reserve("atk5", "o")
    assert a._taken == 4  # text pushes take no slot: nothing here to wait for
    for s in slots:
        s.release()


@pytest.mark.parametrize("how", ["success", "refusal", "exception", "cancel", "discarded_retag"])
async def test_slots_return_to_4_after_every_exit(how) -> None:
    a = ImageAdmission(wait_s=0.05)
    release = asyncio.Event()
    async def body() -> None:
        with a.reserve("u", "o") as slot:
            async with slot.running():
                if how == "refusal":
                    raise PushRefusal(422, "IMAGE_FETCH_FAILED", "x")
                if how == "exception":
                    raise RuntimeError("boom")
                if how in ("cancel",):
                    await release.wait()

    if how == "cancel":
        task = asyncio.create_task(body())
        await settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif how == "discarded_retag":
        with a.reserve("u", "o"):
            pass  # a push whose materialised copy was discarded still releases
    else:
        with contextlib.suppress(PushRefusal, RuntimeError):
            await body()
    assert a._taken == 0 and not a._running and not a._waiters
    for i in range(4):
        a.reserve(f"x{i}", "o")
