"""MS-1..MS-8: ``SandboxMCPRunner`` over the fake broker (P5.10; secrets by proxy injection, OWD-1 WP-06)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

import pytest

from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.egress.trust import TRUST_BUNDLE, TRUST_SCRIPT_ENV
from harness_core.sandbox.mcp_runner import (
    RUNNER_LABEL,
    MCPRunnerSpec,
    RunnerConfig,
    RunnerEgressHost,
    RunnerEgressInvalid,
    RunnerIsolationUnavailable,
    RunnerStartFailed,
    RunnerUnavailable,
    SandboxMCPRunner,
)
from harness_core.sandbox.mcp_runner.sandboxed import bridge_source
from harness_core.sandbox.mcp_runner.types import BRIDGE_DIR, BRIDGE_PATH, BRIDGE_PORT
from harness_core.sandbox.types import (
    LIFETIME_LABEL,
    CredentialInjection,
    EgressDefault,
    RuntimeMode,
)
from harness_core.security.token_patterns import find_forbidden
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.egress import FakeEgressProxy
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_core.testing.sandbox.mcp_echo_server import SECRET_HEADER
from harness_core.testing.sandbox.mcp_runner import (
    ECHO_COMMAND,
    SECRET_NAME,
    MCPRunnerContractEnv,
    digest,
    fake_bridge,
    request,
    session,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.types import LeaseRef, SandboxSpec
    from harness_core.testing.sandbox.egress import EgressRequest
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

CONFIG = RunnerConfig(idle_ttl_s=60, max_age_s=300, reap_grace_s=60, ready_timeout_s=5)
LISTED = "api.listed.test"
UPSTREAM = "http://listed-upstream.internal"


class RecordingBroker(FakeSandboxBroker):
    """Keeps every acquire's spec and idempotency key, for the leak scan."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(**kwargs)
        self.acquired: list[tuple[SandboxSpec, str]] = []

    @override
    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> FakeSandboxHandle:
        self.acquired.append((spec, idempotency_key))
        return await super().acquire(spec, lease=lease, idempotency_key=idempotency_key)


class Env(MCPRunnerContractEnv):
    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock

    @override
    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def env(clock: ManualClock) -> Env:
    return Env(clock)


def make_broker(clock: ManualClock, **kwargs: Any) -> RecordingBroker:  # noqa: ANN401
    programs = kwargs.pop("programs", {**FAKE_PROGRAMS, "python3": fake_bridge()})
    return RecordingBroker(clock=clock, programs=programs, **kwargs)


# The fake proxy's own secret: what it opens sealed routes with (``FakeEgressProxy.sealer``).
SEALER = CredentialSealer(b"fake-egress-secret")


def make_runner(broker: FakeSandboxBroker, clock: ManualClock, **kwargs: Any) -> SandboxMCPRunner:  # noqa: ANN401
    return SandboxMCPRunner(
        broker,
        clock=clock,
        ids=RandomIds(),
        config=kwargs.pop("config", CONFIG),
        sealer=kwargs.pop("sealer", SEALER),
        **kwargs,
    )


def spec_with(env: Env, **changes: Any) -> MCPRunnerSpec:  # noqa: ANN401
    base = env.spec()
    fields: dict[str, Any] = {
        "server_id": base.server_id,
        "image": base.image,
        "command": base.command,
        "args": base.args,
        "env_names": base.env_names,
    }
    return MCPRunnerSpec(**{**fields, **changes})


async def test_ms1_the_runner_sandbox_holds_the_pinned_program_and_a_placeholder_for_its_secret(
    clock: ManualClock, env: Env
) -> None:
    broker = make_broker(clock, modes=frozenset({RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR}))
    runner = make_runner(broker, clock)
    req = request(env, secret="token-ms1-0123456789", spec=spec_with(env, args=("--flag", "x")))
    rpc = await session(runner, req)
    (state,) = broker.sandboxes.values()
    spec = state.spec
    assert spec.image_digest == env.image
    assert spec.mode is RuntimeMode.GVISOR
    assert spec.egress.default is EgressDefault.DENY and not spec.egress.needs_proxy
    assert [(m.target, m.read_only, m.source) for m in spec.mounts] == [(BRIDGE_DIR, True, None)]
    assert spec.labels[RUNNER_LABEL] == "1"
    # max_age_s + reap_grace_s: past it the broker reaps the runner even if this pool died (MCPLEAK).
    assert spec.labels[LIFETIME_LABEL] == "360"
    assert spec.labels["orgId"] == req.key.tenant and spec.labels["mcpServer"] == req.key.server_id
    assert spec.labels["mcpRunnerKey"] == req.key.digest()
    assert state.files[BRIDGE_PATH].data == bridge_source()
    (bridge,) = state.execs
    assert bridge.argv == ("python3", BRIDGE_PATH, str(BRIDGE_PORT), "--", ECHO_COMMAND, "--flag", "x")
    assert bridge.env[SECRET_NAME].startswith("pxh-ph-") and "token-ms1" not in repr(bridge.env)
    assert bridge.env["HOME"] == "/tmp"
    assert "HTTP_PROXY" not in bridge.env and "HTTPS_PROXY" not in bridge.env and "SSL_CERT_FILE" not in bridge.env
    await rpc.aclose()
    _ = await runner.stop_all()


