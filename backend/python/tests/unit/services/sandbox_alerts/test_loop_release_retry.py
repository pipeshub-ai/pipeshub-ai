"""A claim whose release failed after an unsent alert is released on a later
evaluation, so the alert is not held back until the claim expires."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.unit.services.sandbox_alerts.test_loop import WARN_KEY, Env

if TYPE_CHECKING:
    from pathlib import Path


async def test_a_release_that_did_not_delete_is_retried_and_the_alert_is_sent(tmp_path: Path) -> None:
    env = Env(tmp_path)
    real_delete = env.store.delete_config
    failing = {"left": 1}

    async def delete_config(key: str) -> bool:
        if failing["left"] > 0:
            failing["left"] -= 1
            return False  # a store error reported without raising; the key stays
        return await real_delete(key)

    env.store.delete_config = delete_config  # type: ignore[method-assign]
    runner = env.runner()
    env.producer.result = False  # the alert is not sent
    env.warn()
    await runner.run_once()
    assert WARN_KEY in env.store.values  # the release failed: the claim is still there

    env.producer.result = True
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    sent = [e["type"] for _, e, _ in env.producer.sent]
    assert sent[-1] == "SANDBOX_CAPACITY_WARNING"
    assert env.producer.sent[-1][1]["payload"]["level"] == "warning"
    assert len([t for t in sent if t == "SANDBOX_CAPACITY_WARNING"]) == 2  # one refused, one accepted
