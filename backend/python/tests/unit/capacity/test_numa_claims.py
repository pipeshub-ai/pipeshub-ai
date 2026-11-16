"""NUMA pinning claims a cell only in a serving worker.

``python -m app.indexing_main`` imports ``app.utils.runtime_threads`` in the uvicorn master, and every
spawned worker (and every spawn pool a cell starts) re-imports the main module. With
``PIPESHUB_CAPACITY_PROFILE=auto`` and ``PIPESHUB_NUMA_PINNING=auto`` on a two-node host only the workers
may claim and pin; a cell's children keep the claim and the node they inherit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator  # noqa: TC003 - the fixture's return annotation
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.capacity import apply, cells, numa
from app.capacity.facts import CapacityFacts

PY_ROOT = Path(__file__).resolve().parents[3]
CPUS = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []

pytestmark = pytest.mark.skipif(len(CPUS) < 2 or not cells._OFD, reason="needs two CPUs to split into nodes and Linux locks")


def _two_nodes() -> tuple[tuple[int, tuple[int, ...]], tuple[int, tuple[int, ...]]]:
    half = len(CPUS) // 2
    return (0, tuple(CPUS[:half])), (1, tuple(CPUS[half:]))


def _facts_json() -> str:
    from app.capacity.sources import Cgroup

    return replace(Cgroup({}, probe_gpu=False).read(), numa=_two_nodes()).to_json()


def _env(tmp_path: Path, workers: int) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "PYTHONPATH": str(PY_ROOT), "TMPDIR": str(tmp_path), "PIPESHUB_SERVICE": "indexing",
        "PIPESHUB_CAPACITY_PROFILE": "auto", "PIPESHUB_NUMA_PINNING": "auto",
        "INDEXING_UVICORN_WORKERS": str(workers), "PIPESHUB_CAPACITY_FACTS": _facts_json(),
    }


def _run(tmp_path: Path, script: str, workers: int) -> dict:
    path = tmp_path / "main.py"
    path.write_text(textwrap.dedent(script))
    out = subprocess.run([sys.executable, "-W", "ignore", str(path)], cwd=PY_ROOT, env=_env(tmp_path, workers),
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


HEADER = """
import json, multiprocessing as mp, os
import app.utils.runtime_threads  # first import, as in app/indexing_main.py
from app.capacity import cells

def held():
    return cells._PROCESS[0].index if cells._PROCESS else None
"""


def test_the_uvicorn_master_takes_no_cell_and_each_worker_takes_one(tmp_path: Path) -> None:
    got = _run(tmp_path, HEADER + """
def worker(q, done):
    q.put((held(), sorted(os.sched_getaffinity(0)), os.environ.get("PIPESHUB_CELL_INDEX")))
    done.wait(60)  # a worker keeps its claim while it serves

if __name__ == "__main__":
    ctx = mp.get_context("spawn")
    q, done = ctx.Queue(), ctx.Event()
    procs = [ctx.Process(target=worker, args=(q, done)) for _ in range(2)]
    for p in procs: p.start()
    got = [q.get(timeout=60) for _ in procs]
    done.set()
    for p in procs: p.join()
    print(json.dumps({"master": held(), "master_env": os.environ.get("PIPESHUB_CELL_INDEX"), "workers": got}))
""", workers=2)
    assert got["master"] is None and got["master_env"] is None, f"the master took cell {got['master']}"
    assert sorted(w[0] for w in got["workers"]) == [0, 1]
    assert {w[2] for w in got["workers"]} == {"0", "1"}
    nodes = {tuple(w[1]) for w in got["workers"]}
    assert nodes == {_two_nodes()[0][1], _two_nodes()[1][1]}, "the two workers are not on different nodes"


def test_a_cells_spawn_pool_children_keep_its_claim_and_node(tmp_path: Path) -> None:
    got = _run(tmp_path, HEADER + """
from concurrent.futures import ProcessPoolExecutor

def child():
    return (held(), sorted(os.sched_getaffinity(0)), os.environ.get("PIPESHUB_CELL_INDEX"))

def worker(q):
    with ProcessPoolExecutor(2, mp_context=mp.get_context("spawn")) as pool:
        kids = [pool.submit(child).result() for _ in range(4)]
    q.put({"cell": held(), "affinity": sorted(os.sched_getaffinity(0)), "kids": kids})

