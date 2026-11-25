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


def _ci_config() -> ChaosConfig:
    # Many orgs, so most storm waits find every slot taken by other orgs: a wait at an org's own
    # cap counts as its share, not as host load, and would not raise the alert checked below.
    return ChaosConfig(
        name="ci", seed=11, duration_s=42.0, workers=5, indexers=1,
        machine_gib=16.0, cpus=1.4, min_total=1, org_cap=1, orgs=8, expected_ceiling=2,
        alert=AlertCfg(window_s=10, eval_s=1.0, warn_busy_pct=40, warn_min_waited=3,
                       crit_min_refused=3, crit_refused_pct=5, cooldown_s=30),
        fault_every_s=0.0,
        phase_script=[(0, "normal"), (3, "storm"), (10, "memory"), (15, "calm")],
        # The clock jump comes after the storm and the memory phase: a jump forward empties the alert
        # window at once, so one inside the storm can leave neither side of it with an alert's worth
        # of refusals (test_the_slow_storm_keeps_its_alert_across_the_clock_jump).
        fault_script=[
            (2, "kill"), (5, "stop_cont"), (7, "corrupt_live"),
            (9, "kill_leader"), (11, "corrupt_yield"), (12, "reading_gone"), (13, "kv_flaky"),
            (14.5, "clock_fwd"), (16, "corrupt_load"), (18, "kill"),
        ],
        drain_s=20.0,
    )


def test_the_slow_storm_keeps_its_alert_across_the_clock_jump(tmp_path: Path) -> None:
    """The storm a slow runner produced (its slot refusals came late, at 6.6, 7.5, 8.5 and 9.2 s), replayed on the real load
    ledger, alert window and evaluator with the script's clock jump: an alert must still fire. With the jump at 8 s, inside
    the storm, the refusals were split 2 and 2 by the jump and by a minute boundary between them, and none fired."""
    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.load_ledger import MINUTE_S, LoadLedger
    from app.services.sandbox_alerts.config import AlertSettings
    from app.services.sandbox_alerts.evaluator import evaluate
    from app.services.sandbox_alerts.window import read_window

    cfg = _ci_config()
    a = cfg.alert
    settings = AlertSettings(window_s=a.window_s, eval_s=int(max(1, a.eval_s)), warn_busy_pct=a.warn_busy_pct,
                             warn_min_waited=a.warn_min_waited, crit_min_refused=a.crit_min_refused,
                             crit_refused_pct=a.crit_refused_pct, cooldown_s=a.cooldown_s)
    jump_at = next(at for at, fault in cfg.fault_script if fault == "clock_fwd")
    jump = 30.0  # within the harness's 10 to 90 s
    start = 1_800_000_000 - 8.0  # a minute boundary between the second and the third refusal, as on the slow run
    assert start % MINUTE_S == MINUTE_S - 8.0
    now = [start]
    ledger = LoadLedger(tmp_path / "state", mode="compose", clock=lambda: now[0], backed_off=lambda: False)
    refusals = [6.6, 7.5, 8.5, 9.2]
    levels = []
    for step in range(1, 160):
        t = step / 10
        now[0] = start + t + (jump if t >= jump_at else 0.0)
        if refusals and t >= refusals[0]:
            refusals.pop(0)
            ledger.outcome("refused", 0.5, HoldReason.SLOTS_FULL)
            assert ledger.flush()
        if step % 10 == 0:  # one evaluation a second, as the runner's eval_s
            levels.append(evaluate(read_window(tmp_path / "state" / "load", now[0], settings.window_s), settings).level)
    assert "critical" in levels, levels


def test_capacity_invariants_hold_under_chaos(tmp_path: Path) -> None:
    cfg = _ci_config()
    result = run_chaos(cfg, tmp_path / "run")
    assert not result["problems"], result["problems"]
    # The run met what it was meant to meet.
    assert result["outcomes"].get("granted", 0) > 20, result["outcomes"]
    assert result["outcomes"].get("refused", 0) > 0, result["outcomes"]
    assert result["alerts"]["evaluations"] > 10, result["alerts"]
    assert sum(result["alerts"]["alerts"].values()) >= 1, result["alerts"]
    assert result["drain"]["yield_released_after_s"] is not None
    assert result["concurrency"]["peak"] <= cfg.expected_ceiling
