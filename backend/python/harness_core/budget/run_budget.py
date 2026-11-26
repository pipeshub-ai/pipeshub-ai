"""ADR-RUN-01 D4: the run budget tree, the one place a run's time, money, tokens and steps are worked out.

A ``RunBudget`` is created once, at root admission: the run's deadline, its USD cap (the real limit, decision of
2026-10-24), its token cap and step cap (backstops), and the working time one engine turn may take. A child gets a slice
of its parent's (``child``): its deadline is never later than the parent's less the parent's answer reserve, and its
money comes out of the parent's (the ledger tree holds the money itself, ``harness_core.budget.tree``). Node pins every
child with its root's deadline and moves the whole tree's together through a person's wait (WAITCHILD), so a run's
budget is its root's taken down ``depth`` levels (``of_claim``).

From a turn's slice (``Allowance``) and the step times the turn observes comes its phase plan: gather until
``write_by``, write until ``stop_by``, then deliver. The writing reserve covers at least two of the turn's longest model
steps so far (WP-81); the delivery reserve keeps the turn's closing save, the run's finish and the hand-back of its
answer before the run's deadline (WP-82). The engine is told the plan in its brief and steered at ``write_by``; the
hard stop at ``stop_by`` is the last resort.

``allowance.py`` words what is decided here; the engine controller, the engine and loop kernels and the tool time
bound read these numbers and compute none of their own.
"""

from __future__ import annotations

import contextlib
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from harness_core.budget.answer_size import WRAP_UP_OUTPUT_TOKENS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from harness_core.engines.types import RunLimits
    from harness_core.journal.types import Claim

# A metered run's step backstop: this many model steps per USD of its budget, at least ``MIN_BUDGET_STEPS`` (WP-70).
STEPS_PER_USD: Final = 100
MIN_BUDGET_STEPS: Final = 50
# The share of each limit kept back for writing, and its floors (WP-70).
SYNTHESIS_SHARE: Final = 0.15
MIN_SYNTHESIS_STEPS: Final = 8
MIN_SYNTHESIS_WALL_S: Final = 120
# WP-81: the writing reserve on the wall clock is at least this many of the turn's longest model step so far. A steer
# reaches the engine only when the step under way ends, and the writing takes at least one more step: a long research run's
# 270-second reserve opened 47 seconds into a 318-second step, so the engine never read it before the limit.
WALL_RESERVE_STEPS: Final = 2
# WP-70: an engine with its own step bound (opencode, ACP agents) gets the profile's steps plus this, so the
# controller's limit binds first and the turn is cut with its wrap-up and writing reserve.
NATIVE_STEP_HEADROOM: Final = 2
# WP82-FU-3: the two reserves are what an answer-only turn takes, measured, not chosen. Both such turns (a cut engine
# turn's wrap-up, a loop's answer when its time runs out) answer at most ``WRAP_UP_OUTPUT_TOKENS``. A long research run's
# writing steps ran at 35-45 output tokens a second (its 8,000-token wrap-up took 162 s), so the slowest measured
# rate bounds the model's part. The harness's own part was measured on the durable e2e lane (scripted model and
# engine, six parent and engine-child runs): an engine's exit to its run completed (closing save, checkpoint,
# finish, hand-back) 2.3-3.7 s; a child's completion to its parent's answer committed 0.6-1.2 s.
SLOWEST_OUTPUT_TOKENS_PER_S: Final = 35.0
DELIVERY_HARNESS_S: Final = 4.0
ANSWER_HARNESS_S: Final = 2.0


def answer_turn_s(harness_s: float, output_tokens: int = WRAP_UP_OUTPUT_TOKENS) -> int:
    """Whole seconds an answer-only turn of ``output_tokens`` takes at the slowest measured rate, plus ``harness_s``."""
    return math.ceil(output_tokens / SLOWEST_OUTPUT_TOKENS_PER_S + harness_s)


