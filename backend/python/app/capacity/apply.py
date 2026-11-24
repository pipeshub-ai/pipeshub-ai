"""What a process does with its plan before it imports anything heavy."""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import sys
from typing import TYPE_CHECKING

from app.capacity import numa
from app.capacity.knobs import capacity_profile, drop_blank_knobs, knob_value
from app.capacity.policy import DEFAULT_OMP, plan_capacity
from app.capacity.sources import resolve_facts

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from app.capacity.facts import CapacityFacts
    from app.capacity.plan import CapacityPlan, ServicePlan

SERVICE_ENV = "PIPESHUB_SERVICE"
CELL_PENDING = "pending"  # CELL_ENV while a cell process holds no index yet
CELL_ENV = "PIPESHUB_CELL_INDEX"  # set by the process that claimed a cell; its children inherit it and the pinning

_DEFERRED_MASKS: list[tuple[tuple[int, ...], ...]] = []  # the masks of a pin that waits for a late claim

_FAILURE: list[tuple[str, str]] = []  # (requested profile, why) of the last plan that could not be built; empty when it was


def legacy_threads(env: MutableMapping[str, str]) -> dict[str, str]:
    """Today's thread env, with no plan: the fallback when the plan cannot be built."""
    n = env.get("OMP_NUM_THREADS") or str(DEFAULT_OMP)
    return {
        "OMP_NUM_THREADS": n,
        "OPENBLAS_NUM_THREADS": n,
        "MKL_NUM_THREADS": n,
        "TOKENIZERS_PARALLELISM": "false",
    }


def apply_process_env(env: MutableMapping[str, str] | None = None) -> CapacityPlan | None:
    """In this order: drop blank registry knobs, build the plan, ``setdefault`` its thread env.

    A value the operator set is never overwritten. Any failure building the plan falls back to
    the thread env this module has always set, so a process never starts worse than it did.
    """
    target = os.environ if env is None else env
    drop_blank_knobs(target)
    service = target.get(SERVICE_ENV, "")
    plan: CapacityPlan | None = None
    _FAILURE.clear()
    try:
        facts = resolve_facts(target, probe_gpu=False)
        plan = plan_capacity(facts, target)
        service_plan: ServicePlan | None = plan.services.get(service)
        threads = dict((service_plan or next(iter(plan.services.values()))).threads_env)
        _pin_numa(plan, facts, target, service)
    except Exception as exc:  # nothing here may stop a service from starting
        _FAILURE[:] = [(_profile(target), _reason(exc))]
        threads = legacy_threads(target)
    for key, value in threads.items():
        target.setdefault(key, value)
    return plan


def _profile(env: MutableMapping[str, str]) -> str:
    try:
        return capacity_profile(env)
    except Exception:
        return "legacy"


def _reason(exc: BaseException) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text[:120]


def _serves_a_cell(env: MutableMapping[str, str]) -> bool:
    """True in a process that is one cell: not the uvicorn master, and not a child of a cell.

    With several workers the master is the process named ``MainProcess``. (``parent_process()`` is not
    set yet while a spawned child imports the main module, so the name is what tells them apart.) Its workers, and the spawn pools
    those start, re-import the main module; a cell exports ``CELL_ENV``, so its children skip the claim
    and keep the node they inherited.
    """
    if env.get(CELL_ENV):
        return False
    return not (_running_workers(env) > 1 and multiprocessing.current_process().name == "MainProcess")


def _running_workers(env: MutableMapping[str, str]) -> int:
    """Indexing worker processes the launcher will run (``INDEXING_UVICORN_WORKERS``, default 1).

    This is what the service starts, not the number of cells the plan would propose, so NUMA
    placement is built from it."""
    try:
        return max(1, int(knob_value(env, "INDEXING_UVICORN_WORKERS") or 1))  # type: ignore[call-overload]
    except ValueError:
        return 1


def _pin_numa(plan: CapacityPlan, facts: CapacityFacts, env: MutableMapping[str, str], service: str) -> None:
    """Step 2 of the start-up order, only for an indexing cell and only when asked for.

    The masks come from the workers that actually run, so one worker is never confined to one node
    of several: with fewer workers than nodes nothing is pinned."""
    if service != "indexing" or plan.profile != "auto" or not numa.enabled(env) or not _serves_a_cell(env):
        return
    workers = _running_workers(env)
    masks = numa.masks(facts, workers)
    if not masks:
        return
    from app.capacity.cells import cells_directory, process_claim

    directory = cells_directory()
    claim = process_claim(directory, workers) if directory is not None else None
    if claim is not None:
        numa.pin(claim.index, masks, env)
        env[CELL_ENV] = str(claim.index)
    else:
        # No index is held yet (a replacement worker, or no cells directory). Children must not take one
        # for themselves, so the process is marked; the claim and the pin follow in ``adopt_claim``.
        env[CELL_ENV] = CELL_PENDING
        if directory is not None:
            _DEFERRED_MASKS[:] = [masks]


def adopt_claim(index: int, env: MutableMapping[str, str] | None = None) -> bool:
    """Record this process's cell index for its children, and pin it if the pin was deferred.

    One rule for every claim:
    - Only a process that serves a cell claims (``_serves_a_cell``): not the uvicorn master and not a
      child of a cell. A cell's children inherit ``CELL_ENV``, either its index or ``pending`` while it
      holds none, so a spawned child can never take an index a serving worker is waiting for.
    - The service calls this whenever it obtains a claim: at start-up, or later in the background. A
      replacement worker starts while the worker it replaces still holds the index and becoming ready
      cannot wait for it (the old worker is retired only once the new one answers).
    - NUMA placement follows the claim. Threads that exist are moved to the node, and so is every
      process started afterwards. Memory already allocated stays where it is; a cell that never gets
      an index stays unpinned and says so in its start-up WARNING.
    Returns True when this call pinned the process.
    """
    target = os.environ if env is None else env
    already = (target.get(CELL_ENV) or "").isdigit()
    target[CELL_ENV] = str(index)
    if already or not _DEFERRED_MASKS:
        return False
    pinned = numa.pin(index, _DEFERRED_MASKS[0], target, all_threads=True)
    _DEFERRED_MASKS.clear()
    return pinned


def log_line(plan: CapacityPlan | None, env: MutableMapping[str, str] | None = None) -> str:
    target = os.environ if env is None else env
    service = target.get(SERVICE_ENV) or "unknown"
    omp = target.get("OMP_NUM_THREADS", "")
    if plan is None:
        profile, why = _FAILURE[0] if _FAILURE else (_profile(target), "unknown")
        return f"capacity: service={service} omp={omp} profile={profile} plan=unavailable reason={why}"
    line = (
        f"capacity: service={service} omp={omp} profile={plan.profile} cpus={plan.budget.cpus:g} "
        f"mem_gib={plan.budget.mem_bytes / (1 << 30):.1f} cells={plan.cells}"
    )
    shown = [w for w in plan.warnings if plan.profile == "auto" or w.startswith("invalid_")]
    return f"{line} warnings={','.join(shown)}" if shown else line


def announce(plan: CapacityPlan | None) -> None:
    """One line on stderr, from a service's main process only (a spawned child is still named for its parent while it imports the main module): logging is not configured yet when this runs."""
    if not os.environ.get(SERVICE_ENV) or multiprocessing.current_process().name != "MainProcess":
        return
    with contextlib.suppress(OSError):
        sys.stderr.write(log_line(plan) + "\n")
