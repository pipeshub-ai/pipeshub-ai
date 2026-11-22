"""The service's scope sweep loop starts nothing unless the scope fields are on, and reads its interval defensively."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from app.modules.indexing import scope_sweep_loop as loop

if TYPE_CHECKING:
    import pytest


def test_the_interval_is_the_default_unless_it_is_a_number_of_at_least_the_minimum() -> None:
    assert loop.sweep_interval({}) == 300.0
    assert loop.sweep_interval({loop.ENV_SWEEP_INTERVAL: "120"}) == 120.0
    for bad in ("soon", "0", "-5", "29.9", "nan"):
        assert loop.sweep_interval({loop.ENV_SWEEP_INTERVAL: bad}) == 300.0, bad


async def test_with_the_scope_fields_off_the_loop_returns_at_once_and_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    container, sleeps = MagicMock(), AsyncMock()
    await loop.run_scope_sweep_loop(container, MagicMock(), AsyncMock(), sleep=sleeps)
    container.logger.assert_not_called()
    sleeps.assert_not_called()


async def test_with_them_on_it_sweeps_every_organisation_each_round_with_a_jittered_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    monkeypatch.setenv(loop.ENV_SWEEP_INTERVAL, "60")
    seen: dict = {}

    async def forever(sweep: object, orgs: object, interval: float, *, log: object, sleep: object, **kw: object) -> bool:
        seen.update(interval=interval, orgs=await orgs(), sleep=sleep)
        await sleep(interval)
        return False

    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(loop, "sweep_forever", forever)
    await loop.run_scope_sweep_loop(MagicMock(), MagicMock(), AsyncMock(return_value=["o1", "o2"]), sleep=sleep)
    assert seen["interval"] == 60.0 and seen["orgs"] == ["o1", "o2"]
    assert 0 <= waits[0] <= loop.STARTUP_DELAY_SECONDS and 60 <= waits[1] <= 72      # a spread start, then the interval plus up to a fifth
