"""A discrete-event world for the pause budget, driving the real code.

The scenarios are outage kinds, backoff, latencies and job families taken from an earlier stand-alone model of the
pause budget. What is simulated is the world: the provider (which request
fails when, how long each takes), the clock, and the arrival of jobs and of other traffic. What is **real** is
everything that decides: ``pause_verdict``, the fleet breaker's Lua scripts on Redis (fakeredis, or a real Redis),
the trial grant and its carrier fairness, the 5-minute charging rate, the hold rule, the hold ledger and the
redrive of the jobs a hold absorbs. Only the clock is the simulation's (``FleetRedis.clock_ms``), which stands in for Redis ``TIME``.

A job wake is one delivery: admit, then either a refusal (an open breaker: no send, the job pauses) or a send
whose outcome arrives ``latency`` later. The outcome is settled on the breaker at that time and judged by the real
``PauseJudge``.
"""

from __future__ import annotations

import heapq
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.modules.pipeline.models import Priority, StageJob, StageOutcome, StageResult
from app.modules.pipeline.pause_budget import PauseLimits, Verdict
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.services.llm_gateway.fleet_breaker import (
    FleetBreaker,
    provider_pk,
)
from app.services.llm_gateway.fleet_hold import HoldKeeper
from app.services.llm_gateway.gateway import TRIAL_LEASE_RENEW_S
from app.services.messaging.config import compute_retry_backoff_seconds
from app.services.resource_governor.models import ParseTier

if TYPE_CHECKING:
    from app.services.llm_gateway.fleet_redis import FleetRedis

CEIL = 600.0
LATENCY = {"fast5xx": 2.0, "hang": CEIL, "reset": 3.0, "blackhole": 10.0}
OK_LATENCY = 30.0
T0_MS = 1_700_000_000_000
PROVIDER = "ChatSim:model@host"
SEND_FAILED = {Verdict.FAIL_CHARGED: "FAILED6", Verdict.FAIL_ABSOLUTE: "FAILED20", Verdict.FAIL_WALL: "FAILEDwall"}


@dataclass
class SimJob:
    index: int
    cls: str
    poison: bool = False
    wakes: int = 0
    done: str | None = None
    at: float | None = None
    start: float = 0.0
    sends: int = 0
    trials: int = 0
    pending: object = None  # the in-flight send


