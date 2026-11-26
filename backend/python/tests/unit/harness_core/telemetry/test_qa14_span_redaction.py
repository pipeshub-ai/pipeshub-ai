"""QA14-4 (OBS-08): no exported span carries a token, whatever attribute or name a caller puts it in.

Content capture is an operator's opt-in to see prompts and tool payloads in traces, not to ship credentials to a
trace backend; both tracers mask through the shared token vector before a span leaves them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.security.token_patterns import SECRET_MASK
from harness_core.telemetry import RunRef
from harness_core.testing.canary import Canaries, Surface, assert_no_canary
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.telemetry import otel_in_memory, recording
from tests.support.token_vector import EXAMPLES

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.memory import FinishedSpan
    from harness_core.telemetry.ports import HarnessTracer

    TracerEnv = Callable[..., tuple[HarnessTracer, Callable[[], list[FinishedSpan]]]]

RUN = RunRef(org_id="org-1", run_id="run-1")
TRACERS: dict[str, TracerEnv] = {"recording": recording, "otel": otel_in_memory}


@pytest.mark.parametrize("tracer_name", sorted(TRACERS))
@pytest.mark.parametrize(("name", "example"), [(n, e) for n, e, _ in EXAMPLES], ids=[n for n, _, _ in EXAMPLES])
def test_qa14_4_span_attributes_and_names_are_masked(tracer_name: str, name: str, example: str) -> None:
    del name
    tracer, finished = TRACERS[tracer_name](capture_content=True)
    text = f"call failed: {example} (retry)"
    with tracer.span(f"execute_tool {text}", kind="internal", run=RUN, attributes={"start": text}) as span:
        span.set({"gen_ai.tool.call.arguments": text, "listed": ("ok", text)})
        span.rename(f"execute_tool {text}")
    started = tracer.start("chat", kind="client", attributes={"x": 1})
    started.end(error_type=text)
    spans = finished()
    assert_no_canary([Surface("spans", spans)], Canaries(secrets=(example,)))
    (tool_span,) = [s for s in spans if s.name.startswith("execute_tool")]
    assert SECRET_MASK in str(tool_span.attributes["gen_ai.tool.call.arguments"])


@pytest.mark.parametrize("tracer_name", sorted(TRACERS))
@pytest.mark.parametrize(("name", "example"), [(n, e) for n, e, _ in EXAMPLES], ids=[n for n, _, _ in EXAMPLES])
async def test_qa14_4_the_tool_gateways_captured_arguments_and_result_are_masked(
    tracer_name: str, name: str, example: str
) -> None:
    """A forbidden token is refused after its arguments were captured; a redact-only one runs and comes back."""
    del name
    tracer, finished = TRACERS[tracer_name](capture_content=True)
    world = build_endpoint(tracer=tracer)
    _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": f"use {example} now"})
    (span,) = finished()
    assert "gen_ai.tool.call.arguments" in span.attributes
    assert_no_canary([Surface("tool span", span)], Canaries(secrets=(example,)))


@pytest.mark.parametrize("tracer_name", sorted(TRACERS))
async def test_qa14_11_a_secret_named_argument_is_masked_in_the_captured_arguments(tracer_name: str) -> None:
    tracer, finished = TRACERS[tracer_name](capture_content=True)
    world = build_endpoint(tracer=tracer)
    _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x", "password": "hunter2"})
    (span,) = finished()
    captured = json.loads(str(span.attributes["gen_ai.tool.call.arguments"]))
    assert captured == {"q": "x", "password": SECRET_MASK}
