"""Child processes for the member budget's multi-process tests: top-level so a spawned interpreter can import them."""

from __future__ import annotations

import os
import random
import signal
import sys
import time
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


def controller_loop(directory: str, tick_s: float, ready: Connection, counter_key: str = "n") -> None:
    """Become the controller, write state ``{counter_key: n+1}`` each beat, and tell the parent the epoch."""
    budget = open_budget(directory, "ctl")
    epoch = budget.elect_controller(tick_s)
    resumed = budget.controller()["state"]
    ready.send((epoch, resumed))
    n = (resumed or {}).get(counter_key, 0) if epoch else 0
    while epoch:
        n += 1
        if not budget.controller_beat(epoch, {counter_key: n}):
            break
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