async def test_ms2_a_floor_above_every_broker_mode_is_refused_before_acquire(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock)
    runner = make_runner(broker, clock)
    with pytest.raises(RunnerIsolationUnavailable) as refused:
        _ = await runner.open(request(env, spec=spec_with(env, isolation_floor=RuntimeMode.GVISOR)))
    assert refused.value.reason_code == "runner_isolation_unavailable"
    assert broker.acquired == [] and broker.sandboxes == {}


async def test_ms3_egress_hosts_become_exactly_the_allowlist(clock: ManualClock, env: Env) -> None:
    proxy = FakeEgressProxy(clock=clock, gateways={})
    broker = make_broker(clock, egress_proxy=proxy)
    runner = make_runner(broker, clock)
    hosts = (RunnerEgressHost(LISTED), RunnerEgressHost("api.other.test", (8080, 8443)))
    rpc = await session(runner, request(env, spec=spec_with(env, egress=hosts)))
    (state,) = broker.sandboxes.values()
    egress = state.spec.egress
    assert [(d.host, d.ports) for d in egress.destinations] == [(LISTED, (80,)), ("api.other.test", (8080, 8443))]
    assert egress.default is EgressDefault.DENY
    # The one secret, substituted on its way to each listed host and nowhere else.
    placeholder = state.execs[0].env[SECRET_NAME]
    assert [(r.destination, r.placeholder, r.injection) for r in egress.token_routes] == [
        (LISTED, placeholder, CredentialInjection.SUBSTITUTE),
        ("api.other.test", placeholder, CredentialInjection.SUBSTITUTE),
    ]
    assert SEALER.open(egress.token_routes[0]) == "s3cret-alice-0123456789"
    assert state.execs[0].env["SSL_CERT_FILE"] == TRUST_BUNDLE and TRUST_SCRIPT_ENV in state.execs[0].env
    assert state.execs[0].env["HTTP_PROXY"] == "http://egress-proxy:8097"
    # RN-1 (P5.4b): HTTPS clients send CONNECT to the same proxy.
    assert state.execs[0].env["HTTPS_PROXY"] == state.execs[0].env["https_proxy"] == "http://egress-proxy:8097"
    await rpc.aclose()
    _ = await runner.stop_all()


async def test_ms3_a_malformed_host_is_refused(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock, egress_proxy=FakeEgressProxy(clock=clock, gateways={}))
    runner = make_runner(broker, clock)
    with pytest.raises(RunnerEgressInvalid) as refused:
        _ = await runner.open(request(env, spec=spec_with(env, egress=(RunnerEgressHost("bad host/path"),))))
    assert refused.value.reason_code == "runner_egress_invalid"
    assert broker.acquired == []


async def test_ms3_hosts_on_a_broker_without_a_proxy_are_unavailable_and_leave_nothing(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock)
    runner = make_runner(broker, clock)
    with pytest.raises(RunnerUnavailable) as refused:
        _ = await runner.open(request(env, spec=spec_with(env, egress=(RunnerEgressHost(LISTED),))))
    assert (refused.value.reason_code, refused.value.cause) == ("runner_unavailable", "egress_proxy_missing")
    assert broker.sandboxes == {} and runner.runners() == ()


async def test_ms4_the_server_reaches_a_listed_host_and_nothing_else(clock: ManualClock, env: Env) -> None:
    proxy = FakeEgressProxy(clock=clock, gateways={})
    seen: list[str] = []

    async def upstream(req: EgressRequest) -> Mapping[str, str]:
        seen.append(req.url)
        return {"answer": "from-listed"}

    proxy.route_upstream(UPSTREAM, upstream)
    broker = make_broker(clock, egress_proxy=proxy)
    runner = make_runner(broker, clock)
    rpc = await session(
        runner, request(env, spec=spec_with(env, egress=(RunnerEgressHost(LISTED, upstream=UPSTREAM),)))
    )
    denied = await rpc.call("fetch", {"url": "http://api.unlisted.test/data"})
    assert denied.startswith("403 ") and "destination_not_allowed" in denied
    allowed = await rpc.call("fetch", {"url": f"http://{LISTED}/data"})
    assert allowed.startswith("200 ") and "from-listed" in allowed
    assert seen == [f"{UPSTREAM}/data"]
    await rpc.aclose()
    _ = await runner.stop_all()


async def test_ms5_an_idle_runner_releases_its_sandbox(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock)
    runner = make_runner(broker, clock)
    rpc = await session(runner, request(env))
    await rpc.aclose()
    env.advance(CONFIG.idle_ttl_s)
    assert await runner.sweep() == 1
    assert broker.sandboxes == {} and runner.runners() == ()


