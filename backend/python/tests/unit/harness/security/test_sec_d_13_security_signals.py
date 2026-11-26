"""SEC-D-13 (ADR-OPS-03): the Python services count their security decisions where they make them.

- the egress proxy counts every request it lets out (by how) and every refusal (by its reason), once, also when the
  sink or the refusal's logger throws, and the answer never changes;
- the egress control port, the sandbox broker and the gateways' ``/metrics`` count a refused machine credential;
- the Model and Tool Gateways' metrics adapter counts a refused gateway token, and only that;
- every label is mapped onto a fixed set: a token, a host or caller text never becomes a series;
- the Prometheus sink is installed by the metrics modules every service loads, and the forged-token series start at
  0 so the first forgery shows as an increase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestClient, TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.server import create_control_app
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.telemetry import security_signals
from harness_core.telemetry.exposition import MetricsExposition
from harness_core.telemetry.metrics import GatewayRequest
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import Iterator

R = EgressRefusalReason
SECRET = b"sec-d-13-secret"
PH = "ph-model-0123456789abcdef"
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)), EgressDestination("public.example", (80,))),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
A = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, {"orgId": "o1", "runId": "r1"})
CREDENTIAL = "phw_sec_d_13_credential"


@dataclass
class RecordingSink:
    tokens: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    egress: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def token_rejected(self, kind: str, reason: str) -> None:
        self.tokens.append((kind, reason))

    def egress_decided(self, decision: str, reason: str) -> None:
        self.egress.append((decision, reason))


class ExplodingSink:
    def token_rejected(self, kind: str, reason: str) -> None:
        raise RuntimeError("registry full")

    def egress_decided(self, decision: str, reason: str) -> None:
        raise RuntimeError("registry full")


@pytest.fixture
def sink() -> Iterator[RecordingSink]:
    from app.telemetry.modules import security_metrics

    recording = RecordingSink()
    security_signals.install(recording)
    yield recording
    security_metrics.install()


async def _proxy() -> EgressProxy:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(A)
    return EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, clock, ttl_s=600),
        resolver=StaticResolver({"public.example": ["93.184.216.34"]}),
        upstreams={"model-gateway": "http://upstream-model:9000/"},
        clock=clock,
        ids=SeededIds(),
    )


def _call(target: str = "/v1/chat", *, auth: str | None = f"Bearer {PH}") -> ProxyRequest:
    headers = [("Host", "model-gateway:8097")]
    if auth is not None:
        headers.append(("Authorization", auth))
    return ProxyRequest("POST", target, tuple(headers), b"{}")


async def _refused(proxy: EgressProxy, request: ProxyRequest, *, local: str = "10.213.0.2") -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize(request, local=local, peer="10.213.0.3")
    return caught.value.reason


# --- the egress proxy ------------------------------------------------------------------------------------------------


async def test_each_allowed_and_refused_request_is_one_decision_with_its_reason(sink: RecordingSink) -> None:
    proxy = await _proxy()
    _ = await proxy.authorize(_call(), local="10.213.0.2", peer="10.213.0.3")
    assert await _refused(proxy, _call("http://evil.example/")) == R.DESTINATION_NOT_ALLOWED
    assert await _refused(proxy, _call(auth=None)) == R.PLACEHOLDER_MISSING
    assert await _refused(proxy, _call(), local="10.213.0.99") == R.UNBOUND_SOURCE
    assert sink.egress == [
        ("allowed", "http"),
        ("refused", "destination_not_allowed"),
        ("refused", "placeholder_missing"),
        ("refused", "unbound_source"),
    ]


async def test_a_host_or_a_token_is_never_a_label(sink: RecordingSink) -> None:
    proxy = await _proxy()
    _ = await _refused(proxy, _call("http://c2VjcmV0.exfil.example/"))
    _ = await _refused(proxy, _call(auth="Bearer sk-ant-api03-not-mine-000000"))
    text = repr(sink.egress)
    for leaked in ("c2VjcmV0", "exfil", "sk-ant", PH):
        assert leaked not in text
    assert {reason for _, reason in sink.egress} <= set(security_signals.egress_refused_reasons())


async def test_a_sink_that_throws_never_changes_a_decision() -> None:
    from app.telemetry.modules import security_metrics

    security_signals.install(ExplodingSink())
    try:
        proxy = await _proxy()
        forward = await proxy.authorize(_call(), local="10.213.0.2", peer="10.213.0.3")
        assert forward.url.startswith("http://upstream-model:9000/")
        assert await _refused(proxy, _call("http://evil.example/")) == R.DESTINATION_NOT_ALLOWED
    finally:
        security_metrics.install()


async def test_a_refusal_is_counted_before_its_log_line_so_a_failing_logger_loses_no_count(
    sink: RecordingSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy = await _proxy()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("log sink down")

    monkeypatch.setattr(proxy._log, "warning", broken)
    with pytest.raises(OSError, match="log sink down"):
        _ = await proxy.authorize(_call("http://evil.example/"), local="10.213.0.2", peer="10.213.0.3")
    assert sink.egress == [("refused", "destination_not_allowed")]


# --- machine credentials ---------------------------------------------------------------------------------------------


async def test_the_egress_control_port_counts_a_missing_and_a_wrong_credential(sink: RecordingSink) -> None:
    proxy = await _proxy()
    app = create_control_app(proxy, StaticBearerAuth(CREDENTIAL), instance="egress-1", metrics=lambda: "")
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/metrics")).status == 401
        assert (await client.get("/metrics", headers={"Authorization": "Bearer stolen"})).status == 401
        assert (await client.get("/metrics", headers={"Authorization": f"Bearer {CREDENTIAL}"})).status == 200
    assert sink.tokens == [("machine_credential", "missing"), ("machine_credential", "credential")]


async def test_the_sandbox_broker_counts_a_refused_credential(sink: RecordingSink) -> None:
    app = create_broker_app(object(), StaticBearerAuth(CREDENTIAL))  # pyright: ignore[reportArgumentType]
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/v1/sandboxes", headers={"Authorization": "Bearer stolen"})).status == 401
    assert sink.tokens == [("machine_credential", "credential")]


async def test_a_gateways_metrics_route_counts_a_refused_scraper(sink: RecordingSink) -> None:
    exposition = MetricsExposition(StaticBearerAuth(CREDENTIAL), lambda: "x 1\n")
    assert await exposition.answer({"Authorization": f"Bearer {PH}"}) is None
    assert await exposition.answer({}) is None
    assert await exposition.answer({"Authorization": f"Bearer {CREDENTIAL}"}) == "x 1\n"
    assert sink.tokens == [("machine_credential", "credential"), ("machine_credential", "missing")]


# --- gateway tokens --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "counted"),
    [
        ("token_signature", [("gateway_token", "signature")]),
        ("token_unknown_key", [("gateway_token", "unknown_key")]),
        ("token_expired", [("gateway_token", "expired")]),
        ("token_missing", [("gateway_token", "missing")]),
        ("binding_mismatch", [("gateway_token", "claims")]),
        # A request its token was good for, and the scraper's credential (counted where it is checked).
        ("model_not_granted", []),
        ("credential_not_accepted", []),
        (None, []),
    ],
)
def test_the_gateways_count_a_refused_token_and_nothing_else(
    sink: RecordingSink, reason: str | None, counted: list[tuple[str, str]]
) -> None:
    from app.telemetry.modules.harness_gateway_metrics import BackendGatewayMetrics

    BackendGatewayMetrics().request(GatewayRequest("model", "chat_completions", "refused", 0.01, reason=reason))
    assert sink.tokens == counted


# --- fixed label sets ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "reason", "labels"),
    [
        ("gateway_token", "token_signature", ("gateway_token", "signature")),
        ("attempt_token", "superseded", ("attempt_token", "superseded")),
        ("machine_credential", None, ("machine_credential", "missing")),
        ("Bearer phw_secret", "token_signature", ("other", "signature")),
        ("gateway_token", 'sig"} 1\nx{', ("gateway_token", "other")),
        ("gateway_token", "TOKEN_SIGNATURE", ("gateway_token", "other")),
        ("gateway_token", "token_" + "a" * 500, ("gateway_token", "other")),
        ("gateway_token", "signature​", ("gateway_token", "other")),
    ],
)
def test_token_labels_are_a_fixed_set(kind: str, reason: str | None, labels: tuple[str, str]) -> None:
    assert (security_signals.token_kind(kind), security_signals.token_reason(reason)) == labels


@pytest.mark.parametrize(
    ("decision", "reason", "labels"),
    [
        ("allowed", "tunnel", ("allowed", "tunnel")),
        ("refused", "site_relay", ("refused", "site_relay")),
        ("refused", "http", ("refused", "other")),
        ("allowed", "destination_not_allowed", ("allowed", "other")),
        ("denied", "http", ("other", "other")),
        ("refused", None, ("refused", "other")),
    ],
)
def test_egress_labels_are_a_fixed_set(decision: str, reason: str | None, labels: tuple[str, str]) -> None:
    assert security_signals.egress_labels(decision, reason) == labels


def test_every_refusal_reason_is_a_known_label_so_a_new_reason_is_counted_by_name() -> None:
    assert set(security_signals.egress_refused_reasons()) == {r.value for r in EgressRefusalReason}


# --- the Prometheus sink ---------------------------------------------------------------------------------------------


def test_the_metrics_modules_install_the_sink_and_the_forged_series_start_at_zero() -> None:
    from app.telemetry.backend import METRICS_BACKEND
    from app.telemetry.modules import egress_metrics, security_metrics  # noqa: F401 - what the egress proxy loads

    security_metrics.install()
    security_signals.token_rejected("gateway_token", "token_signature")
    security_signals.egress_decided("refused", "control_from_sandbox")
    text = METRICS_BACKEND.serialize()
    assert 'pipeshub_agent_harness_token_rejected_total{kind="attempt_token",reason="unknown_key"}' in text
    assert 'pipeshub_agent_harness_token_rejected_total{kind="gateway_token",reason="signature"}' in text
    assert (
        'pipeshub_agent_harness_egress_decisions_total{decision="refused",reason="control_from_sandbox"}' in text
    )
    for kind in security_signals.TOKEN_KINDS:
        for reason in security_signals.FORGED_REASONS:
            assert f'pipeshub_agent_harness_token_rejected_total{{kind="{kind}",reason="{reason}"}}' in text
