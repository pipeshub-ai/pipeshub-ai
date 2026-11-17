from __future__ import annotations

import pytest

from app.services.sandbox_alerts.cooldown import STATE_RESOLVED, AlertCooldown
from tests.unit.services.sandbox_alerts.fakes import FakeConfig


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> FakeConfig:
    return FakeConfig(clock)


def cooldown(store: FakeConfig, clock: Clock, scope: str = "install") -> AlertCooldown:
    return AlertCooldown(store, scope, 86400, clock=clock, host="h")  # type: ignore[arg-type]


async def test_the_first_claim_wins_and_the_second_is_refused(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    assert await c.claim("warning", "a1") is True
    assert await c.claim("warning", "a2") is False
    assert store.values["/services/sandbox/alerts/install/warning"]["alertId"] == "a1"
    assert store.ttls["/services/sandbox/alerts/install/warning"] == 86400


async def test_scopes_have_their_own_keys(store: FakeConfig, clock: Clock) -> None:
    assert await cooldown(store, clock, "pod-a").claim("warning", "a") is True
    assert await cooldown(store, clock, "pod-b").claim("warning", "b") is True


async def test_a_store_failure_raises(store: FakeConfig, clock: Clock) -> None:
    store.fail = True
    with pytest.raises(ConnectionError):
        await cooldown(store, clock).claim("critical", "a1")


async def test_release_only_drops_our_own_claim(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    await c.claim("warning", "theirs")
    assert await c.release_if_ours("warning", "ours") is False
    assert await c.read("warning") is not None
    assert await c.release_if_ours("warning", "theirs") is True
    assert await c.read("warning") is None


async def test_an_expired_claim_can_be_claimed_again(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    await c.claim("warning", "a1")
    clock.now += 86400
    assert await c.claim("warning", "a2") is True


async def test_mark_resolved_keeps_the_original_expiry(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    await c.claim("critical", "a1")
    clock.now += 3600
    current = await c.read("critical")
    assert current is not None
    assert await c.mark_resolved("critical", current) is True
    key = "/services/sandbox/alerts/install/critical"
    assert store.values[key]["state"] == STATE_RESOLVED
    assert store.ttls[key] == 86400 - 3600


async def test_a_resolved_claim_is_taken_over_by_a_new_alert(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    await c.claim("warning", "a1")
    clock.now += 3600
    current = await c.read("warning")
    assert current is not None and await c.mark_resolved("warning", current)
    clock.now += 600
    assert await c.claim("warning", "a2") is True
    key = "/services/sandbox/alerts/install/warning"
    assert store.values[key]["alertId"] == "a2"
    assert store.values[key]["state"] == "claimed"
    assert store.ttls[key] == 86400
    assert await c.claim("warning", "a3") is False


async def test_an_expired_claim_whose_delete_fails_quietly_is_not_marked_done(
    store: FakeConfig, clock: Clock,
) -> None:
    c = cooldown(store, clock)
    await c.claim("warning", "a1")
    current = await c.read("warning")
    assert current is not None
    clock.now += 86400

    async def failed_delete(key: str) -> bool:
        return False  # the store reports failure without raising; the key stays

    store.delete_config = failed_delete  # type: ignore[method-assign]
    store.expires.pop("/services/sandbox/alerts/install/warning", None)  # the store still holds it
    assert await c.mark_resolved("warning", current) is False


async def test_an_expired_claim_that_is_already_gone_counts_as_resolved(store: FakeConfig, clock: Clock) -> None:
    c = cooldown(store, clock)
    await c.claim("warning", "a1")
    current = await c.read("warning")
    assert current is not None
    clock.now += 86400
    store.values.clear()
    assert await c.mark_resolved("warning", current) is True
