"""Safety state does not expire while it is in force. Redis TTLs are real time, so these assert the TTLs."""

import logging

from app.services.llm_gateway.fleet_breaker import (
    ALLOW,
    REFUSE,
    TRIAL_MAX_MS,
    FleetBreaker,
)
from app.services.llm_gateway.fleet_hold import HoldEntry, HoldKeeper
from tests.support.spend_world import SimClock

PK, CLS = "pkttl", "l"


async def _to_max_cooldown(b: FleetBreaker, clock: SimClock) -> None:
    for _ in range(5):
        await b.settle(PK, CLS, "fail")
    while True:
        br = await b.fleet.client().hgetall(b._key(PK, CLS, "br"))
        if int(br.get("cooldown_ms", 30000)) >= TRIAL_MAX_MS:
            return
        clock.advance(int(br.get("cooldown_ms", 30000)) + 1)
        a = await b.admit(PK, CLS, "job-0")
        await b.settle(PK, CLS, "fail", token=a.token, was_closed=False)


async def test_an_open_breaker_outlives_its_longest_cooldown(fleet, sim_clock) -> None:
    """At the 60-minute cooldown the breaker's TTL is far longer than the cooldown (it was exactly the cooldown: the
    state vanished when the trial was due and every waiting job sent)."""
    b = FleetBreaker(fleet)
    await _to_max_cooldown(b, sim_clock)
    pttl = await fleet.client().pttl(b._key(PK, CLS, "br"))
    assert pttl > 2 * TRIAL_MAX_MS, f"breaker PTTL {pttl} ms is not longer than two cooldowns"
    # waiting jobs see one trial, not a herd
    for i in range(30):
        assert (await b.admit(PK, CLS, f"w{i}")).code == REFUSE
    sim_clock.advance(TRIAL_MAX_MS + 1)
    codes = [(await b.admit(PK, CLS, f"w{i}")).code for i in range(30)]
    assert codes.count(ALLOW) == 0


async def test_a_closed_breaker_still_expires(fleet, sim_clock) -> None:
    b = FleetBreaker(fleet)
    await b.settle(PK, CLS, "ok")
    pttl = await fleet.client().pttl(b._key(PK, CLS, "br"))
    assert 0 < pttl <= 3_600_000


async def test_the_leader_keeps_a_hold_alive_and_releases_it_when_its_breaker_is_gone(fleet, sim_clock) -> None:
    """The hold is open-ended: each tick refreshes the breaker. If the breaker is lost (Redis data loss, an eviction) the hold
    is released explicitly: the ledger stops, and HOLD_RELEASED is sent once."""
    b = FleetBreaker(fleet)
    sent: list[dict] = []

    async def send(doc: dict) -> bool:
        sent.append(doc)
        return True

    keeper = HoldKeeper(fleet, b, sender=send, clock_ms=sim_clock, logger=logging.getLogger("t"))
    for i in range(3):
        capped = await b.register_capped(PK, CLS, f"c-{i}", label="ChatX", reason="charged_cap")
    await keeper.note_hold(HoldEntry(PK, CLS, capped.episode, "ChatX", sim_clock(), "org-1", 3))
    client = fleet.client()
    br_key = b._key(PK, CLS, "br")
    await client.pexpire(br_key, 5_000)
    sim_clock.advance(10_000)
    assert await keeper.tick() == 1
    assert await client.pttl(br_key) > 3_600_000, "a tick refreshes the breaker of a held key"

    await client.delete(br_key)  # the state is lost
    sim_clock.advance(10_000)
    assert await keeper.tick() == 0, "a hold whose breaker is gone is released"
    assert await client.exists(b.hold_key(PK, CLS)) == 0
    sim_clock.advance(86_400_000)
    assert await keeper.tick() == 0
    ledger = await b.ledger(f"{PK}:{CLS}")
    assert ledger <= 30_000, f"the ledger kept growing after the release: {ledger} ms"
    assert [d["type"] for d in sent] == ["INDEXING_PROVIDER_ON_HOLD", "INDEXING_PROVIDER_HOLD_RELEASED"]


async def test_the_rate_window_is_swept_when_traffic_has_gaps(fleet, sim_clock) -> None:
    """Buckets older than the 5-minute window do not pile up when traffic is sparse."""
    b = FleetBreaker(fleet)
    for _ in range(200):
        await b.settle(PK, CLS, "ok")
        sim_clock.advance(400_000)  # a gap longer than the window between every outcome
    fields = await fleet.client().hkeys(b._key(PK, CLS, "rate"))
    assert len(fields) <= 66, f"{len(fields)} rate buckets kept"
