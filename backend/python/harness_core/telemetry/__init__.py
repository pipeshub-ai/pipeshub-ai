"""Tracing for the harness (P9): a port, a no-op default, an in-memory recorder; ``harness_core.telemetry.otel``
adapts OpenTelemetry (imported only by whoever binds it)."""

from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.telemetry.memory import (
    NOOP_TRACER,
    FinishedSpan,
    NoopTracer,
    RecordingTracer,
)
from harness_core.telemetry.ports import (
    AttrValue,
    HarnessTracer,
    RunRef,
    SpanHandle,
    SpanKind,
)

__all__ = [
    "NOOP_TRACER",
    "AttrValue",
    "FinishedSpan",
    "HarnessTracer",
    "NoopTracer",
    "RecordingTracer",
    "RunRef",
    "SpanHandle",
    "SpanKind",
    "run_root_span_id",
    "run_trace_id",
]