if __name__ == "__main__":
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=worker, args=(q,)); p.start()
    out = q.get(timeout=90); p.join()
    print(json.dumps(out))
""", workers=4)
    assert got["cell"] is not None
    for held, affinity, marker in got["kids"]:
        assert held is None, f"a pool child of cell {got['cell']} took cell {held}"
        assert marker == str(got["cell"]) and affinity == got["affinity"]


def test_a_single_process_service_is_its_own_cell_and_pins() -> None:
    """Without worker processes (no uvicorn master), the process itself is the cell."""
    env: dict[str, str] = {"INDEXING_UVICORN_WORKERS": "1"}
    assert apply._serves_a_cell(env)
    assert apply._serves_a_cell({})


def test_the_master_of_several_workers_is_not_a_cell() -> None:
    env = {"INDEXING_UVICORN_WORKERS": "3"}
    assert not apply._serves_a_cell(env)  # this test process is the main process
    with patch("app.capacity.apply.multiprocessing.current_process", return_value=SimpleNamespace(name="SpawnProcess-1")):
        assert apply._serves_a_cell(env)
    assert not apply._serves_a_cell({**env, apply.CELL_ENV: "1"}), "a child of a cell must not claim again"


FACTS64 = CapacityFacts(cpu_quota=64.0, cpu_affinity=64, cpu_host=64, mem_limit=256 << 30, mem_host=256 << 30,
                        cgroup="v2", member_id="m", numa=((0, tuple(range(32))), (1, tuple(range(32, 64)))))
RUNNING = {"PIPESHUB_NUMA_PINNING": "auto", "INDEXING_UVICORN_WORKERS": "4"}


def _plan_with_masks() -> object:
    from app.capacity.policy import plan_capacity

    return plan_capacity(FACTS64, {"PIPESHUB_CAPACITY_PROFILE": "auto", "PIPESHUB_NUMA_PINNING": "auto"})


@pytest.mark.parametrize("service", ["docling", "query", "parsing", "embedding", "extraction", "connectors", ""])
def test_no_other_service_claims_or_pins(service: str) -> None:
    plan = _plan_with_masks()
    assert plan.numa_masks
    env = dict(RUNNING)
    with patch("app.capacity.cells.process_claim") as claim, patch("app.capacity.numa.pin") as pin:
        apply._pin_numa(plan, FACTS64, env, service)
    claim.assert_not_called()
    pin.assert_not_called()
    assert apply.CELL_ENV not in env


def test_an_indexing_worker_claims_pins_and_marks_its_children(tmp_path: Path) -> None:
    plan = _plan_with_masks()
    env = dict(RUNNING)
    fake = cells.CellClaim(tmp_path, 3, -1)
    with (
        patch("app.capacity.cells.cells_directory", return_value=tmp_path),
        patch("app.capacity.cells.process_claim", return_value=fake) as claim,
        patch("app.capacity.numa.pin") as pin,
        patch("app.capacity.apply.multiprocessing.current_process", return_value=SimpleNamespace(name="SpawnProcess-1")),
    ):
        apply._pin_numa(plan, FACTS64, env, "indexing")
        apply._pin_numa(plan, FACTS64, env, "indexing")  # a child of this process now skips
    claim.assert_called_once()
    pin.assert_called_once_with(3, numa.masks(FACTS64, 4), env)  # the pre-pin CPU count is recorded in this env
    assert env[apply.CELL_ENV] == "3"


def test_a_failed_claim_marks_the_process_as_pending_not_as_a_cell(tmp_path: Path) -> None:
    plan = _plan_with_masks()
    env = dict(RUNNING)
    with (
        patch("app.capacity.cells.cells_directory", return_value=tmp_path),
        patch("app.capacity.cells.process_claim", return_value=None),
        patch("app.capacity.numa.pin") as pin,
        patch("app.capacity.apply.multiprocessing.current_process", return_value=SimpleNamespace(name="SpawnProcess-1")),
    ):
        apply._pin_numa(plan, FACTS64, env, "indexing")
    pin.assert_not_called()
    assert env[apply.CELL_ENV] == apply.CELL_PENDING, "a child of this process must not claim an index"
    assert not apply._serves_a_cell(env)


def test_the_workers_that_run_decide_the_placement_not_the_planned_cells(tmp_path: Path) -> None:
    """The plan proposes several cells, but the launcher starts one worker unless told otherwise. That
    worker must keep every node: pinning it to node 0 would idle the other."""
    plan = _plan_with_masks()
    assert plan.cells > 1 and plan.numa_masks
    for workers in (None, "1"):
        env = {"PIPESHUB_NUMA_PINNING": "auto"} | ({} if workers is None else {"INDEXING_UVICORN_WORKERS": workers})
        with patch("app.capacity.cells.process_claim") as claim, patch("app.capacity.numa.pin") as pin:
            apply._pin_numa(plan, FACTS64, env, "indexing")
        claim.assert_not_called()
        pin.assert_not_called()
        assert apply.CELL_ENV not in env


def test_pinning_needs_the_auto_profile(tmp_path: Path) -> None:
    from app.capacity.policy import plan_capacity

    legacy = plan_capacity(FACTS64, {"PIPESHUB_CAPACITY_PROFILE": "legacy", "PIPESHUB_NUMA_PINNING": "auto"})
    with patch("app.capacity.cells.process_claim") as claim, patch("app.capacity.numa.pin") as pin:
        apply._pin_numa(legacy, FACTS64, dict(RUNNING), "indexing")
    claim.assert_not_called()
    pin.assert_not_called()


def test_a_replacement_worker_that_finds_every_index_held_pins_when_it_claims_later(tmp_path: Path) -> None:
    """At start-up the old worker still holds the index: nothing is pinned, the masks wait, and the
    later claim (from the service) pins the process and marks its children."""
    plan = _plan_with_masks()
    env = dict(RUNNING)
    apply._DEFERRED_MASKS.clear()
    try:
        with (
            patch("app.capacity.cells.cells_directory", return_value=tmp_path),
            patch("app.capacity.cells.process_claim", return_value=None),
            patch("app.capacity.numa.pin") as pin,
            patch("app.capacity.apply.multiprocessing.current_process", return_value=SimpleNamespace(name="SpawnProcess-1")),
        ):
            apply._pin_numa(plan, FACTS64, env, "indexing")
            pin.assert_not_called()
            assert env[apply.CELL_ENV] == apply.CELL_PENDING and apply._DEFERRED_MASKS
            assert apply.adopt_claim(1, env)
            pin.assert_called_once_with(1, numa.masks(FACTS64, 4), env, all_threads=True)
            assert env[apply.CELL_ENV] == "1" and not apply._DEFERRED_MASKS
            assert not apply.adopt_claim(1, env), "pinned once"
    finally:
        apply._DEFERRED_MASKS.clear()


def test_a_late_claim_without_a_deferred_pin_only_marks_the_process() -> None:
    """NUMA pinning off (or nothing deferred): the claim still tells the process's children it is a cell."""
    env: dict[str, str] = {}
    with patch("app.capacity.numa.pin") as pin:
        assert not apply.adopt_claim(0, env)
    pin.assert_not_called()
    assert env[apply.CELL_ENV] == "0" and not apply._serves_a_cell(env)


@pytest.fixture(autouse=True)
def _no_deferred_pin() -> Iterator[None]:
    apply._DEFERRED_MASKS.clear()
    yield
    apply._DEFERRED_MASKS.clear()


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity") or not os.path.isdir("/proc/self/task"), reason="needs per-thread affinity")
def test_pinning_later_moves_the_threads_that_already_exist() -> None:
    """``sched_setaffinity(0)`` is the calling thread's only; a thread started before the pin keeps the old mask without ``all_threads``."""
    import threading

    before = os.sched_getaffinity(0)
    cpu = min(before)
    started, release = threading.Event(), threading.Event()
    seen: dict[str, int] = {}

    def worker() -> None:
        seen["tid"] = threading.get_native_id()
        started.set()
        release.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(10)
    try:
        assert numa.pin(0, ((cpu,),), {}, all_threads=True)
        assert os.sched_getaffinity(seen["tid"]) == {cpu}
    finally:
        release.set()
        t.join()
        os.sched_setaffinity(0, before)