@dataclass
class SimResult:
    jobs: dict[int, SimJob]
    holds: int = 0
    notices: list[dict] = field(default_factory=list)

    def failed(self, select=lambda j: True) -> Counter:
        return Counter(j.done for j in self.jobs.values() if select(j) and j.done and j.done.startswith("FAILED"))

    def total(self, select=lambda j: True) -> int:
        return sum(1 for j in self.jobs.values() if select(j))

    def max_sends(self, select=lambda j: True) -> int:
        return max((j.sends for j in self.jobs.values() if select(j)), default=0)

    def p50_sends(self, select=lambda j: True) -> int:
        values = sorted(j.sends for j in self.jobs.values() if select(j))
        return values[len(values) // 2] if values else 0


class World:
    def __init__(self, fleet: FleetRedis, seed: int, *, kind: str = "hang", outage_s: float = 0.0, p: float = 1.0,
                 fail_classes: tuple[str, ...] = ("s", "l"), background_fails: bool = True) -> None:
        self.fleet = fleet
        self.rnd = random.Random(seed)
        self.kind = kind
        self.outage_s = outage_s
        self.p = p
        self.fail_classes = fail_classes
        self.background_fails = background_fails
        self.now = 0.0
        fleet.clock_ms = lambda: T0_MS + int(self.now * 1000)
        self.breaker = FleetBreaker(fleet)
        self.store = PauseStore(fleet)
        self.judge = PauseJudge(
            self.store, clock_ms=fleet.clock_ms, limits=PauseLimits(), breaker=self.breaker, rng=random.Random(seed + 1)
        )
        self.keeper = HoldKeeper(fleet, self.breaker, clock_ms=fleet.clock_ms)
        self.notices: list[dict] = []
        self.keeper_with_sender = HoldKeeper(fleet, self.breaker, sender=self._send_notice, clock_ms=fleet.clock_ms)
        self.queue: list[tuple[float, int, str, object]] = []
        self.seq = 0
        self.jobs: dict[int, SimJob] = {}
        self.stage_jobs: dict[int, StageJob] = {}
        self.pk = provider_pk(PROVIDER)
        self.holds = 0
        self.tick_scheduled = False

    async def _send_notice(self, doc: dict) -> bool:
        self.notices.append(doc)
        return True

    # ---- scheduling ------------------------------------------------------------------------------------
    def at(self, t: float, kind: str, payload: object = None) -> None:
        self.seq += 1
        heapq.heappush(self.queue, (t, self.seq, kind, payload))

    def add_job(self, index: int, cls: str, *, arrive: float, poison: bool = False) -> None:
        job = SimJob(index, cls, poison=poison, start=arrive)
        self.jobs[index] = job
        self.stage_jobs[index] = StageJob.model_validate({
            "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": f"vr-{index}",
            "rev": "rev-a", "record_ids": (f"rec-{index}",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
            "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
            "text_chars": 10, "has_tables": False, "has_images": False,
        })
        self.at(arrive, "wake", index)

    def add_background(self, cls: str, per_hour: float, until: float) -> None:
        for _ in range(int(per_hour * until / 3600)):
            self.at(self.rnd.uniform(0, until), "bg", cls)

    async def add_waiting_ghost(self, cls: str) -> None:
        """A waiting-set entry that was seen once at time 0 and never wakes again (a record deleted while paused)."""
        client = self.fleet.client()
        await client.zadd(self.fleet.key(f"sg:{{p:{self.pk}:{cls}}}:waiting"), {"ghost": T0_MS})
        await client.hset(self.fleet.key(f"sg:{{p:{self.pk}:{cls}}}:trials"), "ghost", 0)

    # ---- the provider ----------------------------------------------------------------------------------
    def fails(self, job: SimJob | None, cls: str) -> bool:
        if job is not None and job.poison:
            return True
        if self.now >= self.outage_s or cls not in self.fail_classes:
            return False
        if job is None and not self.background_fails:
            return False
        return self.rnd.random() < self.p

    def latency(self, job: SimJob | None, ok: bool) -> float:
        if ok:
            return OK_LATENCY
        return CEIL if (job is not None and job.poison) else LATENCY[self.kind]

    # ---- events ----------------------------------------------------------------------------------------
    async def run(self, horizon: float) -> SimResult:
        while self.queue:
            t, _, kind, payload = heapq.heappop(self.queue)
            if t > horizon:
                break
            self.now = t
            if kind == "wake":
                await self._wake(self.jobs[payload])
            elif kind == "settle":
                await self._settle(*payload)
            elif kind == "renew":
                await self._renew(*payload)
            elif kind == "bg":
                await self._background(payload)
            elif kind == "tick":
                self.tick_scheduled = False
                if await self.keeper_with_sender.tick():
                    self._schedule_tick()
        return SimResult(self.jobs, self.holds, self.notices)

    def _schedule_tick(self) -> None:
        if not self.tick_scheduled:
            self.tick_scheduled = True
            self.at(self.now + 10.0, "tick")

    async def _background(self, cls: str) -> None:
        admission = await self.breaker.admit(self.pk, cls, "")
        if not admission.allowed:
            return
        ok = not self.fails(None, cls)
        await self.breaker.settle(
            self.pk, cls, "ok" if ok else "fail", token=admission.token if admission.trial else "",
            was_closed=not admission.trial,
        )

    async def _wake(self, job: SimJob) -> None:
        if job.done:
            return
        job.wakes += 1
        key = self.stage_jobs[job.index].state_key
        admission = await self.breaker.admit(self.pk, job.cls, key)
        if not admission.allowed:
            await self._paused(job, StageResult(
                outcome=StageOutcome.PAUSED, reason="open", sent=False, provider_pk=self.pk, size_class=job.cls,
                provider_label=PROVIDER, retry_after_s=admission.retry_after_s,
            ))
            return
        job.sends += 1
        ok = not self.fails(job, job.cls)
        settle_at = self.now + self.latency(job, ok)
        self.at(settle_at, "settle", (job.index, ok, admission.trial, admission.token))
        if admission.trial:
            # a trial that hangs keeps its lease: the gateway renews it every TRIAL_LEASE_RENEW_S while the call runs
            self.at(self.now + TRIAL_LEASE_RENEW_S, "renew", (job.cls, admission.token, settle_at))

    async def _renew(self, cls: str, token: str, until: float) -> None:
        if self.now < until:
            await self.breaker.renew(self.pk, cls, token)
            self.at(self.now + TRIAL_LEASE_RENEW_S, "renew", (cls, token, until))

    async def _settle(self, index: int, ok: bool, trial: bool, token: str) -> None:
        job = self.jobs[index]
        settlement = await self.breaker.settle(
            self.pk, job.cls, "ok" if ok else "fail", token=token if trial else "", was_closed=not trial
        )
        if ok:
            job.done, job.at = "OK", self.now
            await self.judge.clear(self.stage_jobs[index].state_key, attempt=job.sends)
            return
        if trial:
            job.trials += 1
        await self._paused(job, StageResult(
            outcome=StageOutcome.PAUSED, reason="failed", sent=True, trial=trial,
            chargeable=settlement.chargeable and not trial, provider_pk=self.pk, size_class=job.cls,
            provider_label=PROVIDER,
        ))

    async def _paused(self, job: SimJob, result: StageResult) -> None:
        stage_job = self.stage_jobs[job.index]
        # Each send is the job's next run, so its send count serves as the stage attempt.
        judgement = await self.judge.judge(stage_job, result, attempt=job.sends)
        assert judgement is not None, "the pause budget must be readable in the simulation"
        verdict = judgement.decision.verdict
        if verdict.is_fail:
            job.done, job.at = SEND_FAILED[verdict], self.now
            return
        delay = max(result.retry_after_s or 0.0, compute_retry_backoff_seconds(job.wakes))
        if verdict is Verdict.REDRIVE:
            self.holds += 1 if judgement.newly_held else 0
            delay += judgement.jitter_s
            for other_key in judgement.redrive:
                other = next((j for i, j in self.jobs.items() if self.stage_jobs[i].state_key == other_key), None)
                if other is not None and other.done == "FAILED6":
                    await self.judge.release_charged(other_key)
                    other.done = None
                    self.at(self.now, "wake", other.index)
            self._schedule_tick()
        else:
            delay += await self.judge.hold_jitter(result)
        self.at(self.now + delay, "wake", job.index)


async def run_world(
    fleet: FleetRedis, seed: int, *, jobs: int, outage_s: float = 0.0, kind: str = "hang", p: float = 1.0,
    small_per_h: float = 0.0, large_per_h: float = 0.0, fail_classes: tuple[str, ...] = ("s", "l"),
    arrivals_over_s: float = 60.0, poison_k: int = 0, poison_cls: str = "l", healthy: int = 0,
    healthy_cls: str = "l", job_cls: str = "l", classes: dict[int, str] | None = None, dead_entry: bool = False,
    horizon: float = 2 * 86400.0, background_fails: bool = True, background_until: float | None = None,
) -> SimResult:
    world = World(fleet, seed, kind=kind, outage_s=outage_s, p=p, fail_classes=fail_classes, background_fails=background_fails)
    if dead_entry:
        await world.add_waiting_ghost(job_cls)
    for j in range(jobs + healthy):
        poisoned = j < poison_k
        cls = poison_cls if poisoned else (classes[j] if classes else (job_cls if j < jobs else healthy_cls))
        arrive = world.rnd.uniform(0, arrivals_over_s) if j < jobs else world.rnd.uniform(0, 60)
        world.add_job(j, cls, arrive=arrive, poison=poisoned)
    # Other traffic runs while the scenario's jobs are alive (the outage and a margin), not for the whole horizon.
    until = background_until if background_until is not None else min(horizon, max(outage_s, arrivals_over_s) + 6 * 3600.0)
    world.add_background("s", small_per_h, until)
    world.add_background("l", large_per_h, until)
    return await world.run(horizon)
