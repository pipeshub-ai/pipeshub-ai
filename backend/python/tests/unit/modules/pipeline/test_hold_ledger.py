"""The hold ledger and the hold notices: a key on hold does not use up the wall clock of the jobs waiting for it."""

import asyncio
import contextlib
import logging
from collections.abc import Callable

import pytest

from app.modules.pipeline.models import Priority, StageJob, StageOutcome, StageResult
from app.modules.pipeline.pause_budget import PauseLimits, Verdict
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.services.llm_gateway.fleet_breaker import FleetBreaker
from app.services.llm_gateway.fleet_hold import HoldEntry, HoldKeeper
from app.services.llm_gateway.fleet_redis import FleetRedis
from app.services.resource_governor.models import ParseTier
from tests.support.spend_world import SimClock

HOUR_MS = 3_600_000
PK, CLS = "pkhold", "l"


def _job() -> StageJob:
    return StageJob.model_validate({
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1", "rev": "rev-a",
        "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT, "priority": Priority.BULK,
        "trigger": "embed", "text_digest": "t", "blocks_digest": "b", "text_chars": 10, "has_tables": False,
        "has_images": False,
    })


def _refusal() -> StageResult:
    return StageResult(
        outcome=StageOutcome.PAUSED, reason="held", sent=False, provider_pk=PK, size_class=CLS, provider_label="ChatX:m@host"
    )


async def _hold(breaker: FleetBreaker, keeper: HoldKeeper, clock: SimClock) -> HoldEntry:
    for i in range(3):
        capped = await breaker.register_capped(PK, CLS, f"job-{i}", label="ChatX:m@host", reason="charged_cap")
    assert capped.newly_held
    entry = HoldEntry(PK, CLS, capped.episode, "ChatX:m@host", clock(), "org-1", 3)
    await keeper.note_hold(entry)
    return entry


async def test_unwoken_jobs_get_the_hold_subtracted(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A key on hold for 48 h while a job sleeps: the leader advances the ledger, and on wake the wall is the time
    since it first paused minus the ledger's growth, so the job (50 h old) does not FAIL on wake."""
    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, clock_ms=sim_clock)
    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, limits=PauseLimits())
    job = _job()

    first = await judge.judge(job, _refusal())  # the job's first pause, before the hold
    assert first is not None and first.decision.verdict is Verdict.PAUSE
    sim_clock.advance(HOUR_MS)
    await _hold(breaker, keeper, sim_clock)

    # the recovery leader ticks every 10 s while the key is held: a minute of 10-s ticks, then hourly ticks
    for _ in range(6):
        sim_clock.advance(10_000)
        assert await keeper.tick() == 1
    for _ in range(48):
        sim_clock.advance(HOUR_MS)
        assert await keeper.tick() == 1
    assert await breaker.ledger(f"{PK}:{CLS}") >= 48 * HOUR_MS

    # the hold ends (a trial succeeded); the job wakes 50 h after it first paused
    await fleet.client().delete(breaker.hold_key(PK, CLS))
    sim_clock.advance(1000)
    await keeper.tick()
    woken = await judge.judge(job, _refusal())
    assert woken is not None
    assert woken.decision.verdict is Verdict.PAUSE, "a job that slept through a 48 h hold must not FAIL on wake"
    assert woken.decision.state.since_ms == first.decision.state.since_ms


async def test_without_the_ledger_the_same_job_fails_on_wake(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """The control: the same 50 h with no hold recorded is a 24 h wall breach."""
    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, limits=PauseLimits())
    job = _job()
    assert (await judge.judge(job, _refusal())).decision.verdict is Verdict.PAUSE  # type: ignore[union-attr]
    sim_clock.advance(50 * HOUR_MS)
    woken = await judge.judge(job, _refusal())
    assert woken is not None and woken.decision.verdict is Verdict.FAIL_WALL


async def test_the_ledger_only_grows_while_the_key_is_held(fleet: FleetRedis, sim_clock: SimClock) -> None:
    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, clock_ms=sim_clock)
    entry = await _hold(breaker, keeper, sim_clock)
    sim_clock.advance(100_000)
    await keeper.tick()
    grown = await breaker.ledger(entry.scope)
    assert grown == 100_000
    await fleet.client().delete(breaker.hold_key(PK, CLS))
    await keeper.tick()  # ends the hold; the index entry goes
    sim_clock.advance(HOUR_MS)
    await keeper.tick()
    assert await breaker.ledger(entry.scope) == grown
    assert await keeper.entries() == []
    # a second hold counts from its own start, not from the end of the first
    sim_clock.advance(5 * HOUR_MS)
    for i in range(3):
        await breaker.register_capped(PK, CLS, f"again-{i}", label="x", reason="r")
    await keeper.note_hold(HoldEntry(PK, CLS, "ep2", "x", sim_clock(), "org-1", 3))
    sim_clock.advance(10_000)
    await keeper.tick()
    assert await breaker.ledger(entry.scope) == grown + 10_000


