"""The fleet breaker scripts on a real Lua engine (fakeredis, or Redis with ``SPEND_IT_REDIS_URL``)."""

import asyncio

import pytest

from app.services.llm_gateway.fleet_breaker import (
    ALLOW,
    BASE_COOLDOWN_MS,
    REFUSE,
    TRIAL,
    TRIAL_LEASE_MS,
    TRIAL_MAX_MS,
    FleetBreaker,
)
from app.services.llm_gateway.fleet_redis import FleetRedis
from tests.support.spend_world import SimClock

PK, S, L = "pk0", "s", "l"


def _breaker(fleet: FleetRedis) -> FleetBreaker:
    return FleetBreaker(fleet)  # the fleet carries the simulated clock


async def _open(br: FleetBreaker, cls: str = S) -> None:
    for _ in range(5):
        await br.admit(PK, cls, "opener")
        await br.settle(PK, cls, "fail")


async def test_the_breaker_opens_after_five_consecutive_failures(fleet: FleetRedis) -> None:
    br = _breaker(fleet)
    for _ in range(4):
        assert (await br.admit(PK, S, "j")).code == ALLOW
        assert (await br.settle(PK, S, "fail")).state == "closed"
    assert (await br.settle(PK, S, "fail")).state == "open"
    refused = await br.admit(PK, S, "j")
    assert refused.code == REFUSE
    assert 0 < refused.retry_after_s <= BASE_COOLDOWN_MS / 1000


async def test_half_the_last_twenty_open_it_when_ten_were_seen(fleet: FleetRedis) -> None:
    br = _breaker(fleet)
    states = []
    for i in range(12):
        await br.admit(PK, S, "j")
        states.append((await br.settle(PK, S, "fail" if i % 2 == 0 else "ok")).state)
    # fail, ok, fail, ok ...: never five in a row, but half of ten seen
    assert states[:9] == ["closed"] * 9
    assert "open" in states


async def test_a_success_resets_the_consecutive_count(fleet: FleetRedis) -> None:
    br = _breaker(fleet)
    for _ in range(4):
        await br.settle(PK, S, "fail")
    await br.settle(PK, S, "ok")
    for _ in range(4):
        assert (await br.settle(PK, S, "fail")).state == "closed"


async def test_real_request_trials_and_carrier_fairness(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """Open: no call goes out. Due: the trial goes only to a waiting job that has carried no more trials than any
    other waiting job; it is the job's real request, never charged, and a lapsed holder's result is dropped."""
    br = _breaker(fleet)
    await _open(br)
    assert (await br.admit(PK, S, "a")).code == REFUSE
    assert (await br.admit(PK, S, "b")).code == REFUSE
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)

    first = await br.admit(PK, S, "a")
    assert first.code == TRIAL and first.token
    # one trial in flight at a time
    assert (await br.admit(PK, S, "b")).code == REFUSE
    failed = await br.settle(PK, S, "fail", token=first.token, was_closed=False)
    assert failed.trial_result == "reopened" and failed.chargeable is False
    assert failed.state == "open"

    # the cooldown doubled; "a" has carried one trial and "b" none, so "a" is refused and "b" takes the next
    sim_clock.advance(2 * BASE_COOLDOWN_MS + 1000)
    assert (await br.admit(PK, S, "a")).code == REFUSE
    second = await br.admit(PK, S, "b")
    assert second.code == TRIAL
    # the holder's lease lapses (a dead pod): its late result is dropped and the next waiting job takes over
    sim_clock.advance(TRIAL_LEASE_MS + 1000)
    third = await br.admit(PK, S, "a")
    assert third.code == TRIAL
    late = await br.settle(PK, S, "ok", token=second.token, was_closed=False)
    assert late.trial_result == "dropped"
    assert (await br.admit(PK, S, "c")).code == REFUSE  # still open: the dropped result closed nothing
    closed = await br.settle(PK, S, "ok", token=third.token, was_closed=False)
    assert closed.trial_result == "closed" and closed.state == "closed"
    assert (await br.admit(PK, S, "c")).code == ALLOW


async def test_a_lone_job_carries_every_trial(fleet: FleetRedis, sim_clock: SimClock) -> None:
    br = _breaker(fleet)
    await _open(br)
    for _ in range(3):
        sim_clock.advance(TRIAL_MAX_MS + 1000)
        grant = await br.admit(PK, S, "only")
        assert grant.code == TRIAL
        await br.settle(PK, S, "fail", token=grant.token, was_closed=False)


