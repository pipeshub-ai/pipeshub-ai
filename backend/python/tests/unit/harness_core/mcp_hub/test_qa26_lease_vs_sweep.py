"""QA26-P3 (MCP-27, MCP-G7): leases racing the idle sweep, on the MCP session pool and the runner table.

Interleavings are chosen, not timed: every open, close and connect yields to the loop a
drawn number of times, so Hypothesis explores task orderings deterministically and
shrinks a failure to its shortest schedule. Time moves only through ``ManualClock``.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness_core.ids import RandomIds
from harness_core.mcp_hub import PoolConfig, SessionKey, SessionPool
from harness_core.sandbox.mcp_runner.table import RunnerTable
from harness_core.sandbox.streams import memory_stream_pair
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.mcp_runner import MCPRunnerContractEnv, request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.mcp_hub.pool import SessionLease
    from harness_core.sandbox.mcp_runner.types import RunnerRequest
    from harness_core.sandbox.ports import ByteStream

IDLE_S = 60.0
KEYS = tuple(SessionKey(tenant, "srv-1", owner) for tenant in ("org-a", "org-b") for owner in ("alice", "bob"))


@dataclass
class Session:
    sid: int
    key: SessionKey
    closed: bool = False


@dataclass
class Sessions:
    """Counted sessions whose open and close yield ``delays`` times each, in draw order."""

    delays: itertools.cycle[int]
    ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    opened: list[Session] = field(default_factory=lambda: list[Session]())

    async def _yield(self) -> None:
        for _ in range(next(self.delays)):
            await asyncio.sleep(0)

    def opener(self, key: SessionKey) -> Callable[[], Awaitable[Session]]:
        async def open_() -> Session:
            await self._yield()
            session = Session(next(self.ids), key)
            self.opened.append(session)
            return session

        return open_

    async def close(self, session: Session) -> None:
        await self._yield()
        session.closed = True


async def _hold(pool: SessionPool[Session], sessions: Sessions, key: SessionKey, polls: int) -> SessionLease[Session]:
    lease = await pool.acquire(key, "v1", sessions.opener(key))
    assert not lease.session.closed, "a lease was handed a closed session"
    assert lease.key == key and lease.session.key == key, "a lease crossed owners or orgs"
    for _ in range(polls):
        await asyncio.sleep(0)
        assert not lease.session.closed, "the sweep closed a session under a live lease"
    return lease


async def _race(ops: list[tuple[str, int, int]], delays: list[int]) -> None:
    clock = ManualClock()
    sessions = Sessions(itertools.cycle(delays or [0]))
    pool: SessionPool[Session] = SessionPool(
        clock=clock, close=sessions.close, config=PoolConfig(idle_ttl_s=IDLE_S, max_age_s=10 * IDLE_S)
    )
    for key in KEYS:
        seeded = await pool.acquire(key, "v1", sessions.opener(key))
        await seeded.release()
    _ = clock.advance(IDLE_S + 1)

    async def op(kind: str, index: int, polls: int) -> SessionLease[Session] | None:
        if kind == "sweep":
            _ = await pool.sweep()
            return None
        if kind == "advance":
            _ = clock.advance(IDLE_S / 2)
            return None
        lease = await _hold(pool, sessions, KEYS[index % len(KEYS)], polls)
        if kind == "lease_release":
            await lease.release()
            return None
        return lease

    held = [lease for lease in await asyncio.gather(*(op(*o) for o in ops)) if lease is not None]
    for lease in held:
        assert not lease.session.closed, "a lease still held ended with a closed session"
        await lease.release()
    by_session: dict[int, set[SessionKey]] = {}
    for session in sessions.opened:
        by_session.setdefault(session.sid, set()).add(session.key)
    assert all(len(keys) == 1 for keys in by_session.values())
    _ = await pool.close_all()
    assert all(s.closed for s in sessions.opened), "close_all left a session open"


_OPS = st.lists(
    st.tuples(
        st.sampled_from(["lease", "lease", "lease_release", "sweep", "advance"]),
        st.integers(0, len(KEYS) - 1),
        st.integers(0, 3),
    ),
    min_size=20,
    max_size=40,
)


@settings(max_examples=200, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])
@given(ops=_OPS, delays=st.lists(st.integers(0, 3), max_size=12))
def test_qa26_p3_leases_racing_the_idle_sweep_never_get_a_closed_or_foreign_session(
    ops: list[tuple[str, int, int]], delays: list[int]
) -> None:
    asyncio.run(_race(ops, delays))


async def test_qa26_p3_twenty_concurrent_leases_while_the_pool_sweeps() -> None:
    ops = [("lease", i, i % 4) for i in range(20)]
    for at in range(0, 20, 4):
        ops.insert(at, ("sweep", 0, 0))
    await _race(ops, [0, 1, 2, 3])


class _GatedBackend:
    """A started runner whose ``connect`` waits on the test, the window an open spends in the bridge."""

    def __init__(self, gate: asyncio.Event, served: list[_GatedBackend]) -> None:
        self.gate = gate
        self.served = served
        self.stopped = False

    @property
    def sandbox_id(self) -> str | None:
        return None

    def alive(self) -> bool:
        return not self.stopped

    async def connect(self) -> ByteStream:
        await self.gate.wait()
        # The bridge accepted before the stop landed: the stream is live until the runner goes.
        self.served.append(self)
        client, _server = memory_stream_pair()
        return client

    async def stop(self) -> None:
        self.stopped = True


class _GatedRunners(RunnerTable):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock=clock, ids=RandomIds(), config=MCPRunnerContractEnv.config)
        self.gate = asyncio.Event()
        self.gate.set()
        self.started: list[_GatedBackend] = []
        self.served: list[_GatedBackend] = []

    @override
    async def _start(self, runner_id: str, request: RunnerRequest) -> _GatedBackend:
        backend = _GatedBackend(self.gate, self.served)
        self.started.append(backend)
        return backend


async def test_qa26_p3_an_open_in_flight_keeps_its_idle_runner_from_the_sweep() -> None:
    clock = ManualClock()
    runners = _GatedRunners(clock)
    env = MCPRunnerContractEnv()
    first = await runners.open(request(env))
    await first.aclose()
    _ = clock.advance(env.config.idle_ttl_s + 1)

    runners.gate.clear()
    opening = asyncio.ensure_future(runners.open(request(env)))
    for _ in range(3):
        await asyncio.sleep(0)
    _ = await runners.sweep()
    runners.gate.set()
    stream = await opening

    serving = runners.served[-1]
    assert not serving.stopped, "the open returned a session on a runner the sweep stopped"
    (info,) = runners.runners()
    assert info.sessions == 1
    await stream.aclose()
