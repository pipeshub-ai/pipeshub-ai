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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.modules.pipeline.pause_budget import (
    PauseDecision,
    PauseLimits,
    PauseState,
    Verdict,
    budget_spent,
    fail_reason,
    pause_verdict,
)
from app.modules.pipeline.pause_store import PauseStore, PauseStoreUnavailable
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

@dataclass(frozen=True)
class Judgement:
    decision: PauseDecision
    # Jobs of the hold window that were already FAILED by the charged cap: the worker redrives them.
    redrive: tuple[str, ...] = field(default_factory=tuple)
    # Extra seconds a redriven job waits, spread over the hold's cooldown so released jobs do not arrive in step.
    jitter_s: float = 0.0
    # This verdict put the key on hold (it is the third distinct capped job): an episode begins.
    newly_held: bool = False


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

    async def prior(self, state_key: str) -> PauseState | None:
        return await self._store.get(state_key)

    async def spent(self, job: StageJob, prior: PauseState | None) -> tuple[Verdict, str] | None:
        """Whether a revision that has paused before has used up its budget (judged before it runs again)."""
        if prior is None:
            return None
        ledger = await self._ledger(prior.scope)
        verdict = budget_spent(prior, await self._now(), ledger, await self.limits())
        if verdict is None:
            return None
        return verdict, fail_reason(verdict, prior, await self.limits())

    async def clear(self, state_key: str) -> None:
        """The revision completed or was skipped: its budget starts over."""
        try:
            await self._store.delete(state_key)
        except PauseStoreUnavailable:
            self._logger.warning("could not clear the pause budget of %s; it expires on its own", state_key)

    async def judge(self, job: StageJob, result: StageResult) -> Judgement | None:
        """The verdict of one PAUSED outcome; None when the budget cannot be read (the job just pauses)."""
        try:
            return await self._judge(job, result)
        except (PauseStoreUnavailable, FleetBreakerUnavailable) as exc:
            self._logger.warning("pause budget of %s not updated: %s", job.state_key, exc)
            return None

    async def _judge(self, job: StageJob, result: StageResult) -> Judgement:
        prior = await self._store.get(job.state_key)
        now = await self._now()
        scope = f"{result.provider_pk}:{result.size_class}" if result.provider_pk and result.size_class else ""
        ledger = await self._ledger(prior.scope if prior and prior.scope else scope)
        scope_ledger = await self._ledger(scope)
        kwargs = {
            "sent": result.sent, "trial": result.trial, "chargeable": result.chargeable,
            "now_ms": now, "ledger_now": ledger, "limits": await self.limits(), "provider": result.provider_label,
            "scope": scope, "scope_ledger_now": scope_ledger, "requests": result.requests, "charged": result.charged,
        }
        decision = pause_verdict(prior, **kwargs)
        redrive: tuple[str, ...] = ()
        jitter = 0.0
        newly_held = False
        if decision.verdict is Verdict.CAPPED:
            breaker = self._breaker()
            if breaker is None or not result.provider_pk or not result.size_class:
                decision = pause_verdict(prior, capped_distinct=0, **kwargs)
            else:
                label = result.provider_label or "model provider"
                capped = await breaker.register_capped(
                    result.provider_pk, result.size_class, job.state_key, label=label, reason="charged_cap"
                )
                decision = pause_verdict(prior, capped_distinct=0 if capped.already else capped.distinct, **kwargs)
                if decision.verdict is Verdict.REDRIVE:
                    redrive = capped.others
                    jitter = self._rng.uniform(0, HOLD_COOLDOWN_MS / 1000.0)
                    newly_held = capped.newly_held
                    if capped.newly_held:
                        await self._note_hold(breaker, job, result, capped.episode, label, len(redrive) + 1, now)
        await self._store.put(job.state_key, decision.state)
        return Judgement(decision, redrive, jitter, newly_held)

    async def _note_hold(
        self, breaker: FleetBreaker, job: StageJob, result: StageResult, episode: str, label: str, redriven: int, now: int
    ) -> None:
        if result.provider_pk is None or result.size_class is None:
            return
        try:
            await HoldKeeper(breaker.fleet, breaker).note_hold(
                HoldEntry(result.provider_pk, result.size_class, episode, label, now, job.org_id, redriven)
            )
        except Exception:
            self._logger.warning("could not index the provider hold %s; the next tick will not see it", episode, exc_info=True)

    async def release_charged(self, state_key: str) -> None:
        try:
            await self._store.release_charged(state_key)
        except PauseStoreUnavailable:
            self._logger.warning("could not reset the charged count of %s", state_key)

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