async def test_a_waiting_entry_that_never_wakes_does_not_strand_the_trial(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A deleted record's waiting entry (never seen again) ages out of the fairness minimum, and a due trial
    that goes unclaimed for 300 s is granted to any waiting job."""
    br = _breaker(fleet)
    await _open(br)
    await br.admit(PK, S, "ghost")  # waits once, never wakes again
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    # "live" has carried 0 trials and so has "ghost": equal minimum, so "live" may take it. Make ghost cheaper:
    first = await br.admit(PK, S, "live")
    assert first.code == TRIAL
    await br.settle(PK, S, "fail", token=first.token, was_closed=False)
    sim_clock.advance(2 * BASE_COOLDOWN_MS + 1000)
    # ghost carries 0, live carries 1: live is refused by fairness until the trial has been due for 300 s
    assert (await br.admit(PK, S, "live")).code == REFUSE
    sim_clock.advance(301_000)
    assert (await br.admit(PK, S, "live")).code == TRIAL


async def test_a_recovery_ends_the_trial_fairness_of_the_outage(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A job that waited out one outage and then got through after the breaker closed must not hold a later outage's
    fairness minimum down: the job waiting now would be refused for 300 s although it is the only one waiting."""
    br = _breaker(fleet)
    await _open(br)
    await br.admit(PK, S, "done-later")  # waits through the outage, then sends once the breaker is closed
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    first = await br.admit(PK, S, "carrier")
    assert first.code == TRIAL
    await br.settle(PK, S, "fail", token=first.token, was_closed=False)
    sim_clock.advance(2 * BASE_COOLDOWN_MS + 1000)
    assert (await br.admit(PK, S, "carrier")).code == REFUSE  # fairness: "done-later" has carried none
    sim_clock.advance(301_000)
    second = await br.admit(PK, S, "carrier")
    assert second.code == TRIAL
    assert (await br.settle(PK, S, "ok", token=second.token, was_closed=False)).state == "closed"
    assert (await br.admit(PK, S, "done-later")).code == ALLOW

    await _open(br)  # a later outage
    assert (await br.admit(PK, S, "carrier")).code == REFUSE
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    assert (await br.admit(PK, S, "carrier")).code == TRIAL, "the only job waiting is granted the first trial due"


async def test_the_cooldown_doubles_to_sixty_minutes_and_no_further(fleet: FleetRedis, sim_clock: SimClock) -> None:
    br = _breaker(fleet)
    await _open(br)
    cooldowns = []
    for _ in range(9):
        sim_clock.advance(TRIAL_MAX_MS + 1000)
        grant = await br.admit(PK, S, "j")
        assert grant.code == TRIAL
        await br.settle(PK, S, "fail", token=grant.token, was_closed=False)
        refused = await br.admit(PK, S, "j")
        cooldowns.append(round(refused.retry_after_s))
    assert max(cooldowns) == TRIAL_MAX_MS // 1000
    assert cooldowns[-1] == cooldowns[-2] == TRIAL_MAX_MS // 1000


async def test_the_size_classes_have_their_own_breakers(fleet: FleetRedis) -> None:
    br = _breaker(fleet)
    await _open(br, L)
    assert (await br.admit(PK, L, "j")).code == REFUSE
    for _ in range(10):
        assert (await br.admit(PK, S, "j")).code == ALLOW
        await br.settle(PK, S, "ok")
    assert (await br.admit(PK, S, "j")).code == ALLOW


async def test_the_state_is_shared_by_every_process(fleet: FleetRedis) -> None:
    """One Redis, several breakers (the processes of a pod, other pods): an outage seen by one is seen by all."""
    first, second = _breaker(fleet), _breaker(fleet)
    await _open(first)
    assert (await second.admit(PK, S, "elsewhere")).code == REFUSE


async def test_a_long_trial_keeps_its_lease(fleet: FleetRedis, sim_clock: SimClock) -> None:
    """A trial that hangs 300 s renews its lease, so no second trial starts; a lease nobody renews lapses."""
    br = _breaker(fleet)
    await _open(br)
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    grant = await br.admit(PK, S, "a")
    assert grant.code == TRIAL
    for _ in range(15):  # 15 x 20 s = 300 s of hanging, renewed every 20 s
        sim_clock.advance(20_000)
        assert await br.renew(PK, S, grant.token)
        assert (await br.admit(PK, S, "b")).code == REFUSE
    # the result still counts after 300 s
    assert (await br.settle(PK, S, "ok", token=grant.token, was_closed=False)).trial_result == "closed"

    await _open(br)
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    held = await br.admit(PK, S, "a")
    sim_clock.advance(TRIAL_LEASE_MS + 1000)  # nobody renewed it
    assert (await br.admit(PK, S, "b")).code == TRIAL
    assert not await br.renew(PK, S, held.token)


@pytest.mark.parametrize(
    ("ok", "fail", "charged"),
    [(94, 6, False), (95, 5, True), (96, 4, True), (19, 0, False), (10, 0, False), (80, 20, False)],
    ids=["0.94", "0.95", "0.96", "19-outcomes", "10-outcomes", "0.80"],
)
async def test_charging_needs_ninety_five_percent(fleet: FleetRedis, sim_clock: SimClock, ok: int, fail: int, charged: bool) -> None:
    """A failed send made with the breaker closed is charged only if the key's 5-minute success rate (all real traffic
    on the key, other call sites included) is at least 95 % over at least 20 outcomes."""
    br = _breaker(fleet)
    # spread the outcomes so no five failures are in a row (that would open the breaker, which is not under test)
    pattern = ["ok"] * ok
    step = max(1, len(pattern) // max(fail, 1)) if fail else 1
    for i in range(fail):
        pattern.insert(min(len(pattern), i * (step + 1)), "fail")
    for outcome in pattern:
        await br.admit(PK, S, "bulk")
        await br.settle(PK, S, outcome)
        sim_clock.advance(100)
    await br.admit(PK, S, "j")
    assert (await br.settle(PK, S, "fail")).chargeable is charged


async def test_a_trial_and_a_send_made_while_open_are_never_charged(fleet: FleetRedis, sim_clock: SimClock) -> None:
    br = _breaker(fleet)
    for _ in range(40):
        await br.settle(PK, S, "ok")
    assert (await br.settle(PK, S, "fail")).chargeable is True
    # a failure of a call admitted while the breaker was not closed (a trial) is never charged
    assert (await br.settle(PK, S, "fail", was_closed=False)).chargeable is False


async def test_the_rate_window_is_five_minutes(fleet: FleetRedis, sim_clock: SimClock) -> None:
    br = _breaker(fleet)
    for _ in range(60):
        await br.settle(PK, S, "ok")
    sim_clock.advance(301_000)
    # the 60 successes are older than 5 minutes: fewer than 20 outcomes, so nothing is charged
    assert (await br.settle(PK, S, "fail")).chargeable is False


async def test_four_xx_answers_close_the_failure_streak_but_are_not_rated(fleet: FleetRedis) -> None:
    br = _breaker(fleet)
    for _ in range(4):
        await br.settle(PK, S, "fail")
    await br.settle(PK, S, "ok_unrated")
    assert (await br.settle(PK, S, "fail")).state == "closed"


async def test_concurrent_admissions_grant_one_trial(fleet: FleetRedis, sim_clock: SimClock) -> None:
    br = _breaker(fleet)
    await _open(br)
    sim_clock.advance(BASE_COOLDOWN_MS + 1000)
    grants = await asyncio.gather(*(br.admit(PK, S, f"j{i}") for i in range(20)))
    assert sum(1 for g in grants if g.code == TRIAL) == 1


# ---- the key digest -----------------------------------------------------------------------------------------


def test_the_key_digest_is_an_hmac_that_mixes_in_the_credential() -> None:
    """Two credentials on the same model and endpoint have different keys; the digest changes with the install secret, so
    it cannot be brute-forced from Redis; the credential appears nowhere in it."""
    from app.services.llm_gateway import fleet_breaker as fb

    fb.bind_install_secret("a" * 64)
    one = fb.provider_pk("ChatOpenAI:m@https://h", "api_key=sk-org-one")
    two = fb.provider_pk("ChatOpenAI:m@https://h", "api_key=sk-org-two")
    assert one != two and len(one) == 24 and "sk-org" not in one
    assert one == fb.provider_pk("ChatOpenAI:m@https://h", "api_key=sk-org-one")
    fb.bind_install_secret("b" * 64)
    assert fb.provider_pk("ChatOpenAI:m@https://h", "api_key=sk-org-one") != one
    # a plain hash of the same text must not be what is stored
    import hashlib

    assert one != hashlib.sha256(b"ChatOpenAI:m@https://h|api_key=sk-org-one").hexdigest()[:24]
    fb.bind_install_secret(b"unbound-install-secret")


def test_the_credential_of_a_real_model_is_found_and_never_in_the_provider_key() -> None:
    from langchain_openai import ChatOpenAI

    from app.services.llm_gateway.fleet_breaker import credential_of
    from app.services.llm_gateway.gateway import provider_key

    a = ChatOpenAI(model="m", api_key="sk-aaa", base_url="http://h/v1")
    b = ChatOpenAI(model="m", api_key="sk-bbb", base_url="http://h/v1")
    assert "sk-aaa" in credential_of(a) and credential_of(a) != credential_of(b)
    assert "sk-" not in provider_key(a)


async def test_two_credentials_on_one_endpoint_do_not_share_an_outage(fleet) -> None:
    """Org A's key is down (quota), org B's, on the same model and endpoint, keeps serving."""
    from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError

    class Down:
        def __init__(self) -> None:
            self.api_key = "key-a"

        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            raise ConnectionError("down")

    class Up:
        def __init__(self) -> None:
            self.api_key = "key-b"

        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            return "ok"

    gateway = LLMGateway(4)
    for _ in range(5):
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(Down(), "hi", provider="Chat:m@h")
    with pytest.raises(ProviderUnavailableError) as refused:
        await gateway.invoke(Down(), "hi", provider="Chat:m@h")
    assert refused.value.sent is False
    assert await gateway.invoke(Up(), "hi", provider="Chat:m@h") == "ok"


# ---- the trial lease renewer --------------------------------------------------------------------------------


async def test_the_process_renewer_keeps_a_trial_lease_alive_without_the_calls_loop(fleet, sim_clock) -> None:
    """With the renewer running (on the main loop), a gateway call registers its trial with it instead of renewing from its own
    loop; the lease survives a stall of the calling loop and is dropped when the call ends."""
    from app.services.llm_gateway.gateway import LLMGateway
    from app.services.llm_gateway.trial_lease import get_trial_renewer

    br = _breaker(fleet)
    pk = provider_pk_for("Chat:m@h")
    renewer = get_trial_renewer()
    renewer.start()
    try:
        assert renewer.running
        for _ in range(5):
            await br.admit(pk, S, "x")
            await br.settle(pk, S, "fail")
        sim_clock.advance(BASE_COOLDOWN_MS + 1000)
        release = asyncio.Event()
        seen: dict[str, int] = {}

        class Slow:
            async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
                seen["held"] = renewer.held  # while the trial call is in flight
                await release.wait()
                return "ok"

        task = asyncio.create_task(LLMGateway(2).invoke(Slow(), "hi", provider="Chat:m@h"))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if "held" in seen:
                break
        assert seen.get("held") == 1, "the trial registered with the process renewer"
        sim_clock.advance(TRIAL_LEASE_MS - 5_000)
        assert await renewer.renew_once() == 1  # the main loop's round renews it
        sim_clock.advance(TRIAL_LEASE_MS - 5_000)
        assert (await br.admit(pk, S, "other")).code == REFUSE, "the lease is alive although the call's loop never renewed it"
        release.set()
        assert await task == "ok"
        assert renewer.held == 0
        assert await renewer.renew_once() == 0
    finally:
        await renewer.stop()


class _SlowRenewals:
    """A breaker whose renewals take ``delay_s`` each (a slow Redis); ``hang`` never answers."""

    def __init__(self, delay_s: float, *, hang: frozenset[str] = frozenset()) -> None:
        self.delay_s = delay_s
        self.hang = hang
        self.renewed: list[str] = []

    async def renew(self, pk: str, cls: str, token: str) -> bool:
        if token in self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay_s)
        self.renewed.append(token)
        return True


async def test_a_slow_redis_does_not_serialise_a_renewal_round() -> None:
    """40 leases at 0.1 s a renewal take 4 s one after another; side by side the round fits well inside the lease."""
    from app.services.llm_gateway.trial_lease import TrialLeaseRenewer

    renewer = TrialLeaseRenewer(concurrency=16)
    for i in range(40):
        renewer.register("pk", S, f"t{i}")
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await renewer.renew_once(_SlowRenewals(0.1)) == 40  # type: ignore[arg-type]
    # wall-clock-ok: 40 renewals of 0.1 s at concurrency 16 take about 0.3 s; one after another they take 4 s
    assert loop.time() - started < 1.0


async def test_a_round_ends_at_its_deadline_and_keeps_the_leases_it_did_not_reach() -> None:
    from app.services.llm_gateway.trial_lease import TrialLeaseRenewer

    renewer = TrialLeaseRenewer(round_deadline_s=0.2)
    renewer.register("pk", S, "fast")
    renewer.register("pk", S, "stuck")
    slow = _SlowRenewals(0.0, hang=frozenset({"stuck"}))
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await renewer.renew_once(slow) == 1  # type: ignore[arg-type]
    # wall-clock-ok: a hang guard: the stuck renewal never answers, the renewer must not wait for it
    assert loop.time() - started < 1.0
    assert slow.renewed == ["fast"]
    assert renewer.held == 2, "a lease the round did not reach is renewed next round, not dropped"


def provider_pk_for(provider: str) -> str:
    from app.services.llm_gateway.fleet_breaker import provider_pk

    return provider_pk(provider, "")
