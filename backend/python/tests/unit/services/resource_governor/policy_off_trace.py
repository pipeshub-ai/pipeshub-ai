"""Records what the memory-gate functions return and call, so ``off`` can be pinned.

``record()`` returns plain data: the working-set and cap functions over an
environment grid, ``record_memory_cost``, the calls ``reserve_record_memory`` and
``acquire_parsing_slot`` make, and the limits and ``/health`` heavy fields of a
governor over a few samples. ``fixtures/policy_off_trace.json`` was produced by
``python -m tests.unit.services.resource_governor.policy_off_trace`` on the tree before
the per-kind charges existed. ``unedited_hashes`` hashes every function the 2e work
did not edit, so the rest of each module stays pinned as written.
"""
# ruff: noqa: ANN202, ANN204, B023
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

FIXTURE = Path(__file__).parent / "fixtures" / "policy_off_trace.json"
GIB = 2**30
MODULES = {
    "policy.py": "app/services/resource_governor/policy.py",
    "memory_budget.py": "app/services/resource_governor/memory_budget.py",
    "consumer_concurrency.py": "app/services/messaging/consumer_concurrency.py",
    "controller.py": "app/services/resource_governor/controller.py",
}


def _snap(limit_gb: float | None, raw_gb: float | None, *, source: str = "grid"):
    from app.services.resource_governor.models import ResourceSnapshot

    return ResourceSnapshot(
        cpu_quota=8.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=None if limit_gb is None else int(limit_gb * GIB),
        mem_working_set_bytes=None if raw_gb is None else int(raw_gb * GIB),
        mem_working_set_raw_bytes=None if raw_gb is None else int(raw_gb * GIB),
        source=source,
    )


def policy_grid() -> dict[str, object]:
    from app.services.resource_governor import policy
    from app.services.resource_governor.models import Ceilings, Pool

    out: dict[str, object] = {}
    ceilings = Ceilings(heavy=6, light=40, index_heavy=12, index_light=24)
    snaps = [(None, None), (2, 1), (4, 1), (4, 3.5), (8, 2), (16, 4), (64, 10)]
    for configured in (None, "1.0", "1.5", "2.5"):
        for excel_gb in (0.5, 1.5, 3.0):
            with patch.dict(os.environ, {} if configured is None else {"GOVERNOR_HEAVY_PARSE_WORKING_SET_GB": configured}, clear=False):
                if configured is None:
                    os.environ.pop("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", None)
                constant = 1.5 if configured is None else float(configured)
                with patch.object(policy, "HEAVY_PARSE_WORKING_SET_GB", constant), patch.object(
                    policy, "excel_document_memory_bytes", lambda e=excel_gb: int(e * GIB)
                ):
                    key = f"{configured}/{excel_gb}"
                    out[f"{key}/ws"] = policy.heavy_parse_working_set_gb()
                    for limit, raw in snaps:
                        snap = _snap(limit, raw)
                        for workers in (1, 3):
                            out[f"{key}/{limit}/{raw}/{workers}/cap"] = policy.heavy_memory_cap(snap, workers)
                            out[f"{key}/{limit}/{raw}/{workers}/target"] = policy._target_for(
                                Pool.HEAVY_PARSE, snap, ceilings, workers
                            )
                        out[f"{key}/{limit}/{raw}/cgroup"] = policy.cgroup_heavy_parse_limit(snap, ceilings)
    return out


def cost_grid() -> dict[str, object]:
    from app.services.resource_governor.memory_budget import record_memory_cost
    from app.services.resource_governor.models import ParseTier
    from app.services.resource_governor.tiers import parse_cost

    out: dict[str, object] = {}
    sizes = (None, 0, 1, 10_000, 10 * 2**20, 38 * 2**20, 40 * 2**20, 25 * 2**20 + 1, 3 * GIB)
    for tier in (ParseTier.HEAVY, ParseTier.LIGHT):
        for size in sizes:
            out[f"{tier.value}/{size}/record"] = record_memory_cost(tier, size)
            out[f"{tier.value}/{size}/parse"] = parse_cost(tier, size)
    return out


