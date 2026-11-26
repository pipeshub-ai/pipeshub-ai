"""Tracers whose spans a test reads: ``RecordingTracer`` as is, ``OtelTracer`` over the SDK's in-memory exporter
(needs ``opentelemetry-sdk``). Both read back as ``FinishedSpan``s, so one contract suite drives both."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.telemetry.memory import FinishedSpan, RecordingTracer

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.otel import OtelTracer
    from harness_core.telemetry.ports import AttrValue


_KIND = {"SpanKind.INTERNAL": "internal", "SpanKind.CLIENT": "client", "SpanKind.SERVER": "server"}


def recording(*, capture_content: bool = False) -> tuple[RecordingTracer, Callable[[], list[FinishedSpan]]]:
    tracer = RecordingTracer(capture_content=capture_content)
    return tracer, lambda: list(tracer.finished)


def otel_in_memory(*, capture_content: bool = False) -> tuple[OtelTracer, Callable[[], list[FinishedSpan]]]:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from harness_core.telemetry.otel import OtelTracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def finished() -> list[FinishedSpan]:
        found: list[FinishedSpan] = []
        for span in exporter.get_finished_spans():
            context = span.get_span_context()
            assert context is not None
            attributes: dict[str, AttrValue] = {
                k: (tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else v)  # type: ignore[misc]
                for k, v in (span.attributes or {}).items()
            }
            error = attributes.get("error.type")
            found.append(
                FinishedSpan(
                    name=span.name,
                    kind=_KIND[str(span.kind)],
                    trace_id=f"{context.trace_id:032x}",
                    span_id=f"{context.span_id:016x}",
                    parent_id=f"{span.parent.span_id:016x}" if span.parent is not None else None,
                    attributes=attributes,
                    error_type=str(error) if error is not None else None,
                    ended=True,
                )
            )
        return found

    return OtelTracer(provider, capture_content=capture_content), finished


__all__ = ["otel_in_memory", "recording"]
