"""SP-1..SP-7: the session pool (P2.8), with a ``ManualClock`` and counted sessions."""

from __future__ import annotations

import asyncio
import itertools
import logging

import pytest

from harness_core.mcp_hub import (
    PoolConfig,
    SessionKey,
    SessionOwnerMismatch,
    SessionPool,
)
from harness_core.testing.clock import ManualClock

ALICE = SessionKey("org-1", "srv-1", "alice")


class Sessions:
    def __init__(self) -> None:
        self.ids = itertools.count(1)
        self.opened: list[int] = []
        self.closed: list[int] = []

    async def open(self) -> int:
        await asyncio.sleep(0)
        session = next(self.ids)
        self.opened.append(session)
        return session

    async def close(self, session: int) -> None:
        self.closed.append(session)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def sessions() -> Sessions:
    return Sessions()


@pytest.fixture
def pool(clock: ManualClock, sessions: Sessions) -> SessionPool[int]:
    return SessionPool(clock=clock, close=sessions.close, config=PoolConfig(idle_ttl_s=60, max_age_s=600))


async def test_sp1_one_session_for_sequential_and_concurrent_leases(pool: SessionPool[int], sessions: Sessions) -> None:
    first = await pool.acquire(ALICE, "v1", sessions.open)
    await first.release()
    second = await pool.acquire(ALICE, "v1", sessions.open)
    concurrent = await asyncio.gather(*(pool.acquire(ALICE, "v1", sessions.open) for _ in range(5)))
    assert {first.session, second.session, *(lease.session for lease in concurrent)} == {1}
    assert sessions.opened == [1]
    (info,) = pool.sessions()
    assert info.leases == 6


@pytest.mark.parametrize(
    "other",
    [SessionKey("org-1", "srv-1", "bob"), SessionKey("org-1", "srv-2", "alice"), SessionKey("org-2", "srv-1", "alice")],
    ids=["owner", "server", "tenant"],
)
async def test_sp2_another_owner_server_or_tenant_gets_its_own_session(
    pool: SessionPool[int], sessions: Sessions, other: SessionKey
) -> None:
    alice = await pool.acquire(ALICE, "v1", sessions.open)
    await alice.release()
    theirs = await pool.acquire(other, "v1", sessions.open)
    assert theirs.session != alice.session
    assert theirs.key == other
    assert sessions.opened == [1, 2]


async def test_sp3_another_variant_opens_anew_and_retires_the_old_once_released(
    pool: SessionPool[int], sessions: Sessions
) -> None:
    old = await pool.acquire(ALICE, "v1", sessions.open)
    new = await pool.acquire(ALICE, "v2", sessions.open)
    assert (old.session, new.session) == (1, 2)
    assert sessions.closed == []
    await old.release()
    assert sessions.closed == [1]
    again = await pool.acquire(ALICE, "v2", sessions.open)
    assert again.session == 2


async def test_sp4_idle_sessions_close_leased_ones_stay_and_age_retires(
    pool: SessionPool[int], sessions: Sessions, clock: ManualClock
) -> None:
    idle = await pool.acquire(ALICE, "v1", sessions.open)
    await idle.release()
    held = await pool.acquire(SessionKey("org-1", "srv-1", "bob"), "v1", sessions.open)
    _ = clock.advance(61)
    assert await pool.sweep() == 1
    assert sessions.closed == [1]
    assert [i.key.owner for i in pool.sessions()] == ["bob"]
    _ = clock.advance(600)
    assert await pool.sweep() == 0, "a leased session is never swept"
    renewed = await pool.acquire(SessionKey("org-1", "srv-1", "bob"), "v1", sessions.open)
    assert renewed.session == 3, "past max age a new lease gets a new session"
    await held.release()
    assert sessions.closed == [1, 2]


async def test_sp5_a_discarded_session_closes_and_the_next_lease_opens_anew(
    pool: SessionPool[int], sessions: Sessions
) -> None:
    broken = await pool.acquire(ALICE, "v1", sessions.open)
    sharing = await pool.acquire(ALICE, "v1", sessions.open)
    await broken.discard()
    fresh = await pool.acquire(ALICE, "v1", sessions.open)
    assert fresh.session == 2
    assert sessions.closed == []
    await sharing.release()
    assert sessions.closed == [1]


async def test_sp6_a_session_is_never_handed_to_another_owner(
    pool: SessionPool[int], sessions: Sessions, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lease = await pool.acquire(ALICE, "v1", sessions.open)
    await lease.release()
    monkeypatch.setattr(SessionKey, "digest", lambda self: f"{self.tenant}|{self.server_id}")
    pool._entries = {ALICE.digest(): next(iter(pool._entries.values()))}
    caplog.set_level(logging.WARNING)
    with pytest.raises(SessionOwnerMismatch):
        _ = await pool.acquire(SessionKey("org-1", "srv-1", "bob"), "v1", sessions.open)
    assert sessions.opened == [1]
    assert [r.getMessage() for r in caplog.records if "mcp_session_owner_mismatch" in r.getMessage()]


async def test_sp7_close_all_closes_current_and_retired(pool: SessionPool[int], sessions: Sessions) -> None:
    old = await pool.acquire(ALICE, "v1", sessions.open)
    _ = await pool.acquire(ALICE, "v2", sessions.open)
    assert old.session == 1
    assert await pool.close_all() == 2
    assert sorted(sessions.closed) == [1, 2]
    again = await pool.acquire(ALICE, "v2", sessions.open)
    assert again.session == 3


async def test_a_failing_close_is_logged_and_does_not_stop_the_sweep(
    clock: ManualClock, caplog: pytest.LogCaptureFixture
) -> None:
    async def failing(_: int) -> None:
        raise RuntimeError("boom")

    pool: SessionPool[int] = SessionPool(clock=clock, close=failing, config=PoolConfig(idle_ttl_s=1, max_age_s=10))
    for owner in ("a", "b"):
        lease = await pool.acquire(SessionKey("org", "srv", owner), "v", Sessions().open)
        await lease.release()
    _ = clock.advance(2)
    caplog.set_level(logging.WARNING)
    assert await pool.sweep() == 2
    assert len([r for r in caplog.records if "mcp_hub.session_close_failed" in r.getMessage()]) == 2