async def test_a_pause_records_the_ledger_when_it_first_paused(fleet: FleetRedis, sim_clock: SimClock) -> None:
    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, clock_ms=sim_clock)
    entry = await _hold(breaker, keeper, sim_clock)
    sim_clock.advance(HOUR_MS)
    await keeper.tick()
    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, limits=PauseLimits())
    decision = await judge.judge(_job(), _refusal())
    assert decision is not None
    assert decision.decision.state.ledger_at_since == await breaker.ledger(entry.scope) == HOUR_MS


@pytest.mark.parametrize("second_replica", [False, True])
async def test_the_hold_and_release_notices_go_out_once_each(fleet: FleetRedis, sim_clock: SimClock, second_replica: bool) -> None:
    breaker = FleetBreaker(fleet)
    sent: list[dict] = []

    async def send(doc: dict) -> bool:
        sent.append(doc)
        return True

    keepers = [HoldKeeper(fleet, breaker, sender=send, clock_ms=sim_clock, logger=logging.getLogger("t"))]
    if second_replica:
        keepers.append(HoldKeeper(fleet, breaker, sender=send, clock_ms=sim_clock))
    entry = await _hold(breaker, keepers[0], sim_clock)
    for keeper in keepers * 2:
        sim_clock.advance(10_000)
        await keeper.tick()
    assert [d["type"] for d in sent] == ["INDEXING_PROVIDER_ON_HOLD"]
    doc = sent[0]
    assert doc["payload"]["providerLabel"] == "ChatX:m@host" and doc["payload"]["sizeClass"] == "large"
    assert PK not in str(doc), "the provider key digest never leaves the service"
    assert doc["recipientRoles"] == ["admin"] and doc["orgId"] == "org-1"

    await fleet.client().delete(breaker.hold_key(PK, CLS))
    for keeper in keepers * 2:
        await keeper.tick()
    assert [d["type"] for d in sent] == ["INDEXING_PROVIDER_ON_HOLD", "INDEXING_PROVIDER_HOLD_RELEASED"]
    assert entry.episode == sent[1]["payload"]["episodeId"]


