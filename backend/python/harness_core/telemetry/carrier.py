"""Trace context for a process the harness starts (OB-01): OpenTelemetry's environment-variable carrier.

An engine in its sandbox is handed ``TRACEPARENT`` (the span current when it is launched, its attempt's) and
``OTEL_RESOURCE_ATTRIBUTES`` naming the run, so the engine's own OpenTelemetry joins the run's trace. Only ids the
engine already works under are named: no org id, no token, nothing a sandbox does not hold anyway. Nothing is added
when tracing is off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from harness_core.telemetry.ports import HarnessTracer, RunRef

TRACEPARENT_ENV = "TRACEPARENT"
RESOURCE_ATTRIBUTES_ENV = "OTEL_RESOURCE_ATTRIBUTES"


def trace_env(tracer: HarnessTracer, run: RunRef) -> dict[str, str]:
    traceparent = tracer.traceparent()
    if traceparent is None:
        return {}
    named = {
        "pipeshub.run_id": run.run_id,
        "pipeshub.session_id": run.session_id,
        "pipeshub.attempt_id": run.attempt_id,
    }
    resource = ",".join(f"{key}={quote(value, safe='')}" for key, value in named.items() if value)
    return {TRACEPARENT_ENV: traceparent, RESOURCE_ATTRIBUTES_ENV: resource}


__all__ = ["RESOURCE_ATTRIBUTES_ENV", "TRACEPARENT_ENV", "trace_env"]
