"""``OtelTracer``: the tracing port over the OpenTelemetry API (needs ``opentelemetry-api``; the SDK and an exporter
are the embedder's choice, see ``app.harness.telemetry`` for PipesHub's OTLP wiring).

A span with no current parent in this context is parented to the run's root, a remote span context derived
from the run id (``harness_core.telemetry.ids``), so the worker, the gateways and Node's admission share one trace.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
)

from harness_core.telemetry.genai import ERROR_TYPE
from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.telemetry.masking import masked_attributes, masked_text

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from opentelemetry.context import Context

    from harness_core.telemetry.ports import AttrValue, RunRef, SpanKind

_KINDS = {
    "internal": trace.SpanKind.INTERNAL,
    "client": trace.SpanKind.CLIENT,
    "server": trace.SpanKind.SERVER,
}
SCOPE = "pipeshub.agent_harness"


def _clean(attributes: Mapping[str, AttrValue | None]) -> dict[str, Any]:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in masked_attributes(attributes).items()}


class _OtelSpan:
    def __init__(self, span: trace.Span) -> None:
        self.span = span
        self._ended = False

    def set(self, attributes: Mapping[str, AttrValue | None]) -> None:
        if not self._ended:
            self.span.set_attributes(_clean(attributes))

    def rename(self, name: str) -> None:
        if not self._ended:
            self.span.update_name(masked_text(name))

    def end(self, *, error_type: str | None = None) -> None:
        if self._ended:
            return
        if error_type is not None:
            self.span.set_attribute(ERROR_TYPE, masked_text(error_type))
            self.span.set_status(Status(StatusCode.ERROR))
        self._ended = True
        self.span.end()


def run_root_context(run_id: str) -> Context:
    """The run's root as a remote parent (Node's admission span, wherever it was recorded)."""
    root = SpanContext(
        trace_id=int(run_trace_id(run_id), 16),
        span_id=int(run_root_span_id(run_id), 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(root))


class OtelTracer:
    def __init__(self, provider: trace.TracerProvider, *, capture_content: bool = False) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer(SCOPE)
        self._capture = capture_content

    @property
    def capture_content(self) -> bool:
        return self._capture

    def flush(self, timeout_ms: int = 30_000) -> bool:
        """Exports what is buffered (a process stopping calls it); ``True`` when everything left."""
        force = getattr(self._provider, "force_flush", None)
        return bool(force(timeout_ms)) if callable(force) else True

    def start(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> _OtelSpan:
        current = trace.get_current_span()
        parent = None
        if not current.get_span_context().is_valid and run is not None:
            parent = run_root_context(run.run_id)
        merged = {**(run.attributes() if run is not None else {}), **(attributes or {})}
        return _OtelSpan(self._tracer.start_span(masked_text(name), context=parent, kind=_KINDS[kind], attributes=_clean(merged)))

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> Iterator[_OtelSpan]:
        handle = self.start(name, kind=kind, run=run, attributes=attributes)
        with trace.use_span(handle.span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
            try:
                yield handle
            except BaseException as exc:
                handle.end(error_type=type(exc).__name__)
                raise
            finally:
                handle.end()

    def traceparent(self) -> str | None:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}"


__all__ = ["SCOPE", "OtelTracer", "run_root_context"]
