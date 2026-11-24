"""Breaker edges.

* a trial holder that dies: its lease lapses at 60 s, another waiting job gets the next trial, the dead holder's
  late settle is dropped and does not move the state;
* an unreadable breaker Redis: the gateway pauses the call (ProviderUnavailableError, sent=False) without calling
  the model, within the 2 s op timeout;
* the charging rate is per size class: 100 small successes do not make a large-class failure chargeable.
"""

import time

import pytest

from app.services.llm_gateway.fleet_breaker import REFUSE, TRIAL, FleetBreaker
from app.services.llm_gateway.fleet_redis import FleetRedis, bind_fleet_redis
from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError

PK = "pkedge"


async def _open(b: FleetBreaker, cls: str = "l") -> None:
    for _ in range(5):
        await b.settle(PK, cls, "fail")


async def test_a_dead_trial_holder_loses_the_permit_and_its_late_result_is_dropped(fleet, sim_clock) -> None:
    b = FleetBreaker(fleet)
    await _open(b)
    assert (await b.admit(PK, "l", "a")).code == REFUSE
    assert (await b.admit(PK, "l", "b")).code == REFUSE
    sim_clock.advance_s(31)
    first = await b.admit(PK, "l", "a")
    assert first.code == TRIAL
    sim_clock.advance_s(30)
    assert (await b.admit(PK, "l", "b")).code == REFUSE, "lease still held"
    sim_clock.advance_s(31)  # 61 s: the lease lapsed, nobody renewed
    second = await b.admit(PK, "l", "b")
    assert second.code == TRIAL and second.token != first.token
    late = await b.settle(PK, "l", "ok", token=first.token, was_closed=False)
    assert late.trial_result == "dropped" and late.state == "open"
    done = await b.settle(PK, "l", "fail", token=second.token, was_closed=False)
    assert done.trial_result == "reopened"


async def test_an_unreadable_breaker_redis_pauses_without_calling() -> None:
    import redis.asyncio as aioredis

    client = aioredis.Redis(host="127.0.0.1", port=18499, socket_connect_timeout=5)
    bind_fleet_redis(FleetRedis(lambda: client, "qa"))
    calls = []

    async def make_call() -> str:
        calls.append(1)
        return "x"

    try:
        started = time.monotonic()
        with pytest.raises(ProviderUnavailableError) as err:
            await LLMGateway(4).call(make_call, provider="ChatX:m@h")
        took = time.monotonic() - started
    finally:
        bind_fleet_redis(None)
        await client.aclose()
    assert calls == [] and err.value.sent is False and took < 3.0, (calls, err.value.sent, took)


async def test_the_charging_rate_is_kept_per_size_class(fleet, sim_clock) -> None:
    b = FleetBreaker(fleet)
    for _ in range(100):
        await b.settle(PK, "s", "ok")
    s = await b.settle(PK, "l", "fail")
    assert s.chargeable is False
    for _ in range(40):
        await b.settle(PK, "l", "ok")
    s = await b.settle(PK, "l", "fail")
    assert s.chargeable is True