async def _consumer_trace() -> dict[str, object]:
    from app.services.messaging import consumer_concurrency as cc
    from app.services.resource_governor import ResourceGovernor
    from app.services.resource_governor.feedback import DownstreamFeedback
    from app.services.resource_governor.models import ParseTier, ResourceSnapshot

    snapshot = ResourceSnapshot(
        cpu_quota=4.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=8 * GIB, mem_working_set_bytes=GIB, source="trace",
    )

    class Probe:
        def snapshot(self):
            return snapshot

    out: dict[str, object] = {}
    for tier in (ParseTier.HEAVY, ParseTier.LIGHT, None):
        for size in (None, 1000, 20 * 2**20, 100 * 2**20):
            governor = ResourceGovernor(
                logger=logging.getLogger("trace"), env_index=24, probe=Probe(), feedback=DownstreamFeedback(),
            )
            host = SimpleNamespace(governor=governor, logger=logging.getLogger("trace"))
            calls: list[object] = []
            budget = governor.memory_budget
            real_reserve, real_release = budget.reserve, budget.release

            async def reserve(n, timeout=None, *, held=0, _r=real_reserve):
                calls.append(["reserve", n, held])
                return await _r(n, timeout, held=held)

            def release(n, _r=real_release):
                calls.append(["release", n])
                return _r(n)

            budget.reserve, budget.release = reserve, release  # type: ignore[method-assign]
            mem = await cc.reserve_record_memory(host, tier, size, timeout=1.0)
            again = await cc.reserve_record_memory(host, tier, size, timeout=1.0, downloaded=True, current=mem)
            slot = await cc.acquire_parsing_slot(host, tier, size, timeout=1.0)
            held = governor.stats()
            slot_fields = [slot.tier.value if slot.tier else None, slot.cost, slot.released]
            cc.release_admission(slot)
            cc.release_admission(again)
            key = f"{tier.value if tier else None}/{size}"
            out[key] = {
                "calls": calls,
                "mem": None if mem is None else [mem.tier.value if mem.tier else None, mem.cost],
                "again": None if again is None else [again.tier.value if again.tier else None, again.cost],
                "slot": slot_fields,
                "slot_fields": sorted(slot.__dict__),
                "record_memory": held["record_memory"],
                "in_use": held["in_use"],
            }
            governor.close()
    return out


def governor_trace() -> dict[str, object]:
    from app.services.resource_governor import ResourceGovernor
    from app.services.resource_governor.feedback import DownstreamFeedback
    from app.services.resource_governor.models import Pool

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    out: dict[str, object] = {}
    for limit_gb, raw in ((8, 1), (4, 3), (2, 1.5)):
        shots = [_snap(limit_gb, raw), _snap(limit_gb, raw), _snap(limit_gb, raw / 2)]

        class Probe:
            def __init__(self):
                self.i = 0

            def snapshot(self):
                snap = shots[min(self.i, len(shots) - 1)]
                self.i += 1
                return snap

        clock = Clock()
        # Its own feedback: the process-wide one holds whatever earlier tests in this process reported.
        governor = ResourceGovernor(
            logger=logging.getLogger("trace"), probe=Probe(), clock=clock, sample_interval=1.0, env_index=24,
            feedback=DownstreamFeedback(),
        )

        async def run():
            seen = []
            for _ in range(6):
                clock.now += 15.0
                await governor._sample_once()
                seen.append(governor.limit(Pool.HEAVY_PARSE))
            return seen

        seen = asyncio.run(run())
        stats = governor.stats()
        out[f"{limit_gb}/{raw}"] = {
            "limits": seen,
            "heavy_fields": {
                k: stats[k] for k in ("limits", "ceilings", "fleet_ceilings", "record_memory", "worker_count")
            },
            "keys": sorted(stats),
            "lanes": stats["lanes"],
            "cgroup_limit": governor._heavy_parse_lease_limit,
        }
        governor.close()
    return out


def record() -> dict[str, object]:
    return {
        "policy": policy_grid(),
        "cost": cost_grid(),
        "consumer": asyncio.run(_consumer_trace()),
        "governor": governor_trace(),
    }


def unedited_hashes(path: Path, skip: set[str] | None = None) -> dict[str, str]:
    """A hash of every top-level statement, function and method of a module."""
    from tests.unit.modules.parsers.parse_pool_trace import module_hashes

    hashes = module_hashes(path)
    return {k: v for k, v in hashes.items() if not skip or k not in skip}


def main() -> None:
    root = Path(__file__).resolve().parents[4]
    payload = record()
    hashes: dict[str, dict[str, str]] = {}
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "policy_off_hash.py"
    for name, rel in MODULES.items():
        blob = subprocess.run(
            ["git", "show", f"HEAD:backend/python/{rel}"], capture_output=True, text=True, check=True, cwd=root
        ).stdout
        tmp.write_text(blob)
        hashes[name] = unedited_hashes(tmp)
    tmp.unlink()
    payload["hashes"] = hashes
    payload["recorded_from"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
