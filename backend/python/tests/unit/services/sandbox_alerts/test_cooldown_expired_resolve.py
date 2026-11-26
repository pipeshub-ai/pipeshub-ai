"""Marking a claim resolved after its cooldown is over deletes it, so it
cannot hold back the next alert while the store still has it."""

from __future__ import annotations

from app.services.sandbox_alerts.cooldown import AlertCooldown
from tests.unit.services.sandbox_alerts.fakes import FakeConfig

T0 = 1_800_000_000.0


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


async def test_an_expired_claim_is_deleted_and_the_next_alert_can_claim() -> None:
    clock = _Clock()
    store = FakeConfig()  # the store's own expiry lags: it never expires here
    cooldown = AlertCooldown(store, "install", 60, clock=clock, host="h")  # type: ignore[arg-type]
    assert await cooldown.claim_alert("warning", "a1") is not None
    clock.now += 61
    current = await cooldown.read("warning")
    assert current is not None

    assert await cooldown.mark_resolved("warning", current) is True
    assert await cooldown.read("warning") is None
    assert await cooldown.claim_alert("warning", "a2") is not None


async def test_a_live_claim_is_still_marked_resolved() -> None:
    clock = _Clock()
    store = FakeConfig(clock)
    cooldown = AlertCooldown(store, "install", 60, clock=clock, host="h")  # type: ignore[arg-type]
    assert await cooldown.claim_alert("warning", "a1") is not None
    current = await cooldown.read("warning")
    assert current is not None
    assert await cooldown.mark_resolved("warning", current) is True
    stored = await cooldown.read("warning")
    assert stored is not None
    assert stored["state"] == "resolved"
