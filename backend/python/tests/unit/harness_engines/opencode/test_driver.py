"""OC-11, OC-12, OC-14, LG-1, LG-2: the HTTP driver, a wrong binary, start-up, lifecycle and failure logs."""

from __future__ import annotations

import asyncio
import logging

import httpcore
import pytest

from harness_core.engines.errors import EngineBinaryMismatch, EngineCrashed
from harness_core.engines.events import turn_id_for
from harness_core.engines.types import EngineTask
from harness_core.testing.engines.contract import (
    ENDPOINTS,
    collect,
    make_profile,
    new_engine_sandbox,
    open_rig,
    start_held_turn,
)
from harness_core.testing.engines.gateway import ConformanceGateway
from harness_core.testing.sandbox.egress import FakeEgress
from harness_core.testing.virtual_time import run_on_virtual_time
from harness_engines.opencode_api.http import (
    OpencodeHttp,
    OpencodeHttpError,
    SandboxNetworkBackend,
    SseParser,
)
from tests.unit.harness_engines.opencode.conftest import OpencodeHarness


def test_oc11_sse_events_split_across_chunks() -> None:
    parser = SseParser()
    assert parser.feed(b'data: {"type": "a"') == []
    assert parser.feed(b"}\r\n\r\n: keep-alive\n\ndata: x\ndata: y\n") == ['{"type": "a"}']
    assert parser.feed(b"\n") == ["x\ny"]


async def test_oc11_basic_auth_is_required() -> None:
    harness = OpencodeHarness()
    rig = await open_rig(harness)
    try:
        assert (await rig.session.health()).alive
        wrong = OpencodeHttp(rig.sandbox, 4096, "wrong-password")
        with pytest.raises(OpencodeHttpError) as refused:
            await wrong.request("GET", "/global/health")
        assert refused.value.status == 401
        await wrong.aclose()
    finally:
        await rig.session.close()


async def test_oc14_a_port_nothing_listens_on_yet_is_a_connect_error_the_probe_retries() -> None:
    # Found in X1: the broker reports it as SandboxConnectError, not ConnectionError (P5.1 port).
    sandbox = await new_engine_sandbox(FakeEgress())
    backend = SandboxNetworkBackend(sandbox, 4096)
    with pytest.raises(httpcore.ConnectError):
        _ = await backend.connect_tcp("127.0.0.1", 4096)


async def test_oc12_wrong_binary_is_refused_and_stopped() -> None:
    harness = OpencodeHarness(binary_version="1.18.33")
    egress = FakeEgress()
    ConformanceGateway(endpoints=ENDPOINTS, grant=()).install(egress)
    sandbox = await harness.new_sandbox(egress)
    launch = harness.adapter.render_launch(make_profile(harness), ENDPOINTS, "/skills")
    with pytest.raises(EngineBinaryMismatch):
        await harness.adapter.open(sandbox, launch, None)
    assert [e.argv[0] for e in sandbox.execs] == ["sha256sum", launch.argv[0]]
    assert not any(p.running for p in sandbox.processes)


def _events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, dict[str, object]]]:
    return [
        (r.harness_event, r.harness)  # type: ignore[attr-defined]
        for r in caplog.records
        if hasattr(r, "harness_event")
    ]


async def test_lg1_lifecycle_logs_carry_correlation_and_no_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    harness = OpencodeHarness()
    egress = FakeEgress()
    ConformanceGateway(endpoints=ENDPOINTS, grant=()).install(egress)
    sandbox = await harness.new_sandbox(egress)
    launch = harness.adapter.render_launch(make_profile(harness), ENDPOINTS, "/skills")
    session = await harness.adapter.open(sandbox, launch, None, correlation={"runId": "run-7", "attemptId": "att-1"})
    await collect(session, EngineTask(turn_id_for("run-7", 0), "say SECRET-CONTENT-MARKER"))
    await session.close()
    events = _events(caplog)
    names = [name for name, _ in events]
    assert names == [
        "engine.session.opened",
        "engine.turn.started",
        "engine.turn.completed",
        "engine.session.closed",
    ]
    for _, fields in events:
        assert fields["runId"] == "run-7"
        assert fields["attemptId"] == "att-1"
        assert fields["sandboxId"] == sandbox.id
    assert "SECRET-CONTENT-MARKER" not in caplog.text
    assert not any(p in caplog.text for p in harness.passwords)


