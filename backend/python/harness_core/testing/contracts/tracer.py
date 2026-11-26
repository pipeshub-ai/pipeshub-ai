"""Contract suite for ``HarnessTracer`` (TR-*): provide ``tracer_env(capture_content)`` returning the tracer and a
reader of its ended spans as ``FinishedSpan``s (``harness_core.testing.telemetry``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.telemetry.ports import RunRef

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.memory import FinishedSpan
    from harness_core.telemetry.ports import HarnessTracer

RUN = RunRef(org_id="org-1", run_id="run-1", session_id="ses-1", attempt_id="att-1", fence=3)


class HarnessTracerContract:
    def tracer_env(self, *, capture_content: bool = False) -> tuple[HarnessTracer, Callable[[], list[FinishedSpan]]]:
        raise NotImplementedError

    def test_tr1_a_span_carries_its_name_kind_and_the_runs_correlation(self) -> None:
        tracer, finished = self.tracer_env()
        with tracer.span("execute_tool lookup", kind="internal", run=RUN, attributes={"gen_ai.tool.name": "lookup"}):
            pass
        (span,) = finished()
        assert (span.name, span.kind) == ("execute_tool lookup", "internal")
        assert span.attributes["gen_ai.tool.name"] == "lookup"
        assert span.attributes["pipeshub.run_id"] == "run-1"
        assert span.attributes["pipeshub.org_id"] == "org-1"
        assert span.attributes["pipeshub.session_id"] == "ses-1"
        assert span.attributes["pipeshub.attempt_id"] == "att-1"
        assert span.attributes["pipeshub.fence"] == 3
        assert span.error_type is None

    def test_tr2_without_a_current_span_the_parent_is_the_runs_root(self) -> None:
        tracer, finished = self.tracer_env()
        tracer.start("chat m", kind="client", run=RUN).end()
        (span,) = finished()
        assert span.trace_id == run_trace_id("run-1")
        assert span.parent_id == run_root_span_id("run-1")

    def test_tr3_a_span_inside_another_is_its_child(self) -> None:
        tracer, finished = self.tracer_env()
        with tracer.span("invoke_agent opencode", kind="internal", run=RUN):
            tracer.start("pipeshub.sandbox.acquire", kind="client", run=RUN).end()
        inner, outer = sorted(finished(), key=lambda s: s.name != "pipeshub.sandbox.acquire")
        assert inner.parent_id == outer.span_id
        assert inner.trace_id == outer.trace_id == run_trace_id("run-1")

    def test_tr4_an_exception_marks_the_span_and_propagates(self) -> None:
        tracer, finished = self.tracer_env()
        with pytest.raises(KeyError), tracer.span("x", kind="internal", run=RUN):
            raise KeyError("boom")
        (span,) = finished()
        assert span.error_type == "KeyError"
        assert span.attributes["error.type"] == "KeyError"

    def test_tr5_a_reason_code_wins_and_a_span_ends_once(self) -> None:
        tracer, finished = self.tracer_env()
        with pytest.raises(RuntimeError), tracer.span("x", kind="internal") as span:
            span.end(error_type="not_granted")
            span.set({"late": "ignored"})
            raise RuntimeError
        (only,) = finished()
        assert only.error_type == "not_granted"
        assert "late" not in only.attributes

    def test_tr6_rename_and_none_attributes(self) -> None:
        tracer, finished = self.tracer_env()
        span = tracer.start("chat", kind="client", attributes={"a": None, "b": 1})
        span.rename("chat gpt-x")
        span.set({"c": None, "d": True})
        span.end()
        (only,) = finished()
        assert only.name == "chat gpt-x"
        assert "a" not in only.attributes and "c" not in only.attributes
        assert only.attributes["b"] == 1 and only.attributes["d"] is True

    def test_tr7_start_does_not_make_the_span_current(self) -> None:
        tracer, finished = self.tracer_env()
        held = tracer.start("held", kind="client", run=RUN)
        tracer.start("after", kind="client", run=RUN).end()
        held.end()
        after = next(s for s in finished() if s.name == "after")
        assert after.parent_id == run_root_span_id("run-1")

    def test_tr8_capture_content_is_the_operators_choice(self) -> None:
        assert self.tracer_env()[0].capture_content is False
        assert self.tracer_env(capture_content=True)[0].capture_content is True

    def test_tr9_ob01_traceparent_names_the_current_span(self) -> None:
        """What a child process (an engine in its sandbox) is handed to join the trace: W3C ``traceparent``."""
        tracer, finished = self.tracer_env()
        assert tracer.traceparent() is None
        with tracer.span("invoke_agent opencode", kind="internal", run=RUN):
            carried = tracer.traceparent()
        assert tracer.traceparent() is None
        (span,) = finished()
        assert carried == f"00-{span.trace_id}-{span.span_id}-01"


__all__ = ["RUN", "HarnessTracerContract"]
