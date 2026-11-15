"""``pause_verdict``: the bounded pause budget (6 charged sends, 20 sends in all, 24 h net of holds)."""

import itertools

import pytest

from app.modules.pipeline.pause_budget import (
    PauseLimits,
    PauseState,
    Verdict,
    budget_spent,
    pause_verdict,
    wall_seconds_used,
)

HOUR_MS = 3_600_000
LIMITS = PauseLimits()
T0 = 1_000_000_000


def _oracle(charged: int, sends: int, *, trial: bool, chargeable: bool, sent: bool, wall_ms: int) -> tuple[Verdict, int, int]:
    """The rules as the design states them, written out independently of the implementation."""
    sends += 1 if sent else 0
    charged += 1 if (sent and chargeable and not trial) else 0
    if wall_ms >= 24 * HOUR_MS:
        return Verdict.FAIL_WALL, sends, charged
    if sends >= 20:
        return Verdict.FAIL_ABSOLUTE, sends, charged
    if charged >= 6:
        return Verdict.CAPPED, sends, charged
    return Verdict.PAUSE, sends, charged


@pytest.mark.parametrize("trial", [False, True], ids=["send", "trial"])
@pytest.mark.parametrize("chargeable", [False, True], ids=["unrated", "rate>=95%"])
@pytest.mark.parametrize("sent", [True, False], ids=["sent", "refused"])
def test_pause_verdict_table(trial: bool, chargeable: bool, sent: bool) -> None:
    """Every charged count 0..7, sends 0..21 and wall time around the limit: FAIL exactly at 6 charged, at 20 sends
    and at 24 h net of the hold ledger. Trials and refusals are never charged, and trials count toward the 20."""
    for charged, sends, (wall_ms, held_ms) in itertools.product(
        range(0, 8),
        range(0, 22),
        [(HOUR_MS, 0), (24 * HOUR_MS - 1, 0), (24 * HOUR_MS, 0), (49 * HOUR_MS, 48 * HOUR_MS), (49 * HOUR_MS, 24 * HOUR_MS)],
    ):
        prior = PauseState(since_ms=T0, charged=charged, sends=sends, trials=0, ledger_at_since=500)
        decision = pause_verdict(
            prior, sent=sent, trial=trial, chargeable=chargeable,
            now_ms=T0 + wall_ms, ledger_now=500 + held_ms, limits=LIMITS,
        )
        expected, exp_sends, exp_charged = _oracle(
            charged, sends, trial=trial, chargeable=chargeable, sent=sent, wall_ms=wall_ms - held_ms
        )
        assert decision.verdict is expected, (charged, sends, wall_ms, held_ms)
        assert decision.state.sends == exp_sends
        assert decision.state.charged == exp_charged
        assert decision.state.trials == prior.trials + (1 if sent and trial else 0)
        assert decision.verdict.is_fail == (expected in (Verdict.FAIL_WALL, Verdict.FAIL_ABSOLUTE))


def test_a_first_pause_starts_the_clock_and_records_the_ledger() -> None:
    decision = pause_verdict(
        None, sent=True, trial=False, chargeable=True, now_ms=T0, ledger_now=777, limits=LIMITS
    )
    assert decision.verdict is Verdict.PAUSE
    assert decision.state == PauseState(since_ms=T0, charged=1, sends=1, trials=0, ledger_at_since=777)


def test_the_sixth_charged_send_asks_the_fleet_whether_the_key_is_the_problem() -> None:
    prior = PauseState(since_ms=T0, charged=5, sends=5)
    asked = pause_verdict(prior, sent=True, trial=False, chargeable=True, now_ms=T0 + 1000, ledger_now=0)
    assert asked.verdict is Verdict.CAPPED


@pytest.mark.parametrize(("distinct", "verdict"), [(0, Verdict.FAIL_CHARGED), (1, Verdict.FAIL_CHARGED), (2, Verdict.FAIL_CHARGED), (3, Verdict.REDRIVE), (9, Verdict.REDRIVE)])
def test_the_fleet_hold_rule_fires_at_the_third_distinct_capped_job(distinct: int, verdict: Verdict) -> None:
    """Fewer than 3 distinct capped jobs: the document is the problem and the job FAILs (6 charged sends).
    The 3rd: the key is, and the job is redriven, not failed, with a fresh charge count."""
    prior = PauseState(since_ms=T0, charged=5, sends=7, trials=1)
    decision = pause_verdict(
        prior, sent=True, trial=False, chargeable=True, now_ms=T0 + 1000, ledger_now=0, capped_distinct=distinct
    )
    assert decision.verdict is verdict
    if verdict is Verdict.FAIL_CHARGED:
        assert "Reindex failed" in (decision.reason or "")
        assert decision.state.charged == 6
    else:
        assert decision.state.charged == 0
        assert (decision.state.sends, decision.state.trials) == (8, 1)  # sends and trials carry on


def test_a_trial_is_never_charged_and_a_refusal_is_not_a_send() -> None:
    prior = PauseState(since_ms=T0, charged=5, sends=5)
    trial = pause_verdict(prior, sent=True, trial=True, chargeable=True, now_ms=T0 + 1, ledger_now=0)
    assert (trial.verdict, trial.state.charged, trial.state.sends, trial.state.trials) == (Verdict.PAUSE, 5, 6, 1)
    refusal = pause_verdict(prior, sent=False, trial=False, chargeable=True, now_ms=T0 + 1, ledger_now=0)
    assert (refusal.verdict, refusal.state.charged, refusal.state.sends) == (Verdict.PAUSE, 5, 5)


