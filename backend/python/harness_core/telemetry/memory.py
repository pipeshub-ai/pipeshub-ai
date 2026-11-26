"""Tracers without an exporter: ``NoopTracer`` (nothing recorded) and ``RecordingTracer`` (spans kept in memory)."""

from __future__ import annotations

import contextlib
import contextvars
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.telemetry.genai import ERROR_TYPE
from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.telemetry.masking import masked_attributes, masked_text

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from harness_core.telemetry.ports import AttrValue, RunRef, SpanKind


class _NoopSpan:
    def set(self, attributes: Mapping[str, AttrValue | None]) -> None:
        del attributes

    def rename(self, name: str) -> None:
        del name

    def end(self, *, error_type: str | None = None) -> None:
        del error_type


_NOOP_SPAN = _NoopSpan()


class NoopTracer:
    """What a deployment with tracing off binds: every call does nothing and allocates nothing per span."""

    @property
    def capture_content(self) -> bool:
        return False

    def start(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> _NoopSpan:
        del name, kind, run, attributes
        return _NOOP_SPAN

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> Iterator[_NoopSpan]:
        del name, kind, run, attributes
        yield _NOOP_SPAN

    def traceparent(self) -> str | None:
        return None


NOOP_TRACER = NoopTracer()


@dataclass
class FinishedSpan:
    """A span as an exporter sees it; what contract suites compare across adapters."""

    name: str
    kind: str
    trace_id: str
    span_id: str
    parent_id: str | None
    attributes: dict[str, AttrValue] = field(default_factory=dict[str, "AttrValue"])
    error_type: str | None = None
    ended: bool = False


@dataclass
class _RecordingSpan:
    tracer: RecordingTracer
    record: FinishedSpan

    def set(self, attributes: Mapping[str, AttrValue | None]) -> None:
        if not self.record.ended:
            self.record.attributes.update(masked_attributes(attributes))

    def rename(self, name: str) -> None:
        if not self.record.ended:
            self.record.name = masked_text(name)

    def end(self, *, error_type: str | None = None) -> None:
        if self.record.ended:
            return
        if error_type is not None:
            error_type = masked_text(error_type)
            self.record.error_type = error_type
            self.record.attributes[ERROR_TYPE] = error_type
        self.record.ended = True
        self.tracer.finished.append(self.record)


class RecordingTracer:
    """Keeps every ended span in ``finished``; parents follow the current span, else the run's root."""

    def __init__(self, *, capture_content: bool = False) -> None:
        self._capture = capture_content
        self._ids = itertools.count(1)
        self._current: contextvars.ContextVar[FinishedSpan | None] = contextvars.ContextVar(
            f"recording-span-{id(self)}", default=None
        )
        self.finished: list[FinishedSpan] = []

    @property
    def capture_content(self) -> bool:
        return self._capture

    def start(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> _RecordingSpan:
        parent = self._current.get()
        if parent is not None:
            trace_id, parent_id = parent.trace_id, parent.span_id
        elif run is not None:
            trace_id, parent_id = run_trace_id(run.run_id), run_root_span_id(run.run_id)
        else:
            trace_id, parent_id = f"{next(self._ids):032x}", None
        record = FinishedSpan(masked_text(name), kind, trace_id, f"{next(self._ids):016x}", parent_id)
        span = _RecordingSpan(self, record)
        span.set({**(run.attributes() if run is not None else {}), **(attributes or {})})
        return span

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> Iterator[_RecordingSpan]:
        span = self.start(name, kind=kind, run=run, attributes=attributes)
        token = self._current.set(span.record)
        try:
            yield span
        except BaseException as exc:
            span.end(error_type=type(exc).__name__)
            raise
        finally:
            self._current.reset(token)
            span.end()

    def traceparent(self) -> str | None:
        current = self._current.get()
        return None if current is None else f"00-{current.trace_id}-{current.span_id}-01"


__all__ = ["NOOP_TRACER", "FinishedSpan", "NoopTracer", "RecordingTracer"]
