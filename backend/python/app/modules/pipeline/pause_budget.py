"""How long a stage job may keep pausing on a model provider before it ends FAILED.

A job whose model call cannot be served ends PAUSED and is requeued. The provider being down is not
the job's fault, so an outage of any length must not fail it. But a document the provider chokes on
looks the same to one job, and an unbounded loop of paid retries on such a document is the cost
bug this module closes. ``pause_verdict`` is the pure decision; ``pause_store`` keeps the state.

What counts, per ``(virtual record, revision, stage)``:

* ``sends``: every request the job actually put on the wire (charged, uncharged and half-open
  trials). Capped at ``absolute_sends`` (20): the money bound for a document alone on its key.
* ``charged``: failed sends made while the key's breaker was closed and the provider was evidently
  serving other traffic (the caller decides that from the fleet's success rate), so the document is
  the likely cause. Capped at ``charged_sends`` (6). Trials and refusals are never charged.
* wall time since the first pause, **minus** the time the key was on hold (the hold ledger), capped at
  ``wall_seconds`` (24 h).

When a job reaches the charged cap while at least ``hold_jobs`` distinct jobs on the key did so in
the hold window, the key is the problem, not the documents: the verdict is ``REDRIVE`` (the job
keeps waiting with a fresh charge count), not ``FAIL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import Enum

CHARGED_SENDS = 6
ABSOLUTE_SENDS = 20
WALL_SECONDS = 24 * 3600
HOLD_JOBS = 3


@dataclass(frozen=True)
class PauseLimits:
    charged_sends: int = CHARGED_SENDS
    absolute_sends: int = ABSOLUTE_SENDS
    wall_seconds: int = WALL_SECONDS
    hold_jobs: int = HOLD_JOBS

    @classmethod
    def from_env(cls) -> PauseLimits:
        """``STAGE_PAUSE_SENDS`` (charged cap) and ``STAGE_PAUSE_WALL_SECONDS``. A malformed value keeps the default."""
        return cls(
            charged_sends=_positive_int("STAGE_PAUSE_SENDS", CHARGED_SENDS),
            wall_seconds=_positive_int("STAGE_PAUSE_WALL_SECONDS", WALL_SECONDS),
        )


    def with_config(self, config: object) -> PauseLimits:
        """These limits with the ``pause`` object of the live ``/services/indexing/spendGuard`` configuration laid over them
        (``sends``, ``absoluteSends``, ``wallSeconds``). A missing, malformed or non-positive value keeps the one below."""
        pause = config.get("pause") if isinstance(config, dict) else None
        if not isinstance(pause, dict):
            return self

        def pick(name: str, default: int) -> int:
            value = pause.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

        return replace(
            self,
            charged_sends=pick("sends", self.charged_sends),
            absolute_sends=pick("absoluteSends", self.absolute_sends),
            wall_seconds=pick("wallSeconds", self.wall_seconds),
        )


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class PauseState:
    """The persisted counters of one job's pauses (``messaging:pause:{vrid:rev:stage}``)."""

    since_ms: int
    charged: int = 0
    sends: int = 0
    trials: int = 0
    # The key's hold ledger when the revision first paused; ``wall`` excludes the hold since.
    ledger_at_since: int = 0
    # The key whose hold ledger this wall excludes (``<pk>:<cls>``), learned from the first pause that names one.
    scope: str = ""


class Verdict(str, Enum):
    PAUSE = "pause"
    # The charged cap is reached and the fleet-hold rule has not been asked yet: the caller
    # registers the job as capped and asks again with the distinct count.
    CAPPED = "capped"
    REDRIVE = "redrive"
    FAIL_CHARGED = "fail_charged"
    FAIL_ABSOLUTE = "fail_absolute"
    FAIL_WALL = "fail_wall"

    @property
    def is_fail(self) -> bool:
        return self in (Verdict.FAIL_CHARGED, Verdict.FAIL_ABSOLUTE, Verdict.FAIL_WALL)


@dataclass(frozen=True)
class PauseDecision:
    verdict: Verdict
    state: PauseState
    reason: str | None = None


def wall_seconds_used(state: PauseState, now_ms: int, ledger_now: int) -> float:
    """Wall time since the first pause, net of the time a key hold kept the job waiting."""
    held = min(max(0, ledger_now - state.ledger_at_since), max(0, now_ms - state.since_ms))
    return (now_ms - state.since_ms - held) / 1000.0


@dataclass(frozen=True)
class OutcomeCounts:
    """What one PAUSED outcome adds to its job's budget."""

    sends: int
    charged: int
    trials: int