# WP-82: what a turn leaves of its run's deadline for what comes after the engine: its wrap-up answer when it was
# cut, the closing save, the run's finish and the hand-back of what it delivered. Run 6's re-driven child got a fresh
# 30 minutes 23 minutes before its deadline, and the deadline ended it mid-writing. 233 s (it was a fixed 180 s, below
# a slow model's 8,000-token wrap-up).
DELIVERY_RESERVE_S: Final = answer_turn_s(DELIVERY_HARNESS_S)
# The least working time a turn is given when its run's deadline is near: enough to write from what it has.
MIN_WORKING_S: Final = 60
# What a parent keeps of its own deadline for its answer from what its child delivered: a loop takes its answer-only
# turn this long before its deadline (BUDGETEND), so a child's deadline is its parent's less this (D4). 231 s (it was
# a fixed 120 s, which a long answer at the slowest measured rate overran).
ANSWER_RESERVE_S: Final = float(answer_turn_s(ANSWER_HARNESS_S))

SynthesisReason = Literal["steps", "wall_time", "budget"]


def step_backstop(profile_steps: int | None, usd: float | None, *, metered: bool) -> int | None:
    """The turn's step limit: for a metered run with a USD budget, ``STEPS_PER_USD`` steps a dollar (at least
    ``MIN_BUDGET_STEPS``) and never more than its profile allows; otherwise its profile's (``None``: unbounded)."""
    if not metered or usd is None or usd <= 0:
        return profile_steps
    scaled = max(MIN_BUDGET_STEPS, math.ceil(usd * STEPS_PER_USD))
    return scaled if profile_steps is None else min(profile_steps, scaled)


def native_steps(profile_steps: int | None, default: int) -> int:
    """The step bound rendered into an engine's own configuration: a little above the controller's limit."""
    return (profile_steps or default) + NATIVE_STEP_HEADROOM


def working_s(wall_s: int | None, deadline_left_s: float | None) -> int | None:
    """A turn's working time: ``wall_s`` (the profile's), never past the run's deadline less ``DELIVERY_RESERVE_S``,
    and at least ``MIN_WORKING_S``; ``None`` when neither bounds it."""
    if deadline_left_s is None:
        return wall_s
    cap = max(MIN_WORKING_S, int(deadline_left_s) - DELIVERY_RESERVE_S)
    return cap if wall_s is None else min(wall_s, cap)


@dataclass(frozen=True, slots=True)
class PhasePlan:
    """One turn's time plan, in epoch seconds: gather until ``write_by_s``, write until ``stop_by_s`` (the hard stop),
    deliver before ``deliver_by_s`` (the run's own deadline less its parent's answer, when it has one)."""

    started_at_s: float
    write_by_s: float
    stop_by_s: float
    deliver_by_s: float | None = None

    @property
    def minutes(self) -> int:
        """The turn's working time in whole minutes, at least one."""
        return max(1, round((self.stop_by_s - self.started_at_s) / 60))


@dataclass(frozen=True, slots=True)
class Allowance:
    """What one engine turn may use: model steps, wall seconds, USD, raw tokens (each ``None`` when nothing bounds
    it)."""

    steps: int | None
    wall_s: int | None
    usd: float | None
    # The profile's raw-token ceiling for the turn (input plus output, cache reads counted whole): a backstop only.
    tokens: int | None = None

    @classmethod
    def of(cls, profile_steps: int | None, wall_s: int | None, usd: float | None, *, metered: bool) -> Allowance:
        return cls(step_backstop(profile_steps, usd, metered=metered), wall_s, usd if metered else None)

    def synthesis_steps(self) -> int | None:
        """Steps kept back for writing; ``None`` when steps are unbounded."""
        if self.steps is None:
            return None
        return min(self.steps - 1, max(MIN_SYNTHESIS_STEPS, math.ceil(self.steps * SYNTHESIS_SHARE)))

    def synthesis_wall_s(self, longest_step_s: float = 0.0) -> float | None:
        """Seconds kept back for writing, at least ``WALL_RESERVE_STEPS`` of the turn's longest model step so far
        (never more than half the time); ``None`` when time is unbounded."""
        if self.wall_s is None:
            return None
        share = max(MIN_SYNTHESIS_WALL_S, self.wall_s * SYNTHESIS_SHARE, WALL_RESERVE_STEPS * longest_step_s)
        return min(self.wall_s / 2, share)

    def synthesis_due(
        self, *, steps: int, elapsed_s: float, usd_left_share: float | None = None, longest_step_s: float = 0.0
    ) -> SynthesisReason | None:
        """The limit whose writing reserve the turn has reached, if any (steps first, then time, then money)."""
        reserve = self.synthesis_steps()
        if self.steps is not None and reserve is not None and steps >= self.steps - reserve:
            return "steps"
        wall = self.synthesis_wall_s(longest_step_s)
        if self.wall_s is not None and wall is not None and elapsed_s >= self.wall_s - wall:
            return "wall_time"
        if usd_left_share is not None and usd_left_share <= SYNTHESIS_SHARE:
            return "budget"
        return None

    def plan(
        self, started_at_s: float, *, longest_step_s: float = 0.0, deliver_by_s: float | None = None
    ) -> PhasePlan | None:
        """The turn's phase plan from ``started_at_s``; ``None`` when no working time bounds it."""
        wall = self.synthesis_wall_s(longest_step_s)
        if self.wall_s is None or wall is None:
            return None
        stop_by = started_at_s + self.wall_s
        return PhasePlan(started_at_s, stop_by - wall, stop_by, deliver_by_s)


