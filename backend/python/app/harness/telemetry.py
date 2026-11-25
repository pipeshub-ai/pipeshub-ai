"""PipesHub's tracer for the harness processes (P9): OpenTelemetry over OTLP/HTTP behind ``HARNESS_OTEL``.

Off (the default): ``NOOP_TRACER``, and no OpenTelemetry module is imported. On: an SDK ``TracerProvider`` with a
batching OTLP/HTTP exporter to ``OTEL_EXPORTER_OTLP_ENDPOINT`` (the exporter reads the standard OTel variables
itself), service name ``OTEL_SERVICE_NAME`` or ``pipeshub-<role>``. Prompts, answers and tool arguments are
recorded only with ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` on (the GenAI conventions' opt-in;
off by default). A deployment that turns tracing on without the exporter installed keeps running untraced and
says so once (``telemetry.unavailable``): tracing is observability, it never stops a run.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.utils.env_flags import HARNESS_OTEL, OTEL_GENAI_CAPTURE_CONTENT, env_flag
from harness_core.logs import HarnessLog
from harness_core.telemetry.memory import NOOP_TRACER

if TYPE_CHECKING:
    from harness_core.telemetry.ports import HarnessTracer

SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"
_LOG = HarnessLog.named("app.harness.telemetry")
_tracers: dict[str, HarnessTracer] = {}


def tracing_enabled() -> bool:
    return env_flag(HARNESS_OTEL)


def content_capture_enabled() -> bool:
    return env_flag(OTEL_GENAI_CAPTURE_CONTENT)


def harness_tracer(role: str) -> HarnessTracer:
    """This process's tracer (one per role, built once)."""
    if not tracing_enabled():
        return NOOP_TRACER
    if role not in _tracers:
        _tracers[role] = _otlp_tracer(role)
    return _tracers[role]


def _otlp_tracer(role: str) -> HarnessTracer:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pyright: ignore[reportMissingImports]
            OTLPSpanExporter,  # pyright: ignore[reportUnknownVariableType]
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        _LOG.warning("telemetry.unavailable", reason="otlp_exporter_missing", role=role, errorType=type(exc).__name__)
        return NOOP_TRACER
    from harness_core.telemetry.otel import OtelTracer

    service = (os.getenv(SERVICE_NAME_ENV) or "").strip() or f"pipeshub-{role}"
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # pyright: ignore[reportUnknownArgumentType]
    capture = content_capture_enabled()
    _LOG.info("telemetry.enabled", role=role, service=service, captureContent=capture)
    return OtelTracer(provider, capture_content=capture)


__all__ = ["SERVICE_NAME_ENV", "content_capture_enabled", "harness_tracer", "tracing_enabled"]
