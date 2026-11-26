"""Puts ``pause_verdict`` to work for a stage worker: reads and writes the pause state, asks the fleet whether
the job's key is the problem, and starts a hold when it is.

* ``prior`` / ``spent``: before a job runs, what it has already used.
* ``judge``: after a PAUSED outcome, the verdict. At the charged cap it registers the job as capped on its
  key. If that makes three distinct jobs, the key goes on hold, the job is redriven instead of failed, and the
  jobs of the window that were already failed are returned for the worker to redrive.

Every method that reads Redis raises ``PauseStoreUnavailable``; ``judge`` never raises for it, because the job
is already paused and the next wake is deferred while Redis stays unreadable.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from app.modules.pipeline.pause_budget import (
    PauseDecision,
    PauseLimits,
    PauseState,
    Verdict,
    budget_spent,
    counters_spent,
    decide,
    fail_reason,
    outcome_counts,
)
from app.modules.pipeline.pause_store import (
    PauseStore,
    PauseStoreUnavailable,
    ResetResult,
)
from app.services.llm_gateway.fleet_breaker import (
    HOLD_COOLDOWN_MS,
    FleetBreaker,
    FleetBreakerUnavailable,
)
from app.services.llm_gateway.fleet_hold import HoldEntry, HoldKeeper
from app.services.llm_gateway.fleet_redis import get_fleet_redis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.modules.pipeline.models import StageJob, StageResult
    from app.services.llm_gateway.gateway import JobTrace

@dataclass(frozen=True)
class Judgement:
    decision: PauseDecision
    # Jobs of the hold window that were already FAILED by the charged cap: the worker redrives them.
    redrive: tuple[str, ...] = field(default_factory=tuple)
    # Extra seconds a redriven job waits, spread over the hold's cooldown so released jobs do not arrive in step.
    jitter_s: float = 0.0
    # This verdict put the key on hold (it is the third distinct capped job): an episode begins.
    newly_held: bool = False
    # The key scope (``pk:cls``) the redrives are owed under, and the hold episode they are owed for.
    scope: str = ""
    episode: str = ""


class PauseJudge:
    def __init__(
        self,
        store: PauseStore,
        *,
        limits: PauseLimits | None = None,
        clock_ms: Callable[[], int] | None = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
        breaker: FleetBreaker | None = None,
        limits_source: Callable[[], Awaitable[PauseLimits]] | None = None,
    ) -> None:
        self._limits_source = limits_source
        self._last_limits: PauseLimits | None = None
        self._store = store
        self._breaker_override = breaker
        self._limits = limits or PauseLimits.from_env()
        self._clock_ms = clock_ms
        self._logger = logger or logging.getLogger(__name__)
        self._rng = rng or random.Random()

    async def limits(self) -> PauseLimits:
        """The caps in force: the live configuration when a source is set (it is cached by the config service), else
        the environment defaults this judge was built with."""
        if self._limits_source is not None:
            try:
                self._last_limits = await self._limits_source()
            except Exception:
                self._logger.warning("could not read the pause limits; using the last known", exc_info=True)
            return self._last_limits or self._limits
        return self._limits

    def _breaker(self) -> FleetBreaker | None:
        if self._breaker_override is not None:
            return self._breaker_override
        handle = get_fleet_redis()
        return FleetBreaker(handle, clock_ms=None) if handle is not None else None

    async def _now(self) -> int:
        """The clock of every budget decision: Redis ``TIME`` (the breaker and the hold ledger run on it), so replicas
        whose host clocks differ agree. An injected clock (tests, the simulation) replaces it; with no Redis bound, the host's."""
        if self._clock_ms is not None:
            return self._clock_ms()
        breaker = self._breaker()
        if breaker is None:
            return int(time.time() * 1000)
        try:
            return await breaker.now_ms()
        except FleetBreakerUnavailable as exc:
            raise PauseStoreUnavailable(str(exc)) from exc

    async def _ledger(self, scope: str) -> int:
        """Hold time so far of the key ``scope`` (``pk:cls``), which the recovery leader advances while the key is held.
        0 for no key, or when no fleet Redis is bound."""
        breaker = self._breaker()
        if breaker is None or not scope:
            return 0
        try:
            return await breaker.ledger(scope)
        except FleetBreakerUnavailable as exc:
            raise PauseStoreUnavailable(str(exc)) from exc

    @property
    def available(self) -> bool:
        """Whether a Redis is bound for the budget at all. Without one there is no budget to keep (and no fleet breaker);
        with one that cannot be read, a job waits."""
        return self._store.available

    async def prior(self, state_key: str) -> PauseState | None:
        return await self._store.get(state_key)

    async def spent(self, job: StageJob, prior: PauseState | None) -> tuple[Verdict, str] | None:
        """Whether a revision that has paused before has used up its budget (judged before it runs again). The counter
        caps are judged before the hold ledger is read, so a known cap holds even when the ledger cannot be read; that
        read raises ``PauseStoreUnavailable``, which the caller must not take for "not spent"."""
        if prior is None:
            return None
        limits = await self.limits()
        verdict = counters_spent(prior, limits)
        episode = (
            await self._store.owed_episode(prior.scope, job.state_key)
            if verdict is Verdict.FAIL_CHARGED and prior.scope else None
        )
        if episode is not None:
            # A hold owes this job a redrive whose reset has not happened yet: its charges were the key's, not its own.
            # The scope is indexed first, so the leader also finds the other jobs it owes, which do not wake by themselves.
            await self._store.note_owed_scope(prior.scope)
            reset = await self._store.release_charged(job.state_key, episode)
            await self._store.redrive_done(prior.scope, job.state_key)
            if reset is ResetResult.RESET:
                prior = replace(prior, charged=0)
            else:
                # ALREADY: the reset was done before, possibly after ``prior`` was read, so judge the budget as stored
                # now: what it holds is the new run's own.
                prior = await self._store.get(job.state_key) or replace(prior, charged=0)
            verdict = counters_spent(prior, limits)
        if verdict is Verdict.FAIL_CHARGED and episode is None and prior.scope:
            # The charged cap was reached by runs that did not pause (a failed request inside an attempt, then no pause):
            # the job is counted toward the key's hold like a PAUSED outcome at the cap, so it is not failed while the
            # key is the problem. The other jobs it redrives are owed durably (the recovery leader sends them).
            pk, _, size_class = prior.scope.rpartition(":")
            now = await self._now()
            decision, _, _, _ = await self._ask_hold(
                job, prior, pk, size_class, None, now,
                {"now_ms": now, "ledger_now": await self._ledger(prior.scope), "limits": limits},
            )
            if decision.verdict is Verdict.REDRIVE:
                prior = (await self._store.get(job.state_key)) or replace(prior, charged=0)
                verdict = counters_spent(prior, limits)
        if verdict is None:
            ledger = await self._ledger(prior.scope)
            verdict = budget_spent(prior, await self._now(), ledger, limits)
        if verdict is None:
            return None
        return verdict, fail_reason(verdict, prior, limits)

    async def clear(self, state_key: str, *, attempt: int) -> None:
        """The revision completed or was skipped as ``attempt``: its budget starts over, unless a later attempt has
        written it since (a redrive that claimed the revision meanwhile keeps its own counts)."""
        try:
            await self._store.clear(state_key, attempt=attempt)
        except PauseStoreUnavailable:
            self._logger.warning("could not clear the pause budget of %s; it expires on its own", state_key)

    async def judge(self, job: StageJob, result: StageResult, *, attempt: int = 0) -> Judgement | None:
        """The verdict of one PAUSED outcome of run ``attempt``; None when the budget cannot be read (the job just pauses)."""
        try:
            return await self._judge(job, result, attempt)
        except (PauseStoreUnavailable, FleetBreakerUnavailable) as exc:
            self._logger.warning("pause budget of %s not updated: %s", job.state_key, exc)
            return None

    async def _judge(self, job: StageJob, result: StageResult, attempt: int) -> Judgement:
        now = await self._now()
        scope = f"{result.provider_pk}:{result.size_class}" if result.provider_pk and result.size_class else ""
        # Read only to pick the ledger a new budget starts from; the counts are added atomically below.
        prior = await self._store.get(job.state_key)
        counts = outcome_counts(
            sent=result.sent, trial=result.trial, chargeable=result.chargeable, requests=result.requests,
            charged=result.charged,
        )
        state = await self._store.apply(
            job.state_key,
            attempt=attempt,
            sends=counts.sends,
            charged=counts.charged,
            trials=counts.trials,
            since_ms=now,
            ledger_at_since=await self._ledger(prior.scope if prior and prior.scope else scope),
            scope=scope,
            scope_ledger=await self._ledger(scope),
        )
        limits = await self.limits()
        decide_kwargs = {
            "now_ms": now, "ledger_now": await self._ledger(state.scope), "limits": limits, "provider": result.provider_label,
        }
        decision = decide(state, **decide_kwargs)
        redrive: tuple[str, ...] = ()
        jitter = 0.0
        newly_held = False
        capped_episode = ""
        if decision.verdict is Verdict.CAPPED:
            held = await self._ask_hold(job, state, result.provider_pk, result.size_class, result.provider_label, now, decide_kwargs)
            decision, redrive, newly_held, capped_episode = held
            if decision.verdict is Verdict.REDRIVE:
                jitter = self._rng.uniform(0, HOLD_COOLDOWN_MS / 1000.0)
        return Judgement(decision, redrive, jitter, newly_held, scope, episode=capped_episode)

    async def _ask_hold(
        self, job: StageJob, state: PauseState, pk: str | None, size_class: str | None, label: str | None, now: int,
        decide_kwargs: dict[str, Any],
    ) -> tuple[PauseDecision, tuple[str, ...], bool, str]:
        """The decision for a job at the charged cap: count it as capped on its key, and when it is the third distinct job the
        key is held and the job is redriven with a fresh count. Returns the decision, the other jobs the hold redrives, whether
        this call opened the hold, and the hold's episode. The breaker's script has already made every capped job of the window
        owed a redrive, this one included, atomically with the hold; what follows can fail without losing any of them."""
        breaker = self._breaker()
        if breaker is None or not pk or not size_class:
            return decide(state, capped_distinct=0, **decide_kwargs), (), False, ""
        label = label or "model provider"
        try:
            capped = await breaker.register_capped(pk, size_class, job.state_key, label=label, reason="charged_cap", org=job.org_id)
        except FleetBreakerUnavailable as exc:
            raise PauseStoreUnavailable(str(exc)) from exc
        decision = decide(state, capped_distinct=0 if capped.already else capped.distinct, **decide_kwargs)
        if decision.verdict is not Verdict.REDRIVE:
            return decision, (), False, ""
        await self._settle_own_redrive(job, f"{pk}:{size_class}", capped.episode)
        if capped.newly_held:
            await self._note_hold(breaker, job, pk, size_class, capped.episode, label, len(capped.others) + 1, now)
        return decision, capped.others, capped.newly_held, capped.episode

    async def _settle_own_redrive(self, job: StageJob, scope: str, episode: str) -> None:
        """This job is owed a fresh charge count: give it now, or leave it owed for its next wake (``spent``) or the
        recovery leader, either of which resets it before it can be failed at the old count."""
        try:
            await self._store.note_owed_scope(scope)
            await self._store.release_charged(job.state_key, episode)
            await self._store.redrive_done(scope, job.state_key)
        except PauseStoreUnavailable as exc:
            self._logger.warning("the charge count of %s is reset later (%s): it stays owed", job.state_key, exc)

    async def _note_hold(
        self, breaker: FleetBreaker, job: StageJob, pk: str, size_class: str, episode: str, label: str, redriven: int, now: int
    ) -> None:
        try:
            await HoldKeeper(breaker.fleet, breaker).note_hold(
                HoldEntry(pk, size_class, episode, label, now, job.org_id, redriven)
            )
        except Exception:
            self._logger.warning("could not index the provider hold %s; the next tick will not see it", episode, exc_info=True)

    async def reserve(self, job: StageJob, *, attempt: int, stale_ms: int) -> int:
        """The failed sends run ``attempt`` may make (``PauseStore.reserve``), held for it until it commits."""
        limits = await self.limits()
        return await self._store.reserve(
            job.state_key, attempt=attempt, absolute=limits.absolute_sends, now_ms=await self._now(), stale_ms=stale_ms
        )

    async def commit(self, job: StageJob, *, attempt: int, trace: JobTrace | None) -> None:
        """Commit a run that did not pause: its failed sends, charged sends and trials, and release its reservation. A
        run that spent nothing creates no budget."""
        scope = trace.scope() if trace is not None else None
        scope_key = f"{scope[0]}:{scope[1]}" if scope else ""
        try:
            scope_ledger = await self._ledger(scope_key)
            await self._store.apply(
                job.state_key,
                attempt=attempt,
                sends=trace.requests if trace is not None else 0,
                charged=trace.charged if trace is not None else 0,
                trials=trace.trials if trace is not None else 0,
                since_ms=await self._now(),
                # A budget this commit creates starts at the key's ledger now, so hold time before it is not subtracted.
                ledger_at_since=scope_ledger,
                scope=scope_key,
                scope_ledger=scope_ledger,
                create=False,
            )
        except PauseStoreUnavailable as exc:
            self._logger.warning("the sends of %s were not committed (%s); its reservation counts them", job.state_key, exc)

    async def release_charged(self, state_key: str, episode: str = "") -> ResetResult:
        """A fresh charge count for a job a hold redrives. Raises ``PauseStoreUnavailable``: a redrive must not go out
        with the old count, which the pre-run check would fail at once."""
        return await self._store.release_charged(state_key, episode)

    async def pending_redrives(self) -> dict[str, list[tuple[str, str]]]:
        return await self._store.pending_redrives()

    async def redrive_done(self, scope: str, state_key: str) -> None:
        await self._store.redrive_done(scope, state_key)

    async def reconcile_owed(self) -> int:
        return await self._store.reconcile_owed()

    async def hold_jitter(self, result: StageResult) -> float:
        """A paused job refused by a key on hold waits a random part of the hold's cooldown on top, so the
        jobs the hold redrives do not all wake together."""
        if result.sent or not result.provider_pk or not result.size_class:
            return 0.0
        breaker = self._breaker()
        if breaker is None:
            return 0.0
        try:
            hold = await breaker.hold(result.provider_pk, result.size_class)
        except FleetBreakerUnavailable:
            return 0.0
        return self._rng.uniform(0, HOLD_COOLDOWN_MS / 1000.0) if hold else 0.0


SPEND_GUARD_KEY = "/services/indexing/spendGuard"


def spend_limits_source(config_service: object, base: PauseLimits | None = None) -> Callable[[], Awaitable[PauseLimits]]:
    """The pause caps from the live configuration: the ``pause`` object of ``/services/indexing/spendGuard``, read through
    the configuration service's cache (invalidated on save, at most 10 s old), over the environment defaults. Every replica
    reads the same key, so replicas agree on a verdict."""
    defaults = base or PauseLimits.from_env()

    async def read() -> PauseLimits:
        config = await config_service.get_config(SPEND_GUARD_KEY, use_cache=True, max_age_seconds=10)  # type: ignore[attr-defined]
        return defaults.with_config(config)

    return read
