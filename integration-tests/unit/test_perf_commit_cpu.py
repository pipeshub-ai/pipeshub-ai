"""PERF-D: what the CPU bench computes from the Node ledger's windows, and how its result is gated.

The bench needs real processes; these pin the arithmetic that would quietly produce a wrong number (idle CPU
counted as a turn's, the hooked run's total used, a per-turn kind divided by nothing) and the ratchet's verdicts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import harness_profiles  # noqa: E402
from commit_cpu import (  # noqa: E402
    ADMISSION,
    BACKGROUND,
    ENDING_COMMIT,
    GATED_KINDS,
    IN_STEP,
    STEP_COMMIT,
    STREAM,
    Window,
    in_step_ms,
    metrics,
    render,
    request_cpu_ms,
)
from harness_profiles import CPU_MIN_TURNS, N16_SESSIONS, N16_WARM_UP_TURNS, PROFILES, n16_warm_up  # noqa: E402
from slo_gates import Bound, evaluate, gates, passed  # noqa: E402

TURNS = 10


def _plain(loop_ms_per_turn: float = 100.0, calibration_us: float = 5_000.0) -> Window:
    """A plain window: 10 turns over 10 s; idle, the process burns 1% of a core."""
    idle = {"loopUs": 50_000.0, "wallMs": 5_000.0}
    run = {"loopUs": loop_ms_per_turn * 1000 * TURNS + 100_000.0, "wallMs": 10_000.0}
    return Window.of(TURNS, idle, run, calibration_us)


def _attributed() -> Window:
    """The hooked window: slower in total, with every callback charged; a turn is 1 admission, 6 steps, 1 ending."""
    idle = {"loopUs": 80_000.0, "wallMs": 5_000.0, "tickUs": {BACKGROUND: 40_000.0}}
    run = {
        "loopUs": 2_400_000.0,
        "wallMs": 10_000.0,
        "requests": {ADMISSION: TURNS, STEP_COMMIT: 6 * TURNS, ENDING_COMMIT: TURNS},
        "tickUs": {ADMISSION: 200_000.0, STEP_COMMIT: 600_000.0, ENDING_COMMIT: 400_000.0, STREAM: 200_000.0,
                   BACKGROUND: 680_000.0},
        "commands": {f"{ADMISSION} | find chatSessions": 3 * TURNS, f"{ADMISSION} | insert agentRuns": TURNS,
                     f"{STEP_COMMIT} | find agentRuns": 12 * TURNS, f"{ENDING_COMMIT} | find agentRuns": 4 * TURNS,
                     f"{STREAM} | find sessionEvents": 5 * TURNS, f"{BACKGROUND} | find outbox_events": 7 * TURNS},
    }
    return Window.of(TURNS, idle, run, 9_000.0)


def test_perfd_5_a_turns_cpu_is_the_event_loops_less_what_the_process_burns_idle() -> None:
    assert _plain().turn_cpu_ms() == pytest.approx(100.0)
    busy_idle = Window.of(TURNS, {"loopUs": 500_000.0, "wallMs": 5_000.0}, {"loopUs": 2_000_000.0, "wallMs": 10_000.0}, 1.0)
    assert busy_idle.turn_cpu_ms() == pytest.approx(100.0)


def test_perfd_5_shares_are_of_the_callbacks_with_the_backgrounds_idle_part_taken_out() -> None:
    shares = _attributed().shares()
    # The background ran 40 ms of callbacks in 5 s idle: 80 ms of its 680 over the 10 s window are not a turn's.
    assert shares == pytest.approx({ADMISSION: 0.1, STEP_COMMIT: 0.3, ENDING_COMMIT: 0.2, STREAM: 0.1, BACKGROUND: 0.3})
    assert sum(shares.values()) == pytest.approx(1.0)


def test_perfd_5_a_requests_cpu_is_its_share_of_the_plain_runs_turn_not_of_the_hooked_runs() -> None:
    cost = request_cpu_ms(_plain(100.0), _attributed())
    assert cost == pytest.approx({ADMISSION: 10.0, STEP_COMMIT: 5.0, ENDING_COMMIT: 20.0, STREAM: 10.0, BACKGROUND: 30.0})
    # Per turn it adds up to the plain run's total, whatever the hooks added to the other run.
    per_turn = cost[ADMISSION] + 6 * cost[STEP_COMMIT] + cost[ENDING_COMMIT] + cost[STREAM] + cost[BACKGROUND]
    assert per_turn == pytest.approx(_plain(100.0).turn_cpu_ms())
    assert request_cpu_ms(_plain(50.0), _attributed())[STEP_COMMIT] == pytest.approx(2.5)


def test_perfd_5_store_commands_are_per_request_of_the_kind_that_sent_them_and_per_turn_for_the_rest() -> None:
    window = _attributed()
    assert window.commands_per_request() == pytest.approx(
        {ADMISSION: 4.0, STEP_COMMIT: 2.0, ENDING_COMMIT: 4.0, STREAM: 5.0, BACKGROUND: 7.0})
    assert window.commands_per_turn() == pytest.approx(4 + 12 + 4 + 5 + 7)


def test_perfd_5_a_window_without_attribution_has_no_shares_and_no_costs() -> None:
    assert _plain().shares() == {}
    assert request_cpu_ms(_plain(), _plain()) == {}


def test_perfd_6_requests_of_one_instant_end_one_after_another_on_one_loop() -> None:
    assert IN_STEP == N16_SESSIONS
    assert in_step_ms(4.0) == 64.0
    assert in_step_ms(9.0) == 144.0
    assert in_step_ms(5.0, sessions=20) == 100.0


def test_perfd_6_the_result_states_each_cost_in_ms_in_units_in_commands_and_in_step() -> None:
    result = metrics(_plain(100.0, calibration_us=5_000.0), _attributed())
    assert result["turn_cpu_ms"]["max"] == 100.0
    assert result["turn_cpu_units"]["max"] == 20.0
    assert result[f"cpu_ms[{STEP_COMMIT}]"]["max"] == 5.0
    assert result[f"cpu_units[{STEP_COMMIT}]"]["max"] == 1.0
    assert result[f"in_step_ms[{ENDING_COMMIT}]"]["max"] == 320.0
    assert result[f"commands[{ENDING_COMMIT}]"]["max"] == 4.0
    assert result["commands[turn]"]["max"] == 32.0
    assert f"in_step_ms[{STREAM}]" not in result, "only the requests an SLO times are multiplied out"
    table = render(result)
    assert f"| {STEP_COMMIT} | 5 | 1 | 2 | 80 |" in table
    assert "| a turn | 100 | 20 | 32 | — |" in table


def test_perfd_6_without_a_calibration_reading_nothing_is_stated_in_units() -> None:
    result = metrics(_plain(100.0, calibration_us=0.0), _attributed())
    assert "turn_cpu_units" not in result
    assert not [name for name in result if name.startswith("cpu_units[")]
    assert result[f"cpu_ms[{ADMISSION}]"]["max"] == 10.0


def _ratchets() -> list[Bound]:
    return [g for g in gates() if g.profile == "commit-cpu"]


def test_perfd_5_the_bench_is_a_rig_profile_with_a_ratchet_on_each_gated_request() -> None:
    assert "commit-cpu" in PROFILES
    assert CPU_MIN_TURNS >= 60
    ratchets = _ratchets()
    assert ratchets and all(isinstance(g, Bound) and g.slo == "PERFC-F1" and not g.waived_by for g in ratchets)
    held = {g.metric for g in ratchets}
    for kind in GATED_KINDS:
        assert f"commands[{kind}]" in held, kind
        assert f"cpu_units[{kind}]" in held, kind
    assert "commands[turn]" in held
    assert all(not g.at_least and g.stat == "max" for g in ratchets)


@pytest.mark.parametrize(("factor", "load", "status"), [(1.0, 3.0, "pass"), (0.5, 3.0, "pass"), (1.01, 3.0, "fail"),
                                                        (1.01, 40.0, "rerun"), (0.5, 40.0, "rerun")])
def test_perfd_5_a_cost_over_its_ratchet_fails_and_a_loaded_hosts_number_is_not_a_result(
    factor: float, load: float, status: str
) -> None:
    for gate in _ratchets():
        value = {"n": 1, "max": gate.threshold * factor}
        results: dict[str, dict[str, Any]] = {"commit-cpu": {"metrics": {gate.metric: value}, "load1_max": load}}
        verdict = next(iter(evaluate(results, cores=32, rows=(gate,))))
        assert verdict.status == status, gate.metric
        assert passed([verdict]) is (status == "pass")


def test_perfd_5_a_kind_the_run_did_not_report_is_missing_not_passed() -> None:
    gate = next(g for g in _ratchets() if g.metric == f"commands[{ENDING_COMMIT}]")
    verdict = next(iter(evaluate({"commit-cpu": {"metrics": {}, "load1_max": 3.0}}, cores=32, rows=(gate,))))
    assert verdict.status == "missing"
    assert passed([verdict]) is False


class _Rig:
    """Stands in for the rig where the warm-up reads MongoDB's transaction counts."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def transactions(self) -> dict[str, int]:
        self.log.append("transactions")
        return {"committed": len(self.log), "aborted": 0}


