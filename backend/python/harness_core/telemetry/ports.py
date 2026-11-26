"""The tracing port (P9): spans for runs, model calls, tool calls and sandbox acquires.

Product-neutral. Adapters: ``NoopTracer`` (the default, and what a switched-off deployment binds),
``RecordingTracer`` (in memory, for tests and embedders) and ``OtelTracer`` (OpenTelemetry SDK). Spans
follow the OpenTelemetry GenAI semantic conventions (``harness_core.telemetry.genai``); content attributes
are written only when ``capture_content`` is set, which an adapter takes from its operator's opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager

AttrValue = str | int | float | bool | tuple[str, ...]
SpanKind = Literal["internal", "client", "server"]


@dataclass(frozen=True, slots=True)
class RunRef:
    """The correlation of a span: the same ids the harness logs carry (``orgId``, ``sessionId``, ...)."""

    org_id: str
    run_id: str
    session_id: str | None = None
    attempt_id: str | None = None
    fence: int | None = None

    def attributes(self) -> dict[str, AttrValue]:
        found: dict[str, AttrValue | None] = {
            "pipeshub.org_id": self.org_id,
            "pipeshub.run_id": self.run_id,
            "pipeshub.session_id": self.session_id,
            "pipeshub.attempt_id": self.attempt_id,
            "pipeshub.fence": self.fence,
        }
        return {k: v for k, v in found.items() if v is not None}


class SpanHandle(Protocol):
    def set(self, attributes: Mapping[str, AttrValue | None]) -> None:
        """Adds attributes; ``None`` values are skipped."""
        ...

    def rename(self, name: str) -> None: ...

    def end(self, *, error_type: str | None = None) -> None:
        """Ends the span once (later calls do nothing); ``error_type`` marks it failed (``error.type``)."""
        ...


class HarnessTracer(Protocol):
    @property
    def capture_content(self) -> bool:
        """Whether prompts, answers and tool arguments may be recorded (the operator's opt-in; off by default)."""
        ...

    def start(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> SpanHandle:
        """A span the caller ends. Its parent is the span current in this context, else the run's root
        (derived from ``run.run_id``, so every process that knows the run joins its trace)."""
        ...

    def span(
        self,
        name: str,
        *,
        kind: SpanKind,
        run: RunRef | None = None,
        attributes: Mapping[str, AttrValue | None] | None = None,
    ) -> AbstractContextManager[SpanHandle]:
        """As ``start``, current for the block and ended at its end; an exception sets ``error.type`` to its
        type's name (unless the block already ended the span with a reason code) and propagates."""
        ...

    def traceparent(self) -> str | None:
        """The W3C ``traceparent`` of the span current in this context, for a process the harness starts (an engine
        in its sandbox) to join the trace; ``None`` outside a span, and from a tracer that records nothing."""
        ...


__all__ = ["AttrValue", "HarnessTracer", "RunRef", "SpanHandle", "SpanKind"]