async def test_lg2_a_dead_event_stream_is_a_crash_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """LCFIX: a dead stream is the engine gone mid-turn, as for Codex and Claude Code, never a completed turn."""
    caplog.set_level(logging.INFO)
    harness = OpencodeHarness()
    rig = await open_rig(harness)
    try:
        runner = await start_held_turn(rig, EngineTask(turn_id_for("run", 0), "hold\nsay never"))
        program = rig.sandbox.processes[-1]
        assert program.running
        harness.fakes[rig.sandbox.id].servers[0].drop_event_streams()
        with pytest.raises(EngineCrashed):
            _ = await runner
        failures = [f for name, f in _events(caplog) if name == "engine.stream.failed"]
        assert len(failures) == 1
        assert failures[0]["reasonCode"] == "stream_failed"
    finally:
        rig.gateway.release()
        await rig.session.close()



class _StallingHttp:
    """Refuses once, then accepts and never answers twice (listening, not serving), then answers."""

    def __init__(self, script: list[Exception | None]) -> None:
        self.script = script
        self.timeouts: list[float] = []

    async def request(self, method: str, path: str, body: object = None, *, timeout_s: float = 30.0) -> object:
        self.timeouts.append(timeout_s)
        step = self.script.pop(0)
        if step is not None:
            raise step
        return {"healthy": True, "version": "1.18.34"}


async def test_os1_a_probe_that_connects_but_stalls_is_retried_soon_then_with_longer_waits() -> None:
    """P5.9a OS-1: each stalled probe cost 2 s of every opencode start; now 0.4 s, doubling to 2 s."""
    from harness_engines.opencode.adapter import (
        STARTUP_FIRST_PROBE_S,
        STARTUP_POLL_S,
        STARTUP_PROBE_TIMEOUT_S,
        OpencodeAdapter,
    )

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stall = httpcore.ReadTimeout("no answer yet")
    http = _StallingHttp([httpcore.ConnectError("refused"), stall, stall, None])
    adapter = OpencodeAdapter(sleep=sleep)
    assert await adapter._wait_healthy(http) == "1.18.34"  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert http.timeouts == [STARTUP_FIRST_PROBE_S, STARTUP_FIRST_PROBE_S, 2 * STARTUP_FIRST_PROBE_S, 4 * STARTUP_FIRST_PROBE_S]
    assert sleeps == [STARTUP_POLL_S], "a stalled probe already waited; only a refusal sleeps"
    slow = _StallingHttp([stall] * 6 + [None])
    assert await OpencodeAdapter(sleep=sleep)._wait_healthy(slow) == "1.18.34"  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert max(slow.timeouts) == STARTUP_PROBE_TIMEOUT_S


class _UntilServing:
    """Listening, not serving: a probe hangs until cancelled; once ``serving``, a probe is answered."""

    def __init__(self) -> None:
        self.serving = False
        self.cancelled = 0

    async def request(self, method: str, path: str, body: object = None, *, timeout_s: float = 30.0) -> object:
        if self.serving:
            return {"healthy": True, "version": "1.18.34"}
        try:
            await asyncio.sleep(timeout_s)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise httpcore.ReadTimeout("no answer yet")


@pytest.mark.parametrize("refused_first", [False, True], ids=["stalled", "refused"])
def test_os2_the_servers_own_listening_line_ends_the_wait_for_a_stalled_or_refused_probe(
    refused_first: bool,
) -> None:
    """WP-17 (PF-01): a probe sent while opencode accepts but does not yet serve stalls for its whole first wait
    (0.25 s of a ~2 s warm start, P5.9a OS-1). opencode prints its listening line once it serves: the probe or the
    poll in flight is dropped then, and the next probe goes out at once."""
    from harness_engines.opencode.adapter import STARTUP_FIRST_PROBE_S, OpencodeAdapter

    async def main() -> None:
        http = _UntilServing()
        ready = asyncio.Event()
        adapter = OpencodeAdapter()
        if refused_first:
            refusals = [httpcore.ConnectError("refused")]
            serving = http.request

            async def refusing(method: str, path: str, body: object = None, *, timeout_s: float = 30.0) -> object:
                if refusals:
                    raise refusals.pop()
                return await serving(method, path, body, timeout_s=timeout_s)

            http.request = refusing  # type: ignore[method-assign]

        async def says_it_serves() -> None:
            await asyncio.sleep(0.02)
            http.serving = True
            ready.set()

        loop = asyncio.get_running_loop()
        started = loop.time()
        announce = asyncio.ensure_future(says_it_serves())
        assert await adapter._wait_healthy(http, ready) == "1.18.34"  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        await announce
        assert loop.time() - started < STARTUP_FIRST_PROBE_S / 2

    run_on_virtual_time(main)
