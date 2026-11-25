"""A warning never emails within the cooldown of a critical email, even when
the store fails at the wrong moments.

The critical alert also claims the warning level. When that claim fails, the
warning key stays empty, so the next warning alert relies on reading the
critical claim to learn that an email already went out. If that read failed
too, the warning was claimed as if no email had been sent, and a second
email followed the critical one a minute later. Found by the multi-process
chaos run (``tests/integration/chaos``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.services.sandbox_alerts.test_loop import CRIT_KEY, WARN_KEY, Env

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


class _Faulty:
    """The fake store, failing the calls a test names."""

    def __init__(self, store: object) -> None:
        self.store = store
        self.fail_reads: set[str] = set()
        self.fail_creates: set[str] = set()

    async def create_config_if_absent(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        if key in self.fail_creates:
            self.fail_creates.discard(key)
            raise ConnectionError("store down")
        return await self.store.create_config_if_absent(key, value, ttl_seconds=ttl_seconds)  # type: ignore[attr-defined]

    async def set_config(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        return await self.store.set_config(key, value, ttl_seconds=ttl_seconds)  # type: ignore[attr-defined]

    async def get_config(self, key: str, use_cache: bool = False, raise_on_error: bool = False) -> object:
        if key in self.fail_reads:
            raise ConnectionError("store down")
        return await self.store.get_config(key, use_cache=use_cache, raise_on_error=raise_on_error)  # type: ignore[attr-defined]

    async def delete_config(self, key: str) -> bool:
        return await self.store.delete_config(key)  # type: ignore[attr-defined]


def _sent(env: Env) -> list[tuple[str, bool]]:
    return [(m["payload"]["level"], m["email"]) for _, m, _ in env.producer.sent]


async def test_an_unreadable_critical_claim_holds_the_warning_back(tmp_path: Path) -> None:
    env = Env(tmp_path)
    store = env.store = _Faulty(env.store)  # type: ignore[assignment]
    runner = env.runner()
    store.fail_creates.add(WARN_KEY)
    env.critical()
    await runner.run_once()
    assert _sent(env) == [("critical", True)]

    env.clock.now += 60
    env.warn()
    store.fail_reads.add(CRIT_KEY)
    await runner.run_once()
    assert _sent(env) == [("critical", True)]

    # Once the store answers, the warning goes in-app, without an email.
    store.fail_reads.clear()
    env.clock.now += 60
    env.warn()
    await runner.run_once()
    assert _sent(env) == [("critical", True), ("warning", False)]
