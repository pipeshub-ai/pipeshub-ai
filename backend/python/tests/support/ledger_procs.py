"""Child processes for the member budget's multi-process tests: top-level so a spawned interpreter can import them."""

from __future__ import annotations

import contextlib
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.capacity.budget_policy import AdmissionPolicy, Demand, Family, plan_floors
from app.capacity.member_budget import MemberBudget

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

C = 160
MEM = 256 << 30


def open_budget(directory: str, service: str = "", cpu_tokens: int = C, mem: int = MEM, llm_cap: int = 24) -> MemberBudget:
    floors = plan_floors(128, cpu_tokens, None, 1.0, AdmissionPolicy())
    return MemberBudget(directory, cpu_tokens=cpu_tokens, mem_bytes=mem, floors=floors, llm_cap=llm_cap, service=service)


def hammer(directory: str, seconds: float, seed: int, out: Connection, start_at: float) -> None:
    """Reserve and release random weighted demands for ``seconds``; report pairs, refusals-with-room and the largest sum held."""
    rng = random.Random(seed)
    budget = open_budget(directory, f"h{seed}")
    while time.time() < start_at:
        time.sleep(0.001)
    end = time.monotonic() + seconds
    pairs = refused_with_room = 0
    kinds = [(Family.PARSE, "small", 1), (Family.EMBED, "small", 2), (Family.DOCLING, "small", 2),
             (Family.PARSE, "large", 1), (Family.INSPECT, None, 1)]
    while time.monotonic() < end:
        family, cls, w = rng.choice(kinds)
        permit = budget.try_reserve(Demand(family, w, 0, cls))
        if permit is None:
            # C is 160 and 16 processes hold at most a few tokens each: a refusal here is a contention refusal.
            refused_with_room += 1
            continue
        permit.release()
        pairs += 1
    budget.close()
    out.send((pairs, refused_with_room))


def hold_and_wait(directory: str, family: str, cpu: int, ready: Connection) -> None:
    """Take a permit and sleep: the parent kills this process."""
    budget = open_budget(directory, "holder", cpu_tokens=int(os.environ.get("LEDGER_C", C)))
    permit = budget.try_reserve(Demand(Family(family), cpu, 1 << 30, "small"))
    ready.send(permit is not None)
    signal.pause()


def controller_loop(directory: str, tick_s: float, ready: Connection, counter_key: str = "n", *,
                    stop_after_beat: bool = False) -> None:
    """Become the controller, write state ``{counter_key: n+1}`` each beat, and tell the parent the epoch.

    ``stop_after_beat`` SIGSTOPs this process after its first beat, between two ledger transactions: a stop from
    outside could land while it holds the ledger lock, and then every other process waits for it to resume."""
    budget = open_budget(directory, "ctl")
    epoch = budget.elect_controller(tick_s)
    resumed = budget.controller()["state"]
    ready.send((epoch, resumed))
    n = (resumed or {}).get(counter_key, 0) if epoch else 0
    while epoch:
        n += 1
        if not budget.controller_beat(epoch, {counter_key: n}):
            break
        if stop_after_beat:
            stop_after_beat = False
            stop_self()
        time.sleep(tick_s / 4)


def pid_of_self() -> int:
    return os.getpid()


def stop_self() -> None:
    os.kill(os.getpid(), signal.SIGSTOP)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(0)


def bulk_loader(directory: str, until: float, ready: Connection, hold_s: float) -> None:
    """Large units, as many as the ledger lets in, held ``hold_s``, then again: bulk load."""
    budget = open_budget(directory, "bulk")
    ready.send(True)
    while time.time() < until:
        held = []
        while (permit := budget.try_reserve(Demand(Family.PARSE, 6, 0, "large"))) is not None:
            held.append(permit)
        time.sleep(hold_s if held else 0.01)
        for permit in held:
            permit.release()
    budget.close()


def die_at_hot_write(directory: str, cpu_tokens: int, mode: str) -> None:
    """Take (``acquire``) or give back (``release``) tokens and die just before the hot block reaches the file,
    after this process's own slot was written."""
    import app.capacity.member_budget as mb

    budget = MemberBudget(directory, cpu_tokens=cpu_tokens, mem_bytes=1 << 34)
    real = os.pwrite
    state = {"armed": False}

    def dying(fd: int, data: bytes, offset: int) -> int:
        if state["armed"] and offset < mb.TABLE_OFFSET:
            os._exit(9)
        return real(fd, data, offset)

    os.pwrite = dying  # type: ignore[assignment]
    if mode == "acquire":
        state["armed"] = True
        budget.try_reserve(Demand(Family.PARSE, 2, 0, "small"))
    else:
        permit = budget.try_reserve(Demand(Family.PARSE, 2, 0, "small"))
        state["armed"] = True
        permit.release()  # type: ignore[union-attr]


def llm_waiters(directory: str, count: int, seconds: float, out: Connection) -> None:
    """``count`` LLM calls waiting for the container's member slots; reports the CPU seconds the process spent."""
    import asyncio

    from app.capacity.member_budget import AdmissionTimeout

    budget = open_budget(directory, "w", cpu_tokens=16, mem=1 << 36)

    async def run() -> None:
        async def one() -> None:
            with contextlib.suppress(AdmissionTimeout):
                await budget.reserve(Demand(Family.LLM, 0), deadline_s=seconds)

        await asyncio.gather(*(one() for _ in range(count)))

    start = time.process_time()
    asyncio.run(run())
    out.send(time.process_time() - start)


def hold_all_llm_slots_then_wait(directory: str, count: int, ready: Connection) -> None:
    """Take every LLM slot, tell the parent, then sleep until killed."""
    budget = open_budget(directory, "holder", cpu_tokens=16, mem=1 << 36, llm_cap=count)
    held = [budget.try_reserve(Demand(Family.LLM, 0)) for _ in range(count)]
    ready.send(all(held))
    time.sleep(600)


def hold_then_leave_an_idle_child(directory: str, ready: Connection) -> None:
    """Take tokens, fork a child that stays alive without touching the budget, then die without cleaning up."""
    budget = open_budget(directory, "parent", cpu_tokens=16, mem=1 << 36)
    held = [budget.try_reserve(Demand(Family.PARSE, 1, 0, "small")) for _ in range(4)]
    pid = os.fork()
    if pid == 0:
        time.sleep(120)
        os._exit(0)
    ready.send((pid, all(held)))
    os._exit(9)


def signal_ready_then_wait(ready_path: str, release_path: str, seconds: float = 30.0) -> int:
    """Runs in a pool worker: say it is running (a file), then wait until the test releases it (another file)."""
    Path(ready_path).write_text("up")
    end = time.monotonic() + seconds
    while time.monotonic() < end and not Path(release_path).exists():
        time.sleep(0.02)
    return 1
