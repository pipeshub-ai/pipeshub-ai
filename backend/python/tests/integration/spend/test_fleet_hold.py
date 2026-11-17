"""Fleet hold: when many jobs on one key hit their charged cap, the key is the problem: the key is held and the jobs are redriven."""

import logging

import pytest

from app.modules.pipeline.models import StageOutcome, StageResult
from app.modules.pipeline.pause_budget import Verdict
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.services.llm_gateway.fleet_breaker import (
    HOLD_COOLDOWN_MS,
    FleetBreaker,
    provider_pk,
)
from app.services.llm_gateway.fleet_hold import HoldKeeper
from tests.support.gateway_world import GatewayWorld, patch_gateway_ceiling

pytestmark = pytest.mark.timeout(600)
HOUR = 3600.0
LABEL = "FakeChat:fake-model@host"


@pytest.fixture(autouse=True)
def _ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gateway_ceiling(monkeypatch)


async def test_many_capped_jobs_put_the_key_on_hold_and_are_redriven(fleet, sim_clock) -> None:
    """Heavy other traffic succeeds on the key while every job request fails, with 20 jobs: the jobs are charged. At 3 jobs
    capped within the window the key goes on hold, the jobs are redriven (none FAILED), one hold notice is claimed (its
    payload carries no credential); after recovery one trial succeeds, the hold is released, one release notice goes out
    and every job completes."""
    world = GatewayWorld(fleet, sim_clock, workers=2)
    world.provider.kind = "503"
    world.provider.outage_until_s = 1.0 * HOUR
    for i in range(20):
        world.add_job(f"doc-{i}", arrive_s=300 + i * 30.0)
    # the other traffic must outnumber the jobs' failures 20 to 1 for the key's success rate to stay at 95 %
    world.add_background(every_s=0.4, until_s=1.0 * HOUR + 600)
    sent: list[dict] = []

    async def send(doc: dict) -> bool:
        sent.append(doc)
        return True

    breaker = FleetBreaker(fleet)
    keeper = HoldKeeper(fleet, breaker, sender=send, clock_ms=sim_clock, logger=logging.getLogger("t"))
    held_seen = {"hold": None}
    original_deliver = world.deliver

    async def deliver_and_tick(doc: str, worker: int) -> None:
        await original_deliver(doc, worker)
        await keeper.tick()
        if held_seen["hold"] is None:
            entries = await keeper.entries()
            if entries:
                held_seen["hold"] = entries[0]

    world.deliver = deliver_and_tick  # type: ignore[method-assign]
    outcomes = await world.run()
    failed = {d: o.reason for d, o in outcomes.items() if o.status == "FAILED" and not d.startswith("bg-")}
    assert not failed, f"redriven, not failed: {failed}"
    assert all(outcomes[f"doc-{i}"].status == "OK" for i in range(20))
    assert held_seen["hold"] is not None, "the key must have gone on hold"
    await keeper.tick()
    kinds = [d["type"] for d in sent]
    assert kinds == ["INDEXING_PROVIDER_ON_HOLD", "INDEXING_PROVIDER_HOLD_RELEASED"], kinds
    notice = sent[0]
    assert notice["payload"]["providerLabel"] == LABEL and notice["payload"]["redrivenJobs"] >= 3
    assert "apiKey" not in str(notice) and provider_pk(LABEL) not in str(notice)
    assert await breaker.hold(provider_pk(LABEL), "s") is None, "the hold ends with the successful trial"


async def test_redrives_are_spread_over_the_release_cooldown(fleet, sim_clock) -> None:
    """Jobs a hold redrives wake at uniform(0, cooldown) on top of their backoff, so released poison documents do not arrive in step."""
    breaker = FleetBreaker(fleet)
    judge = PauseJudge(PauseStore(), clock_ms=sim_clock, breaker=breaker)
    pk = provider_pk(LABEL)
    jitters = []
    from tests.support.stage_worker_harness import make_job

    for i in range(40):
        job = make_job().model_copy(update={"virtual_record_id": f"vr-{i}"})
        # each is at 5 charged sends already; the sixth reaches the cap
        from app.modules.pipeline.pause_budget import PauseState

        await judge._store.put(job.state_key, PauseState(since_ms=sim_clock(), charged=5, sends=5))
        result = StageResult(
            outcome=StageOutcome.PAUSED, reason="x", sent=True, chargeable=True, provider_pk=pk, size_class="s", provider_label=LABEL
        )
        judgement = await judge.judge(job, result)
        assert judgement is not None
        if judgement.decision.verdict is Verdict.REDRIVE:
            jitters.append(judgement.jitter_s)
    assert len(jitters) >= 30
    assert max(jitters) - min(jitters) >= 0.5 * HOLD_COOLDOWN_MS / 1000.0, "the redrives must spread over at least half the cooldown"
    assert all(0 <= j <= HOLD_COOLDOWN_MS / 1000.0 for j in jitters)


async def test_the_same_set_cannot_trigger_a_second_hold(fleet, sim_clock) -> None:
    """Each job counts toward a hold once per revision: after the hold is released, the same three reaching their cap again are
    failed as the documents' own problem, and no second hold (episode) starts."""
    breaker = FleetBreaker(fleet)
    pk = provider_pk(LABEL)
    first = [await breaker.register_capped(pk, "s", f"j{i}", label=LABEL, reason="r") for i in range(3)]
    assert [r.newly_held for r in first] == [False, False, True]
    await fleet.client().delete(breaker.hold_key(pk, "s"))  # released by a successful trial
    again = [await breaker.register_capped(pk, "s", f"j{i}", label=LABEL, reason="r") for i in range(3)]
    assert all(r.already for r in again) and not any(r.newly_held for r in again)
    assert await breaker.hold(pk, "s") is None
    # a job that was in flight when a hold began and settles while it lasts is the key's, not the document's
    for i in range(3):
        await breaker.register_capped(pk, "s", f"k{i}", label=LABEL, reason="r")
    late = await breaker.register_capped(pk, "s", "in-flight", label=LABEL, reason="r")
    assert not late.already and not late.newly_held and late.distinct >= 3


async def test_a_poison_document_alone_in_its_class_ends_at_the_absolute_cap(fleet, sim_clock) -> None:
    """A single poison document with no other traffic in its size class makes at most 20 sends and ends FAILED."""
    world = GatewayWorld(fleet, sim_clock)
    world.provider.poison = {"doc-poison"}
    world.add_job("doc-poison")
    outcomes = await world.run()
    assert outcomes["doc-poison"].status == "FAILED"
    assert world.provider.requests["doc-poison"] == 20, "it ends at the absolute cap, not before"
    assert "20 times" in (outcomes["doc-poison"].reason or "")
