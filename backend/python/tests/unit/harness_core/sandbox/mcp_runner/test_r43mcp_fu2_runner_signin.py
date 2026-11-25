"""R43MCP-FU-2: a runner stops with its owner's sign-in, and retires at the sweep when the secret changed.

``retain`` may answer the secrets the server would start with now; the sweep compares their fingerprint with the
running one's. Before, a removed sign-in kept its runner until idle (15 min) and a changed secret until the next open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.mcp_runner import RunnerConfig, SandboxMCPRunner
from harness_core.sandbox.mcp_runner.types import RunnerSecrets
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_core.testing.sandbox.mcp_runner import (
    SECRET_NAME,
    MCPRunnerContractEnv,
    fake_bridge,
    request,
    session,
)

if TYPE_CHECKING:
    from harness_core.sandbox.mcp_runner.types import RunnerKey

CONFIG = RunnerConfig(idle_ttl_s=60, max_age_s=300, reap_grace_s=60, ready_timeout_s=5)
SECRET = "s3cret-alice-0123456789"


def _pool(clock: ManualClock, retain: object) -> tuple[SandboxMCPRunner, FakeSandboxBroker]:
    broker = FakeSandboxBroker(clock=clock, programs={**FAKE_PROGRAMS, "python3": fake_bridge()})
    pool = SandboxMCPRunner(
        broker, clock=clock, ids=RandomIds(), config=CONFIG, sealer=CredentialSealer(b"fake-egress-secret"),
        retain=retain,  # type: ignore[arg-type]
    )
    return pool, broker


async def test_fu2_a_removed_sign_in_stops_its_runner_at_the_next_sweep() -> None:
    clock = ManualClock()
    signed_in = {"alice": True}

    async def retain(key: RunnerKey) -> bool | RunnerSecrets:
        return RunnerSecrets({SECRET_NAME: SECRET}) if signed_in[key.owner] else False

    pool, broker = _pool(clock, retain)
    rpc = await session(pool, request(MCPRunnerContractEnv(), secret=SECRET))
    assert await pool.sweep() == 0 and len(broker.sandboxes) == 1, "an unchanged sign-in keeps its runner"
    signed_in["alice"] = False
    assert await pool.sweep() == 1
    assert pool.runners() == () and broker.sandboxes == {}
    await rpc.aclose()


async def test_fu2_a_changed_secret_retires_the_runner_at_the_next_sweep_not_at_the_next_open() -> None:
    clock = ManualClock()
    current = {"secret": SECRET}

    async def retain(key: RunnerKey) -> bool | RunnerSecrets:
        return RunnerSecrets({SECRET_NAME: current["secret"]})

    pool, broker = _pool(clock, retain)
    rpc = await session(pool, request(MCPRunnerContractEnv(), secret=SECRET))
    current["secret"] = "s3cret-alice-rotated-98765"
    _ = await pool.sweep()
    assert len(broker.sandboxes) == 1, "a session still open on the old secret is not cut"
    assert pool.runners()[0].sessions == 1
    await rpc.aclose()
    assert await pool.sweep() == 1 and broker.sandboxes == {} and pool.runners() == ()


async def test_fu2_an_idle_runner_with_a_changed_secret_stops_at_once() -> None:
    clock = ManualClock()

    async def retain(key: RunnerKey) -> bool | RunnerSecrets:
        return RunnerSecrets({SECRET_NAME: "s3cret-alice-rotated-98765"})

    pool, broker = _pool(clock, retain)
    rpc = await session(pool, request(MCPRunnerContractEnv(), secret=SECRET))
    await rpc.aclose()
    _ = await pool.sweep()
    assert broker.sandboxes == {} and pool.runners() == ()


async def test_fu2_a_server_with_no_secret_and_none_now_is_kept() -> None:
    clock = ManualClock()

    async def retain(key: RunnerKey) -> bool | RunnerSecrets:
        return RunnerSecrets()

    pool, broker = _pool(clock, retain)
    rpc = await session(pool, request(MCPRunnerContractEnv(), secret=None))
    await rpc.aclose()
    assert await pool.sweep() == 0 and len(broker.sandboxes) == 1


async def test_fu2_a_check_that_fails_keeps_the_runner() -> None:
    clock = ManualClock()

    async def retain(key: RunnerKey) -> bool | RunnerSecrets:
        raise ConnectionError("credential store unreachable")

    pool, broker = _pool(clock, retain)
    rpc = await session(pool, request(MCPRunnerContractEnv(), secret=SECRET))
    await rpc.aclose()
    assert await pool.sweep() == 0 and len(broker.sandboxes) == 1