async def test_ms5_the_sweep_reaps_another_pools_orphans_past_max_age_only(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock)
    sweeper = make_runner(broker, clock)
    crashed_early, crashed_late = make_runner(broker, clock), make_runner(broker, clock)
    _ = await session(crashed_early, request(env, "early", secret="token-early-0123456789"))
    env.advance(CONFIG.max_age_s + CONFIG.reap_grace_s - 10)
    _ = await session(crashed_late, request(env, "late", secret="token-late-0123456789"))
    env.advance(20)
    assert await sweeper.sweep() == 1
    (survivor,) = broker.sandboxes.values()
    assert survivor.spec.labels["mcpRunnerKey"] == request(env, "late").key.digest()


async def test_ms6_a_bridge_that_never_listens_fails_the_start_and_releases(clock: ManualClock, env: Env) -> None:
    missing = make_broker(clock, programs=dict(FAKE_PROGRAMS))
    runner = make_runner(missing, clock)
    with pytest.raises(RunnerStartFailed) as refused:
        _ = await runner.open(request(env))
    assert refused.value.reason_code == "runner_start_failed"
    assert missing.sandboxes == {} and runner.runners() == ()

    async def silent(_: object) -> int:
        await __import__("asyncio").sleep(3600)
        return 0

    stuck = make_broker(clock, programs={**FAKE_PROGRAMS, "python3": silent})
    runner = make_runner(stuck, clock, config=RunnerConfig(idle_ttl_s=60, max_age_s=300, ready_timeout_s=0.2))
    with pytest.raises(RunnerStartFailed):
        _ = await runner.open(request(env))
    assert stuck.sandboxes == {}


async def test_ms6_a_dead_bridge_is_replaced_on_the_next_open(clock: ManualClock, env: Env) -> None:
    broker = make_broker(clock)
    runner = make_runner(broker, clock)
    rpc = await session(runner, request(env))
    await rpc.aclose()
    (first,) = runner.runners()
    (state,) = broker.sandboxes.values()
    state.processes[0].kill()
    again = await session(runner, request(env))
    assert await again.call("echo", {"text": "revived"}) == "revived"
    (second,) = runner.runners()
    assert second.runner_id != first.runner_id and first.sandbox_id not in broker.sandboxes
    await again.aclose()
    _ = await runner.stop_all()


async def test_ms7_leak_scan_the_secret_is_in_no_process_and_reaches_only_its_listed_host(
    clock: ManualClock, env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "ghp_" + "Ms7CanaryLeakScan0123456789abcdefABCD"
    caplog.set_level(logging.DEBUG)
    proxy = FakeEgressProxy(clock=clock, gateways={})
    received: list[str] = []

    async def upstream(req: EgressRequest) -> Mapping[str, str]:
        received.append(req.headers.get(SECRET_HEADER.lower(), ""))
        return {"ok": "yes"}

    proxy.route_upstream(UPSTREAM, upstream)
    broker = make_broker(clock, egress_proxy=proxy)
    runner = make_runner(broker, clock)
    spec = spec_with(env, egress=(RunnerEgressHost(LISTED, upstream=UPSTREAM),))
    rpc = await session(runner, request(env, secret=canary, spec=spec))
    assert await rpc.call("env_digest", {"name": SECRET_NAME}) != digest(canary)
    assert (await rpc.call("send_secret", {"url": f"http://{LISTED}/x", "name": SECRET_NAME})).startswith("200 ")
    assert received == [canary]
    (state,) = broker.sandboxes.values()
    assert not [e for e in state.execs if canary in repr(e.env) or canary in repr(e.argv)]
    assert not [f for f in state.files.values() if canary.encode() in f.data]
    await rpc.aclose()
    env.advance(CONFIG.idle_ttl_s)
    _ = await runner.sweep()
    (spec, key), = broker.acquired
    surfaces = [repr(dict(spec.labels)), key, repr(broker.calls), repr(runner.runners())]
    # The spec carries the secret only sealed: its routes open under the deployment key alone.
    assert canary not in repr(spec) and SEALER.open(spec.egress.token_routes[0]) == canary
    surfaces += [record.getMessage() for record in caplog.records]
    surfaces += [repr(record.__dict__) for record in caplog.records]
    assert not [s for s in surfaces if canary in s]
    assert not [s for s in surfaces if find_forbidden(s)]
    assert any(r.message.startswith("mcp_runner.started") for r in caplog.records)


async def test_ms8_without_the_deployment_key_a_server_with_a_secret_is_refused_not_handed_it(
    clock: ManualClock, env: Env
) -> None:
    broker = make_broker(clock)
    runner = make_runner(broker, clock, sealer=None)
    with pytest.raises(RunnerUnavailable) as refused:
        _ = await runner.open(request(env, secret="token-ms8-0123456789"))
    assert (refused.value.reason_code, refused.value.cause) == ("runner_unavailable", "credential_sealing_unavailable")
    assert broker.acquired == [] and runner.runners() == ()
    rpc = await session(runner, request(env, secret=None))
    assert await rpc.call("echo", {"text": "no secret, no key needed"}) == "no secret, no key needed"
    await rpc.aclose()
    _ = await runner.stop_all()