_FRACTION = re.compile(r"(\.\d{6})\d+")


def _epoch_s(rfc3339: str) -> float | None:
    try:
        # At most microseconds: the contract allows nanoseconds, the parser does not.
        return datetime.fromisoformat(_FRACTION.sub(r"\1", rfc3339)).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RunBudget:
    """A run's budget: ``max_cost_usd`` and ``max_tokens`` are the caps its ledger account holds (the model grant pins
    them; the money is the real limit, the tokens a backstop); ``deadline_at_s`` is when its own work must be delivered
    (epoch seconds); ``depth`` is how far below its root the run is. ``None``: nothing bounds that dimension. An engine
    turn's own limits (steps, working time, raw tokens) are its profile's, applied here (``turn``)."""

    max_cost_usd: float | None
    max_tokens: int | None
    deadline_at_s: float | None = None
    depth: int = 0

    @classmethod
    def of_claim(cls, claim: Claim) -> RunBudget:
        """The budget of a claimed run. Node pins a child with its root's deadline and moves the tree's together, so
        the claim's deadline (Node's live one, which a person's wait moved) is the root's, taken down the run's
        depth; a journal that names none falls back to the spec's admission pin (``calendarDeadline``)."""
        budgets = claim.spec.budgets
        deadline = claim.deadline_at_ms / 1000 if claim.deadline_at_ms is not None else _epoch_s(budgets.calendar_deadline)
        budget = cls(max_cost_usd=budgets.max_cost_usd, max_tokens=budgets.max_tokens, deadline_at_s=deadline)
        for _ in range(max(0, claim.depth)):
            budget = budget.child()
        # The child's own caps are what Node pinned for it; only its time is derived from its root's.
        return replace(budget, max_cost_usd=budgets.max_cost_usd, max_tokens=budgets.max_tokens)

    def child(self, share: float | None = None) -> RunBudget:
        """A child's slice: its deadline is this run's less ``ANSWER_RESERVE_S`` (this run's answer from what the child
        delivered), its money and tokens ``share`` of this run's (all of them when ``None``: the ledger still takes
        what it spends out of this run's account)."""
        if share is not None and not 0 < share <= 1:
            raise ValueError(f"a child's share is above 0 and at most 1, got {share}")
        tokens = _share(self.max_tokens, share)
        return RunBudget(
            max_cost_usd=_share(self.max_cost_usd, share),
            max_tokens=None if tokens is None else max(1, int(tokens)),
            deadline_at_s=None if self.deadline_at_s is None else self.deadline_at_s - ANSWER_RESERVE_S,
            depth=self.depth + 1,
        )

    def left_s(self, now_s: float) -> float | None:
        """Seconds until the run's deadline; ``None`` when it has none."""
        return None if self.deadline_at_s is None else self.deadline_at_s - now_s

    def out_of_time(self, now_s: float) -> bool:
        """Too little is left for another working turn and an answer: a loop takes its answer-only turn now."""
        left = self.left_s(now_s)
        return left is not None and left <= ANSWER_RESERVE_S

    def turn(self, now_s: float, limits: RunLimits | None, *, metered: bool) -> Allowance:
        """What one engine turn starting at ``now_s`` may use under its profile's ``limits`` (WP-70): the money
        decides, the steps are a backstop scaled to it, and the working time never runs past the deadline less the
        delivery reserve (WP-82)."""
        steps = limits.max_steps if limits is not None else None
        wall = limits.max_wall_s if limits is not None else None
        allowance = Allowance.of(steps, working_s(wall, self.left_s(now_s)), self.max_cost_usd, metered=metered)
        return replace(allowance, tokens=limits.max_tokens if limits is not None else None)

    def plan(
        self, now_s: float, limits: RunLimits | None, *, metered: bool, longest_step_s: float = 0.0
    ) -> PhasePlan | None:
        """The phase plan of a turn starting at ``now_s``."""
        return self.turn(now_s, limits, metered=metered).plan(
            now_s, longest_step_s=longest_step_s, deliver_by_s=self.deadline_at_s
        )

    def bound_s(self, wall_s: float | None, now_s: float) -> float | None:
        """One bounded call's time limit: ``wall_s``, never past the run's deadline (a call already past it gets no
        time). A call that bounds nothing itself (a question to the person, a child's run) stays unbounded here."""
        left = self.left_s(now_s)
        if wall_s is None or left is None:
            return wall_s
        return max(0.0, min(wall_s, left))


