"""The harness's security decisions on the process's metrics backend (SEC-D-13, ADR-OPS-03).

``harness_core.telemetry.security_signals`` bounds the labels; this module only counts. Every harness metrics module a
service loads (egress, gateways, broker) calls ``install``, so each of those processes counts. Each series of
the forged-token reasons starts at 0: a counter that first appears at 1 has no increase for the alert to see.
"""

from __future__ import annotations

from app.telemetry.backend import METRICS_BACKEND
from harness_core.telemetry import security_signals
from harness_core.telemetry.security_signals import FORGED_REASONS, TOKEN_KINDS

TOKEN_REJECTED = METRICS_BACKEND.counter(
    "pipeshub_agent_harness_token_rejected_total",
    "Machine credentials, attempt tokens and gateway tokens the harness refused, by kind and reason",
    ["kind", "reason"],
)
EGRESS_DECISIONS = METRICS_BACKEND.counter(
    "pipeshub_agent_harness_egress_decisions_total",
    "Sandbox requests the egress proxy allowed (reason: how they went out) or refused (reason: why)",
    ["decision", "reason"],
)


class PrometheusSecuritySignals:
    """``SecuritySignalSink`` on ``METRICS_BACKEND``."""

    def token_rejected(self, kind: str, reason: str) -> None:
        TOKEN_REJECTED.inc(kind, reason)

    def egress_decided(self, decision: str, reason: str) -> None:
        EGRESS_DECISIONS.inc(decision, reason)


def install() -> None:
    for kind in TOKEN_KINDS:
        for reason in sorted(FORGED_REASONS):
            TOKEN_REJECTED.inc(kind, reason, value=0.0)
    security_signals.install(PrometheusSecuritySignals())

