"""R36COVER-FU-4: STDIO MCP runners do not outlive their server or their process, and one org cannot fill the host.

On the hub lane 50 runners reached the broker's live-sandbox ceiling (C2.19) and every ``run_code`` and job sandbox
was refused until they were removed by hand: a deleted instance left its runners until idle (15 min), a restart
orphaned them until their lifetime (7 h), and nothing bounded how many one org kept. Decided (recorded in STATUS
R43-MCP): runners stay sandboxes of the install and count against its ceiling, as every other sandbox does, since
the host pays for them alike; they are not counted per run, because a runner is shared by every run of its server
and owner, so one run's count would depend on others' servers. One org keeps at most ``max_per_tenant`` in a pool;
past it a start replaces its least recently used idle runner, and with none idle it is refused ``runner_capacity``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.mcp_runner import (
    RunnerConfig,
    RunnerUnavailable,
    SandboxMCPRunner,
)
from harness_core.sandbox.mcp_runner.types import POOL_LABEL
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_core.testing.sandbox.mcp_runner import (
    MCPRunnerContractEnv,
    fake_bridge,
    request,
    session,
)

if TYPE_CHECKING:
    from harness_core.sandbox.mcp_runner.types import RunnerKey

CONFIG = RunnerConfig(idle_ttl_s=60, max_age_s=300, reap_grace_s=60, ready_timeout_s=5, max_per_tenant=2)
SEALER = CredentialSealer(b"fake-egress-secret")


class _Env(MCPRunnerContractEnv):
    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock

    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


def _broker(clock: ManualClock) -> FakeSandboxBroker:
    return FakeSandboxBroker(clock=clock, programs={**FAKE_PROGRAMS, "python3": fake_bridge()})


def _pool(broker: FakeSandboxBroker, clock: ManualClock, **kwargs: object) -> SandboxMCPRunner:
    return SandboxMCPRunner(broker, clock=clock, ids=RandomIds(), config=CONFIG, sealer=SEALER, **kwargs)  # type: ignore[arg-type]


async def test_lc1_a_deleted_or_disabled_server_loses_its_runners_at_the_next_sweep_not_at_idle() -> None:
    clock = ManualClock()
    env, broker = _Env(clock), _broker(clock)
    removed: set[str] = set()

    async def retain(key: RunnerKey) -> bool:
        return key.server_id not in removed

    pool = _pool(broker, clock, retain=retain)
    gone = await session(pool, request(env, server_id="srv-gone", secret=None))
    kept = await session(pool, request(env, server_id="srv-kept", secret=None))
    assert len(broker.sandboxes) == 2
    removed.add("srv-gone")
    assert await pool.sweep() == 1, "stopped though its session is open and it is far from idle"
    assert {i.key.server_id for i in pool.runners()} == {"srv-kept"}
    assert [s.spec.labels["mcpServer"] for s in broker.sandboxes.values()] == ["srv-kept"]
    await gone.aclose()
    await kept.aclose()


async def test_lc1_a_check_that_fails_keeps_the_runner_for_its_idle_limit() -> None:
    clock = ManualClock()
    env, broker = _Env(clock), _broker(clock)

    async def broken(key: RunnerKey) -> bool:
        raise RuntimeError("config store down")

    pool = _pool(broker, clock, retain=broken)
    rpc = await session(pool, request(env, secret=None))
    await rpc.aclose()
    assert await pool.sweep() == 0 and len(pool.runners()) == 1
    env.advance(CONFIG.idle_ttl_s)
    assert await pool.sweep() == 1 and broker.sandboxes == {}


async def test_lc2_a_restarted_pool_stops_what_its_earlier_process_left_and_nothing_of_another_pool() -> None:
    clock = ManualClock()
    env, broker = _Env(clock), _broker(clock)
    crashed = _pool(broker, clock, pool="host-a:app.query_main")
    other = _pool(broker, clock, pool="host-a:app.harness_main")
    _ = await session(crashed, request(env, server_id="srv-1", secret=None))
    _ = await session(other, request(env, server_id="srv-2", secret=None))
    assert len(broker.sandboxes) == 2
    restarted = _pool(broker, clock, pool="host-a:app.query_main")
    assert await restarted.reap_previous() == 1
    (survivor,) = broker.sandboxes.values()
    assert survivor.spec.labels[POOL_LABEL] == "host-a:app.harness_main"
    assert await _pool(broker, clock).reap_previous() == 0, "a pool with no name reaps nothing"


async def test_lc3_past_the_orgs_cap_a_start_replaces_its_least_recently_used_idle_runner() -> None:
    clock = ManualClock()
    env, broker = _Env(clock), _broker(clock)
    pool = _pool(broker, clock)
    first = await session(pool, request(env, server_id="srv-1", secret=None))
    await first.aclose()
    env.advance(5)
    second = await session(pool, request(env, server_id="srv-2", secret=None))
    third = await session(pool, request(env, server_id="srv-3", secret=None))
    assert {i.key.server_id for i in pool.runners()} == {"srv-2", "srv-3"}
    assert len(broker.sandboxes) == 2
    # Another org has its own share.
    elsewhere = await session(pool, request(env, tenant="org-other", server_id="srv-9", secret=None))
    assert len(broker.sandboxes) == 3
    for rpc in (second, third, elsewhere):
        await rpc.aclose()


async def test_lc3_with_every_runner_of_the_org_in_use_a_start_is_refused_and_starts_nothing() -> None:
    clock = ManualClock()
    env, broker = _Env(clock), _broker(clock)
    pool = _pool(broker, clock)
    busy = [await session(pool, request(env, server_id=f"srv-{i}", secret=None)) for i in (1, 2)]
    try:
        _ = await session(pool, request(env, server_id="srv-3", secret=None))
    except RunnerUnavailable as refused:
        assert refused.cause == "runner_capacity"
    else:
        raise AssertionError("a third runner started past the cap")
    assert len(broker.sandboxes) == 2
    for rpc in busy:
        await rpc.aclose()
