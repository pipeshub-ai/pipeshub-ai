"""A short, scripted chaos run of the code sandbox capacity system.

Five query worker processes and one indexing process share a private state
directory (``sandbox_chaos.py`` has the details and the long-run driver).
The phases and faults follow a fixed script, so each run meets the same
kinds of trouble in the same order: a worker killed while it holds slots, a
stopped worker, a damaged live record and yield signal, a wall-clock jump,
readings that disappear, the alert leader killed, a flaky store and a
damaged load file. Chat timing stays random, so the checks are invariants,
not exact counts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.chaos.sandbox_chaos import AlertCfg, ChaosConfig, run_chaos

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("fcntl")
pytestmark = [pytest.mark.chaos, pytest.mark.slow, pytest.mark.timeout(150)]


def test_capacity_invariants_hold_under_chaos(tmp_path: Path) -> None:
    # Many orgs, so most storm waits find every slot taken by other orgs: a wait at an org's own
    # cap counts as its share, not as host load, and would not raise the alert checked below.
    cfg = ChaosConfig(
        name="ci", seed=11, duration_s=42.0, workers=5, indexers=1,
        machine_gib=16.0, cpus=1.4, min_total=1, org_cap=1, orgs=8, expected_ceiling=2,
        alert=AlertCfg(window_s=10, eval_s=1.0, warn_busy_pct=40, warn_min_waited=3,
                       crit_min_refused=3, crit_refused_pct=5, cooldown_s=30),
        fault_every_s=0.0,
        phase_script=[(0, "normal"), (3, "storm"), (10, "memory"), (15, "calm")],
        fault_script=[
            (2, "kill"), (5, "stop_cont"), (7, "corrupt_live"), (8, "clock_fwd"),
            (9, "kill_leader"), (11, "corrupt_yield"), (12, "reading_gone"), (13, "kv_flaky"),
            (16, "corrupt_load"), (18, "kill"),
        ],
        drain_s=20.0,
    )
    result = run_chaos(cfg, tmp_path / "run")
    assert not result["problems"], result["problems"]
    # The run met what it was meant to meet.
    assert result["outcomes"].get("granted", 0) > 20, result["outcomes"]
    assert result["outcomes"].get("refused", 0) > 0, result["outcomes"]
    assert result["alerts"]["evaluations"] > 10, result["alerts"]
    assert sum(result["alerts"]["alerts"].values()) >= 1, result["alerts"]
    assert result["drain"]["yield_released_after_s"] is not None
    assert result["concurrency"]["peak"] <= cfg.expected_ceiling
