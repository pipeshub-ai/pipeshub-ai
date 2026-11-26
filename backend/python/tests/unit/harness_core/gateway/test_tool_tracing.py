"""P9 OT-4, OT-8, OT-9 for tool calls, and the gateway's request log line (``gateway.call``) the auditor reads."""

from __future__ import annotations

import logging

import pytest

from harness_core.telemetry.ids import run_trace_id
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    SECRET,
    build_endpoint,
    claims_for,
)
from harness_core.testing.telemetry import otel_in_memory, recording

ADAPTERS = {"recording": recording, "otel": otel_in_memory}
SENTINEL = "SENTINEL-tool-args-p9"


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot4_an_executed_call_is_one_execute_tool_span(adapter: str) -> None:
    tracer, finished = ADAPTERS[adapter]()
    world = build_endpoint(tracer=tracer)
    answer = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": SENTINEL})
    (span,) = finished()
    assert (span.name, span.kind) == ("execute_tool lookup", "internal")
    attrs = span.attributes
    assert attrs["gen_ai.operation.name"] == "execute_tool"
    assert attrs["gen_ai.tool.name"] == "lookup" and attrs["gen_ai.tool.type"] == "function"
    assert attrs["gen_ai.tool.call.id"] == answer.invocation_id
    assert attrs["pipeshub.disposition"] == "executed"
    assert attrs["pipeshub.run_id"] == BINDING.run_id and attrs["pipeshub.attempt_id"] == BINDING.attempt_id
    assert span.trace_id == run_trace_id(BINDING.run_id)
    assert span.error_type is None
    assert SENTINEL not in repr(span), "tool arguments are content: not recorded by default"


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot4_refused_calls_carry_their_reason_code(adapter: str) -> None:
    tracer, finished = ADAPTERS[adapter]()
    world = build_endpoint(tracer=tracer)
    _ = await world.endpoint.call_tool(claims_for(BINDING), "ghost", {"q": "x"})
    _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": SECRET})
    _ = await world.endpoint.call_tool(claims_for(BINDING), "reject", {"q": "x"})
    assert [s.error_type for s in finished()] == ["not_granted", "forbidden_arguments", "tool_error"]
    assert SECRET not in repr(finished())


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot9_tool_arguments_and_result_when_the_operator_opts_in(adapter: str) -> None:
    tracer, finished = ADAPTERS[adapter](capture_content=True)
    world = build_endpoint(tracer=tracer)
    _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": SENTINEL})
    (span,) = finished()
    assert SENTINEL in str(span.attributes["gen_ai.tool.call.arguments"])
    assert SENTINEL in str(span.attributes["gen_ai.tool.call.result"])


async def test_every_call_leaves_one_gateway_call_line_with_its_tool_id(caplog: pytest.LogCaptureFixture) -> None:
    world = build_endpoint()
    with caplog.at_level(logging.INFO):
        _ = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": SENTINEL})
        _ = await world.endpoint.call_tool(claims_for(BINDING), "ghost", {"q": "x"})
    lines = [r.__dict__["harness"] for r in caplog.records if r.__dict__.get("harness_event") == "gateway.call"]
    assert [(f["toolId"], f["disposition"]) for f in lines] == [
        ("crm@inst-1/lookup", "executed"),
        ("ungranted@default/ghost", "refused"),
    ]
    for fields in lines:
        assert (fields["orgId"], fields["runId"], fields["attemptId"]) == ("org-1", "run-1", "run-1.a2")
    assert SENTINEL not in caplog.text
