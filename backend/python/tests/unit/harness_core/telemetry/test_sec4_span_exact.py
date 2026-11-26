"""SEC4-8: spans made for a call carry the mask for a value its boundary resolved, in both tracers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.security.secrets import SecretSet, masking
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.telemetry import RunRef
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    secret_canary,
)
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.telemetry import otel_in_memory, recording

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.memory import FinishedSpan
    from harness_core.telemetry.ports import HarnessTracer

    TracerEnv = Callable[..., tuple[HarnessTracer, Callable[[], list[FinishedSpan]]]]

IDS = SeededIds(4088)
SECRET = secret_canary(IDS.uuid4)
RESOLVED = SecretSet.of({"client_secret": SECRET})
TRACERS: dict[str, TracerEnv] = {"recording": recording, "otel": otel_in_memory}


@pytest.mark.parametrize("tracer_name", sorted(TRACERS))
async def test_sec4_8_the_tool_gateways_span_masks_the_resolved_value_in_arguments_and_result(tracer_name: str) -> None:
    tracer, finished = TRACERS[tracer_name](capture_content=True)
    world = build_endpoint(tracer=tracer, secrets=RESOLVED)

    answer = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": f"why {SECRET}"})

    (span,) = finished()
    assert answer.text == f"lookup:why {SECRET_MASK}"
    assert json.loads(str(span.attributes["gen_ai.tool.call.arguments"])) == {"q": f"why {SECRET_MASK}"}
    assert_no_canary([Surface("tool span", span), Surface("engine answer", answer)], Canaries(secrets=(SECRET,)))


@pytest.mark.parametrize("tracer_name", sorted(TRACERS))
def test_sec4_8_a_span_a_tool_opens_inside_its_call_is_masked(tracer_name: str) -> None:
    tracer, finished = TRACERS[tracer_name](capture_content=True)
    with masking(RESOLVED), tracer.span(f"fetch {SECRET}", kind="client", run=RunRef("org-1", "run-1"), attributes={"q": SECRET}) as span:
        span.set({"response": f"401 for {SECRET}"})
        span.end(error_type=f"denied {SECRET}")

    spans = finished()
    assert_no_canary([Surface("spans", spans)], Canaries(secrets=(SECRET,)))
    assert SECRET_MASK in spans[0].name