async def test_perfd_10_the_warm_up_runs_every_session_once_and_is_over_before_anything_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PERFC-F5: the burst profile's first turn (the org's data key, the pool, the JIT) is in none of its numbers."""
    log: list[str] = []

    async def turn(_client: object, _kind: str, _mode: str, _query: str, user: str | None = None) -> harness_profiles.Turn:
        log.append(f"turn {user}")
        return harness_profiles.Turn(run_id=f"run-{len(log)}", end="RUN_FINISHED", done_ms=1.0)

    monkeypatch.setattr(harness_profiles, "chat_turn", turn)
    runs, counts = await n16_warm_up(object(), _Rig(log))  # type: ignore[arg-type]

    assert N16_WARM_UP_TURNS == 1
    assert len(runs) == N16_SESSIONS * N16_WARM_UP_TURNS
    assert log[-1] == "transactions", "MongoDB's counts are read once the warm-up is over"
    assert len({line for line in log[:-1]}) == N16_SESSIONS, "each session's own user"
    assert counts == {"committed": N16_SESSIONS + 1, "aborted": 0}


def test_perfd_10_both_n16_profiles_that_start_in_step_warm_up_the_same_way() -> None:
    import inspect

    for name in ("commits-n16-burst", "commits-n16-instep"):
        source = inspect.getsource(PROFILES[name])
        assert "n16_warm_up(client, rig)" in source, name
        assert "unrecorded" in source, name