def test_the_twentieth_send_fails_whatever_the_charging() -> None:
    prior = PauseState(since_ms=T0, charged=0, sends=19)
    decision = pause_verdict(prior, sent=True, trial=False, chargeable=False, now_ms=T0 + 1, ledger_now=0)
    assert decision.verdict is Verdict.FAIL_ABSOLUTE
    assert "20 times" in (decision.reason or "") and "Reindex failed" in (decision.reason or "")


def test_the_wall_is_net_of_the_hold_ledger() -> None:
    """A job that slept through a 48 h hold has the hold subtracted: it does not FAIL on wake."""
    prior = PauseState(since_ms=T0, charged=0, sends=3, ledger_at_since=1_000)
    now = T0 + 50 * HOUR_MS
    held = wall_seconds_used(prior, now, 1_000 + 48 * HOUR_MS)
    assert held == pytest.approx(2 * 3600)
    assert pause_verdict(prior, sent=False, trial=False, chargeable=False, now_ms=now, ledger_now=1_000 + 48 * HOUR_MS).verdict is Verdict.PAUSE
    assert pause_verdict(prior, sent=False, trial=False, chargeable=False, now_ms=now, ledger_now=1_000).verdict is Verdict.FAIL_WALL


def test_the_ledger_can_never_subtract_more_than_the_time_that_passed() -> None:
    prior = PauseState(since_ms=T0, ledger_at_since=0)
    assert wall_seconds_used(prior, T0 + 1000, 10**12) == 0
    assert wall_seconds_used(prior, T0 + 1000, -5) == 1.0


def test_a_budget_spent_is_judged_before_the_job_runs_again() -> None:
    assert budget_spent(PauseState(since_ms=T0, charged=6, sends=6), T0 + 5, 0) is Verdict.FAIL_CHARGED
    assert budget_spent(PauseState(since_ms=T0, charged=0, sends=20), T0 + 5, 0) is Verdict.FAIL_ABSOLUTE
    assert budget_spent(PauseState(since_ms=T0), T0 + 25 * HOUR_MS, 0) is Verdict.FAIL_WALL
    assert budget_spent(PauseState(since_ms=T0, charged=5, sends=19), T0 + 5, 0) is None


def test_limits_come_from_the_environment_and_ignore_nonsense(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAGE_PAUSE_SENDS", "4")
    monkeypatch.setenv("STAGE_PAUSE_WALL_SECONDS", "3600")
    limits = PauseLimits.from_env()
    assert (limits.charged_sends, limits.wall_seconds, limits.absolute_sends) == (4, 3600, 20)
    monkeypatch.setenv("STAGE_PAUSE_SENDS", "many")
    monkeypatch.setenv("STAGE_PAUSE_WALL_SECONDS", "-1")
    limits = PauseLimits.from_env()
    assert (limits.charged_sends, limits.wall_seconds) == (6, 86400)


# ---- live configuration ------------------------------------------------------------------------------------


def test_the_live_configuration_overrides_the_defaults_and_ignores_nonsense() -> None:
    base = PauseLimits()
    live = base.with_config({"pause": {"sends": 4, "absoluteSends": 12, "wallSeconds": 7200}})
    assert (live.charged_sends, live.absolute_sends, live.wall_seconds) == (4, 12, 7200)
    odd = base.with_config({"pause": {"sends": "many", "absoluteSends": 0, "wallSeconds": True}})
    assert odd == base
    assert base.with_config(None) == base and base.with_config({"pause": 3}) == base


async def test_the_judge_reads_its_limits_live_and_survives_a_failing_read() -> None:
    from app.modules.pipeline.pause_judge import PauseJudge, spend_limits_source
    from app.modules.pipeline.pause_store import PauseStore

    calls = {"n": 0}

    class Config:
        cfg: object = {"pause": {"sends": 2}}

        async def get_config(self, key: str, **_: object) -> object:
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("store down")
            return self.cfg

    config = Config()
    judge = PauseJudge(PauseStore(), limits_source=spend_limits_source(config))
    assert (await judge.limits()).charged_sends == 2
    config.cfg = {"pause": {"sends": 9}}
    assert (await judge.limits()).charged_sends == 9
    assert (await judge.limits()).charged_sends == 9, "a failing read keeps the last known limits"


def test_sends_are_requests_on_the_wire() -> None:
    """One PAUSED outcome that took three requests (SDK retries, a gateway 429 retry) is three sends."""
    prior = PauseState(since_ms=T0, sends=5)
    decision = pause_verdict(prior, sent=True, trial=False, chargeable=False, now_ms=T0 + 1, ledger_now=0, requests=3)
    assert decision.state.sends == 8
    assert pause_verdict(prior, sent=True, trial=False, chargeable=False, now_ms=T0 + 1, ledger_now=0).state.sends == 6
    assert pause_verdict(prior, sent=False, trial=False, chargeable=False, now_ms=T0 + 1, ledger_now=0, requests=3).state.sends == 5
    atcap = pause_verdict(PauseState(since_ms=T0, sends=18), sent=True, trial=False, chargeable=False, now_ms=T0 + 1, ledger_now=0, requests=3)
    assert atcap.verdict is Verdict.FAIL_ABSOLUTE