async def test_ending_a_hold_does_not_unindex_a_newer_episode_of_the_key(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A tick ending episode A awaits the notifier; episode B, noted at the same key meanwhile, stays indexed."""
    breaker = FleetBreaker(fleet)
    noting = HoldKeeper(fleet, breaker, clock_ms=sim_clock)
    newer = HoldEntry(PK, CLS, "ep-b", "ChatX:m@host", sim_clock(), "org-1", 3)

    async def send(doc: dict) -> bool:
        if doc["payload"]["episodeId"] != newer.episode and doc["type"].endswith("RELEASED"):
            await noting.note_hold(newer)
        return True

    keeper = HoldKeeper(fleet, breaker, sender=send, clock_ms=sim_clock)
    await _hold(breaker, keeper, sim_clock)
    await keeper.tick()  # the hold notice of A goes out
    await fleet.client().delete(breaker.hold_key(PK, CLS))
    await keeper.tick()  # A ends; B is noted while its release notice is sent

    assert [entry.episode for entry in await keeper.entries()] == [newer.episode]


async def test_no_release_notice_without_a_hold_notice(fleet: FleetRedis, sim_clock: SimClock) -> None:
    breaker = FleetBreaker(fleet)
    sent: list[dict] = []

    async def refuse(doc: dict) -> bool:
        return False  # the broker does not take it

    async def take(doc: dict) -> bool:
        sent.append(doc)
        return True

    entry = await _hold(breaker, HoldKeeper(fleet, breaker, clock_ms=sim_clock), sim_clock)
    await HoldKeeper(fleet, breaker, sender=refuse, clock_ms=sim_clock).tick()  # the claim is freed
    await fleet.client().delete(breaker.hold_key(PK, CLS))
    await HoldKeeper(fleet, breaker, sender=take, clock_ms=sim_clock).tick()
    assert sent == [], "the hold notice was never sent, so no release notice is"
    assert entry.episode


class _Counted:
    """Counts calls and sets ``reached`` once ``target`` calls have been made, so a test waits for iterations, not time."""

    def __init__(self, target: int) -> None:
        self.calls = 0
        self.target = target
        self.reached = asyncio.Event()

    def hit(self) -> int:
        self.calls += 1
        if self.calls >= self.target:
            self.reached.set()
        return self.calls


async def _run_leader(leader: object, *, until: asyncio.Event, idle_check: Callable[[], None] | None = None) -> None:
    """Run ``leader`` until ``until`` is set (at most 5 s), always cancelling it."""
    task = asyncio.create_task(leader)  # type: ignore[arg-type]
    try:
        if idle_check is not None:
            idle_check()
        await asyncio.wait_for(until.wait(), 5.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_only_the_leader_ticks_and_a_failing_tick_does_not_stop_the_loop(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """The recovery leader ticks every interval; a replica without the lease does not; a tick that raises is logged and retried."""
    from app.services.llm_gateway.fleet_hold import run_hold_leader

    ticks = _Counted(3)
    asked = _Counted(3)

    class Counting(HoldKeeper):
        async def tick(self) -> int:
            if ticks.hit() == 2:
                raise RuntimeError("redis hiccup")
            return 0

    async def is_leader() -> bool:
        return asked.hit() > 3  # three passes without the lease, then it is acquired

    keeper = Counting(fleet, FleetBreaker(fleet), clock_ms=sim_clock)
    await _run_leader(run_hold_leader(keeper, is_leader, logger=logging.getLogger("t"), interval_s=0.001), until=ticks.reached)
    assert asked.calls >= ticks.calls + 3, "the passes without the lease did not tick"
    assert ticks.calls >= 3, "the loop goes on after a tick raised"


async def test_the_leader_retries_the_owed_redrives_after_each_tick(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """The redrives holds still owe are the leader's recovery work on its cadence; a replica without the lease does none."""
    from app.services.llm_gateway.fleet_hold import run_hold_leader

    passes = _Counted(2)
    asked = _Counted(3)

    async def is_leader() -> bool:
        asked.hit()
        return asked.calls > 3

    async def retry_redrives() -> int:
        assert asked.calls > 3, "no redrive pass without the lease"
        passes.hit()
        return 0

    keeper = HoldKeeper(fleet, FleetBreaker(fleet), clock_ms=sim_clock)
    await _run_leader(
        run_hold_leader(keeper, is_leader, logger=logging.getLogger("t"), interval_s=0.001, after_tick=retry_redrives),
        until=passes.reached,
    )
    assert passes.calls >= 2


async def test_only_the_hold_of_the_jobs_own_key_is_subtracted_from_its_wall(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A job's wall excludes the time its own key was on hold, which the recovery leader records. A ledger nothing in
    the service advances (a fleet-wide scope) must not be read: whatever wrote it, the job would get time it never waited for
    a hold."""
    breaker = FleetBreaker(fleet)
    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, limits=PauseLimits())
    job = _job()
    first = await judge.judge(job, _refusal())
    assert first is not None
    await breaker.start_ledger("fleet")
    sim_clock.advance(30 * HOUR_MS)
    await breaker.advance_ledger("fleet", held=True)
    woken = await judge.judge(job, _refusal())
    assert woken is not None and woken.decision.verdict is Verdict.FAIL_WALL


async def test_a_hold_the_index_lost_is_found_again_from_redis(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """The script that opens a hold ran, but indexing it failed after: the leader's first tick finds it by scanning,
    with the org to notify, so the hold is ticked and announced."""
    breaker = FleetBreaker(fleet)
    for i in range(3):
        capped = await breaker.register_capped(PK, CLS, f"job-{i}", label="ChatX:m@host", reason="charged_cap", org="org-9")
    assert capped.newly_held
    sent: list[dict] = []

    async def take(doc: dict) -> bool:
        sent.append(doc)
        return True

    keeper = HoldKeeper(fleet, breaker, sender=take, clock_ms=sim_clock)
    assert await keeper.entries() == []
    assert await keeper.tick() == 1
    [entry] = await keeper.entries()
    assert (entry.pk, entry.cls, entry.episode, entry.org_id) == (PK, CLS, capped.episode, "org-9")
    assert [doc["orgId"] for doc in sent] == ["org-9"], "the hold is announced to its org"


async def test_a_notice_cut_off_mid_send_is_not_taken_for_delivered(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """Shutdown cancels the leader while the hold notice is being sent: the claim is freed, so the next tick sends it,
    and no release notice goes out for a hold notice the broker never took."""
    breaker = FleetBreaker(fleet)
    entry = await _hold(breaker, HoldKeeper(fleet, breaker, clock_ms=sim_clock), sim_clock)

    async def cut_off(doc: dict) -> bool:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await HoldKeeper(fleet, breaker, sender=cut_off, clock_ms=sim_clock).tick()
    sent: list[str] = []

    async def take(doc: dict) -> bool:
        sent.append(doc["type"])
        return True

    await fleet.client().delete(breaker.hold_key(PK, CLS))
    await HoldKeeper(fleet, breaker, sender=take, clock_ms=sim_clock).tick()
    assert sent == [], "the hold notice was never delivered, so its release is not sent"
    assert entry.episode


@pytest.mark.asyncio
async def test_a_scan_that_fails_or_stalls_is_a_store_outage_and_stops_neither_pass(fleet: FleetRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scans that re-index holds and owed redrives raise the store's own error type, bounded in time, and a failing scan
    stops neither the hold tick nor the redrive pass that follows it."""
    from app.modules.pipeline.pause_store import PauseStoreUnavailable
    from app.services.llm_gateway import fleet_hold

    client = fleet.client()
    # Patches are undone inside the test: the fleet fixture's own cleanup scans this same client, and a stalled scan left
    # in place there would wait out the test timeout.
    with monkeypatch.context() as patch:

        def broken_scan(*args, **kwargs):  # noqa: ANN202
            async def gen():  # noqa: ANN202
                raise ConnectionError("node down")
                yield  # pragma: no cover

            return gen()

        patch.setattr(client, "scan_iter", broken_scan)
        with pytest.raises(PauseStoreUnavailable):
            await PauseStore().reconcile_owed()
        keeper = HoldKeeper(fleet, FleetBreaker(fleet))
        assert await keeper.tick() == 0, "the tick ran although its reconcile scan failed"

        async def stalled_scan(*args, **kwargs):  # noqa: ANN202
            await asyncio.sleep(3600)
            yield "never"

        patch.setattr(client, "scan_iter", stalled_scan)
        patch.setattr(fleet_hold, "REDIS_OP_TIMEOUT_S", 0.05)
        with pytest.raises(PauseStoreUnavailable):
            await PauseStore().reconcile_owed()


async def test_a_restored_hold_credits_no_time_from_before_it_began(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A hold's index entry was never written (its ledger start failed) and the reconcile scan restores it later. The ledger's
    last advance was ten hours before the hold began, and nothing advanced it since: the first tick credits the time the key
    was held, not those ten hours, which would otherwise extend every pause budget on the key."""
    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, clock_ms=sim_clock)
    scope = f"{PK}:{CLS}"
    await breaker.advance_ledger(scope, held=False)  # the ledger's last advance, at hour 0
    sim_clock.advance(10 * HOUR_MS)
    for i in range(3):
        capped = await breaker.register_capped(PK, CLS, f"job-{i}", label="ChatX:m@host", reason="charged_cap")
    assert capped.newly_held and await keeper.entries() == [], "the hold exists in Redis only"
    sim_clock.advance(10 * 60_000)
    assert await keeper.tick() == 1  # the reconcile restores the entry, then the pass advances the ledger
    assert await breaker.ledger(scope) == 10 * 60_000, "ten minutes of hold, not ten hours and ten minutes"


async def test_a_job_at_the_charged_cap_waits_while_the_hold_cannot_be_asked(sim_clock: SimClock) -> None:
    """The budget check of a job at its charged cap registers it with the key's hold. When Redis cannot answer, that is the
    store's own outage (the caller defers the job), not an error the delivery is counted and abandoned for."""
    from app.modules.pipeline.pause_budget import PauseState
    from app.modules.pipeline.pause_store import PauseStoreUnavailable
    from app.services.llm_gateway.fleet_breaker import FleetBreakerUnavailable

    class DownBreaker:
        async def now_ms(self) -> int:
            return 0

        async def ledger(self, scope: str) -> int:
            return 0

        async def register_capped(self, *args: object, **kwargs: object) -> None:
            raise FleetBreakerUnavailable("redis down")

    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, limits=PauseLimits(), breaker=DownBreaker())  # type: ignore[arg-type]
    limits = await judge.limits()
    prior = PauseState(since_ms=0, charged=limits.charged_sends, scope=f"{PK}:{CLS}")
    with pytest.raises(PauseStoreUnavailable):
        await judge._ask_hold(_job(), prior, PK, CLS, None, 0, {"now_ms": 0, "ledger_now": 0, "limits": limits})
