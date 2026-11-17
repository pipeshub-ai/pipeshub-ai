"""The elected container controller: one per ledger, fenced by epoch, replaced when dead or hung, resuming its state."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from typing import TYPE_CHECKING

import pytest

from app.capacity.budget_policy import AdmissionPolicy, Demand, Family, plan_floors
from app.capacity.controller import ElectedController
from tests.support import ledger_procs as lp

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from multiprocessing.connection import Connection
    from pathlib import Path

    from app.capacity.member_budget import MemberBudget

POLICY = AdmissionPolicy()
TICK = 0.4


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def handle(ledger: Path, service: str) -> MemberBudget:
    return lp.open_budget(str(ledger), service)  # the same sizes as the child processes use, so the ledger is one


@pytest.fixture
def children() -> Iterator[list]:
    """Child controllers: daemons, and killed at teardown whatever the test did, so a failure never leaves one running."""
    started: list = []
    yield started
    for proc in started:
        if proc.is_alive():
            os.kill(proc.pid, signal.SIGCONT)
            proc.kill()
        proc.join(5)


def start_controller(ctx: mp.context.BaseContext, ledger: Path, children: list, *,
                     stop_after_beat: bool = False) -> tuple[mp.Process, Connection]:
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=lp.controller_loop, args=(str(ledger), TICK, child),
                       kwargs={"stop_after_beat": stop_after_beat}, daemon=True)
    proc.start()
    children.append(proc)
    return proc, parent


def recv(pipe: Connection) -> tuple:
    assert pipe.poll(10), "the controller started"
    return pipe.recv()


def wait_until(condition: Callable[[], object], what: str, timeout_s: float = 10.0) -> object:
    end = time.monotonic() + timeout_s
    while not (result := condition()):
        assert time.monotonic() < end, f"{what} within {timeout_s}s"
        time.sleep(0.02)
    return result


def stopped(pid: int) -> bool:
    waited, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)
    assert waited == 0 or os.WIFSTOPPED(status), "the controller exited instead of stopping"
    return waited != 0


def test_exactly_one_process_is_elected_and_the_others_follow(ledger: Path) -> None:
    handles = [handle(ledger, f"s{i}") for i in range(5)]
    epochs = [h.elect_controller(15) for h in handles]
    assert [e for e in epochs if e is not None] == [1]
    assert epochs[0] == 1
    # the elected one stays elected on the next call, with the same epoch
    assert handles[0].elect_controller(15) == 1
    assert handles[3].elect_controller(15) is None


def test_roles_pod_elects(ledger: Path) -> None:
    """A pod with only parse and docling processes (no indexing process) still has a controller."""
    small, docling = handle(ledger, "parse-small"), handle(ledger, "docling")
    first = ElectedController(small, TICK)
    second = ElectedController(docling, TICK)
    ticks = [c.tick(lambda state, demand: {"n": (state or {}).get("n", 0) + 1}) for c in (first, second)]
    assert sorted(t.role for t in ticks) == ["controller", "follower"]
    assert first.is_controller and not second.is_controller


def test_a_stale_controller_is_fenced_by_the_epoch(ledger: Path) -> None:
    a, b = handle(ledger, "a"), handle(ledger, "b")
    now = int(time.time() * 1000)
    assert a.elect_controller(15, now) == 1
    assert a.controller_beat(1, {"limit": 7}, now)
    assert b.elect_controller(15, now + 10_000) is None  # a beat 10 s ago is fresh at a 15 s tick
    assert b.elect_controller(15, now + 31_000) == 2  # older than two ticks: replaced although a is alive
    assert a.controller_beat(1, {"limit": 99}, now + 31_500) is False  # the resumed stale holder is rejected
    assert b.controller()["state"] == {"limit": 7}  # and wrote nothing
    assert a.elect_controller(15, now + 31_600) is None  # a learns it lost
    assert a.set_floors(plan_floors(16, 18, None, 3.0, POLICY), epoch=1) is False


def test_controller_failover_resumes(ledger: Path, children: list) -> None:
    """SIGKILL the controller: another process takes over at once and resumes the persisted state."""
    ctx = mp.get_context("spawn")
    proc, pipe = start_controller(ctx, ledger, children)
    epoch, resumed = recv(pipe)
    assert epoch == 1 and resumed is None
    time.sleep(TICK * 2.5)
    watcher = handle(ledger, "watch")
    before = watcher.controller()["state"]["n"]
    assert before >= 2
    os.kill(proc.pid, signal.SIGKILL)
    proc.join()
    t = time.monotonic()
    epoch2 = watcher.elect_controller(TICK)
    assert epoch2 == 2 and time.monotonic() - t < TICK  # no waiting for the beat to age: the slot's lock died
    state = watcher.controller()["state"]
    assert state["n"] >= before  # resumed, not restarted from nothing


def test_controller_hang_fenced(ledger: Path, children: list) -> None:
    """SIGSTOP the controller: after two ticks another process takes over; the resumed one cannot write."""
    ctx = mp.get_context("spawn")
    proc, pipe = start_controller(ctx, ledger, children, stop_after_beat=True)
    assert recv(pipe)[0] == 1
    wait_until(lambda: stopped(proc.pid), "the controller stopped")
    watcher = handle(ledger, "watch")
    try:
        beat_ms = watcher.controller()["beat_ms"]
        assert watcher.elect_controller(TICK, beat_ms + 1) is None  # the beat is still fresh
        epoch = wait_until(lambda: watcher.elect_controller(TICK), "a takeover after two silent ticks")
        assert epoch == 2
        assert watcher.controller_beat(epoch, {"n": 1000})
    finally:
        os.kill(proc.pid, signal.SIGCONT)
    proc.join(10)  # the old controller wakes, beats, and is told no
    assert not proc.is_alive(), "a fenced controller stops"
    info = watcher.controller()
    assert info["epoch"] == 2 and info["state"] == {"n": 1000}


def test_controller_crash_loop(ledger: Path, children: list) -> None:
    """A controller process that restarts every few seconds never resets the state: it only ever grows."""
    ctx = mp.get_context("spawn")
    seen = []
    for _ in range(4):
        proc, pipe = start_controller(ctx, ledger, children)
        epoch, resumed = recv(pipe)
        assert epoch is not None
        seen.append((resumed or {}).get("n", 0))
        time.sleep(TICK * 1.2)
        os.kill(proc.pid, signal.SIGKILL)
        proc.join()
    assert seen == sorted(seen) and seen[-1] >= seen[1] > 0
    final = handle(ledger, "watch")
    assert final.elect_controller(TICK) == 5
    assert final.controller()["state"]["n"] >= seen[-1]


def test_a_tick_resumes_persisted_state_and_sums_container_demand(ledger: Path) -> None:
    cells = [handle(ledger, f"cell{i}") for i in range(4)]
    for i, c in enumerate(cells):
        c.publish_demand({"heavy_parse": (2.0 * (i + 1), i, 1, 3), "index_light": (1.0, 0, 5, 9)})
    controller = ElectedController(cells[3], TICK)  # an idle cell holds the role, the others are loaded
    seen = {}

    def compute(state: dict | None, demand: object) -> dict:
        seen["demand"] = demand
        seen["state"] = state
        return {"n": (state or {}).get("n", 0) + 1}

    t1 = controller.tick(compute)
    assert t1.role == "controller" and seen["state"] is None
    assert seen["demand"]["heavy_parse"] == (20.0, 6, 4, 12)  # the sum over every live process, not the controller's own
    assert seen["demand"]["index_light"] == (4.0, 0, 20, 36)
    controller.tick(compute)
    assert seen["state"] == {"n": 1}
    follower = ElectedController(cells[0], TICK)
    t = follower.tick(compute)
    assert t.role == "follower" and t.state == {"n": 2} and not t.stale


def test_a_follower_sees_a_stale_beat(ledger: Path) -> None:
    a, b = handle(ledger, "a"), handle(ledger, "b")
    now = int(time.time() * 1000)
    a.elect_controller(15, now)
    a.controller_beat(1, {"n": 1}, now)
    t = ElectedController(b, 15).tick(lambda s, d: {}, now_ms=now + 5000)
    assert t.role == "follower" and not t.stale
    # the controller is silent for more than two ticks and nobody else takes over by now_ms: stale
    t2 = ElectedController(handle(ledger, "c"), 15).tick(lambda s, d: {}, now_ms=now + 40_000)
    assert t2.role == "controller"  # a stale beat is a takeover, not a wait


def test_a_dead_controller_never_blocks_reservations(ledger: Path) -> None:
    a, b = handle(ledger, "a"), handle(ledger, "b")
    a.elect_controller(15)
    p = a.try_reserve(Demand(Family.PARSE, 160, 0, "small"))
    assert p is not None
    a.close()
    assert b.reserve_blocking(Demand(Family.PARSE, 1, 0, "small"), deadline_s=2) is not None
    assert b.elect_controller(15) == 2
