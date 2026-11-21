"""P9 OT-1, OT-7 and the tracer contract (TR-*) on the in-memory and OpenTelemetry adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, override

from harness_core.telemetry import (
    NOOP_TRACER,
    NoopTracer,
    RunRef,
    run_root_span_id,
    run_trace_id,
)
from harness_core.testing.contracts.tracer import HarnessTracerContract
from harness_core.testing.telemetry import otel_in_memory, recording

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.memory import FinishedSpan
    from harness_core.telemetry.ports import HarnessTracer

VECTOR = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "trace-ids.json"


class TestRecordingTracer(HarnessTracerContract):
    @override
    def tracer_env(self, *, capture_content: bool = False) -> tuple[HarnessTracer, Callable[[], list[FinishedSpan]]]:
        return recording(capture_content=capture_content)


class TestOtelTracer(HarnessTracerContract):
    @override
    def tracer_env(self, *, capture_content: bool = False) -> tuple[HarnessTracer, Callable[[], list[FinishedSpan]]]:
        return otel_in_memory(capture_content=capture_content)


def test_ot1_the_noop_tracer_records_nothing_and_captures_no_content() -> None:
    assert isinstance(NOOP_TRACER, NoopTracer)
    assert NOOP_TRACER.capture_content is False
    span = NOOP_TRACER.start("chat m", kind="client", run=RunRef("o", "r"))
    span.set({"a": 1})
    span.rename("x")
    span.end(error_type="e")
    with NOOP_TRACER.span("x", kind="internal") as inner:
        inner.set({"b": 2})


def test_ot1_the_core_package_does_not_import_opentelemetry() -> None:
    """Importing the port and its defaults must not pull the SDK in (a deployment with tracing off pays nothing)."""
    import subprocess

    probe = (
        "import sys, harness_core.telemetry, harness_core.gateway.model_gateway, harness_core.gateway.endpoint;"
        "print(any(m.startswith('opentelemetry') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_ot7_the_trace_context_matches_the_shared_vector() -> None:
    cases = json.loads(VECTOR.read_text())["cases"]
    assert len(cases) >= 3
    for case in cases:
        assert run_trace_id(case["runId"]) == case["traceId"]
        assert run_root_span_id(case["runId"]) == case["rootSpanId"]
        assert len(case["traceId"]) == 32 and len(case["rootSpanId"]) == 16