def _share(value: float | int | None, share: float | None) -> float | None:
    if value is None:
        return None
    return float(value) if share is None else value * share


_SCOPE: ContextVar[tuple[RunBudget, Callable[[], float]] | None] = ContextVar("harness_run_budget", default=None)


@contextlib.contextmanager
def budget_scope(budget: RunBudget, now_s: Callable[[], float]) -> Iterator[None]:
    """The run whose work runs inside: its budget and its clock (epoch seconds). A tool call reads it for its time
    bound (``harness_core.tools.time_bound``)."""
    token = _SCOPE.set((budget, now_s))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scoped_bound_s(wall_s: float | None) -> float | None:
    """``wall_s`` bounded by the deadline of the run in scope (``RunBudget.bound_s``); unchanged outside a run."""
    scope = _SCOPE.get()
    if scope is None:
        return wall_s
    budget, now_s = scope
    return budget.bound_s(wall_s, now_s())


def scoped_now_s(fallback: Callable[[], float]) -> float:
    """Now on the clock of the run in scope, the one its deadline is on (``Claim.store_time``); ``fallback`` outside a
    run."""
    scope = _SCOPE.get()
    return fallback() if scope is None else scope[1]()


class PersonWaits:
    """The seconds a run spent waiting for a person (an approval, an answer) inside its attempt: its working time is
    paused while any wait is open (D4), however many run at once. ``monotonic``: the clock the turn measures with."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._closed_s = 0.0
        self._open = 0
        self._since = 0.0

    def waited_s(self) -> float:
        """All seconds waited so far, an open wait's included."""
        if self._open:
            return self._closed_s + self._monotonic() - self._since
        return self._closed_s

    @contextlib.asynccontextmanager
    async def waiting(self) -> AsyncIterator[None]:
        if self._open == 0:
            self._since = self._monotonic()
        self._open += 1
        try:
            yield
        finally:
            self._open -= 1
            if self._open == 0:
                self._closed_s += self._monotonic() - self._since


__all__ = [
    "ANSWER_RESERVE_S",
    "DELIVERY_RESERVE_S",
    "MIN_BUDGET_STEPS",
    "MIN_SYNTHESIS_STEPS",
    "MIN_SYNTHESIS_WALL_S",
    "MIN_WORKING_S",
    "NATIVE_STEP_HEADROOM",
    "STEPS_PER_USD",
    "SYNTHESIS_SHARE",
    "WALL_RESERVE_STEPS",
    "Allowance",
    "PersonWaits",
    "PhasePlan",
    "RunBudget",
    "SynthesisReason",
    "budget_scope",
    "native_steps",
    "scoped_bound_s",
    "scoped_now_s",
    "step_backstop",
    "working_s",
]