def outcome_counts(
    *, sent: bool, trial: bool, chargeable: bool, requests: int | None = None, charged: int | None = None
) -> OutcomeCounts:
    """``sent``: the failed call reached the wire. ``requests``: the failed sends behind the outcome (None: one when
    sent). ``trial``: it was a half-open trial (never charged). ``charged``: its chargeable failed sends, each judged on
    its own with trials already left out (None: one when the outcome was a chargeable send that was not a trial)."""
    return OutcomeCounts(
        sends=(requests if requests is not None else 1) if sent else 0,
        charged=charged if charged is not None else (1 if sent and chargeable and not trial else 0),
        trials=1 if sent and trial else 0,
    )


def decide(
    state: PauseState,
    *,
    now_ms: int,
    ledger_now: int,
    limits: PauseLimits = PauseLimits(),
    capped_distinct: int | None = None,
    provider: str | None = None,
) -> PauseDecision:
    """What to do about a job whose budget, after its latest outcome, is ``state``. ``capped_distinct`` is the number of
    distinct jobs capped on the key within the hold window (this job included) once the caller has registered this one;
    while it is None a job at the charged cap gets ``CAPPED`` so the caller can ask."""
    if wall_seconds_used(state, now_ms, ledger_now) >= limits.wall_seconds:
        return PauseDecision(Verdict.FAIL_WALL, state, fail_reason(Verdict.FAIL_WALL, state, limits, provider))
    if state.sends >= limits.absolute_sends:
        return PauseDecision(Verdict.FAIL_ABSOLUTE, state, fail_reason(Verdict.FAIL_ABSOLUTE, state, limits, provider))
    if state.charged >= limits.charged_sends:
        if capped_distinct is None:
            return PauseDecision(Verdict.CAPPED, state)
        if capped_distinct >= limits.hold_jobs:
            return PauseDecision(Verdict.REDRIVE, replace(state, charged=0))
        return PauseDecision(Verdict.FAIL_CHARGED, state, fail_reason(Verdict.FAIL_CHARGED, state, limits, provider))
    return PauseDecision(Verdict.PAUSE, state)


def pause_verdict(
    prior: PauseState | None,
    *,
    sent: bool,
    trial: bool,
    chargeable: bool,
    now_ms: int,
    ledger_now: int,
    limits: PauseLimits = PauseLimits(),
    capped_distinct: int | None = None,
    provider: str | None = None,
    scope: str = "",
    scope_ledger_now: int = 0,
    requests: int | None = None,
    charged: int | None = None,
) -> PauseDecision:
    """The state after one PAUSED outcome, and what to do about it: ``outcome_counts`` added to ``prior`` (the rule the
    pause store applies atomically), then ``decide``."""
    state = prior or PauseState(since_ms=now_ms, ledger_at_since=ledger_now, scope=scope)
    if scope and not state.scope:
        # The key became known after the first pause: hold time before now is not subtracted retroactively.
        state = replace(state, scope=scope, ledger_at_since=state.ledger_at_since + scope_ledger_now)
    counts = outcome_counts(sent=sent, trial=trial, chargeable=chargeable, requests=requests, charged=charged)
    state = replace(
        state, sends=state.sends + counts.sends, trials=state.trials + counts.trials, charged=state.charged + counts.charged
    )
    return decide(state, now_ms=now_ms, ledger_now=ledger_now, limits=limits, capped_distinct=capped_distinct, provider=provider)


def fail_reason(verdict: Verdict, state: PauseState, limits: PauseLimits, provider: str | None = None) -> str:
    """The reason a revision ends FAILED by budget, as the record shows it."""
    what = f" ({provider})" if provider else ""
    if verdict is Verdict.FAIL_WALL:
        return f"The model provider{what} was unavailable for {limits.wall_seconds / 3600:g} h. Use Reindex failed to try again."
    if verdict is Verdict.FAIL_ABSOLUTE:
        return (
            f"This document was sent to the model {state.sends} times without success{what}. "
            "Use Reindex failed to try again."
        )
    return (
        f"The model kept failing on this document while serving other requests ({state.charged} times). "
        "Use Reindex failed to try again."
    )


def counters_spent(state: PauseState, limits: PauseLimits = PauseLimits()) -> Verdict | None:
    """The caps that need no clock or ledger: judged first, so they hold when the ledger cannot be read."""
    if state.sends >= limits.absolute_sends:
        return Verdict.FAIL_ABSOLUTE
    if state.charged >= limits.charged_sends:
        return Verdict.FAIL_CHARGED
    return None


def budget_spent(state: PauseState, now_ms: int, ledger_now: int, limits: PauseLimits = PauseLimits()) -> Verdict | None:
    """Whether a revision that has paused before has used its budget, judged before it is run again.

    A non-forced re-dispatch of a revision that ended FAILED by budget must not send anything.
    """
    if wall_seconds_used(state, now_ms, ledger_now) >= limits.wall_seconds:
        return Verdict.FAIL_WALL
    if state.sends >= limits.absolute_sends:
        return Verdict.FAIL_ABSOLUTE
    if state.charged >= limits.charged_sends:
        return Verdict.FAIL_CHARGED
    return None
