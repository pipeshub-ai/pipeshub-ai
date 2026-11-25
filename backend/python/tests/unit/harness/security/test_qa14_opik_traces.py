"""QA14-7 (OBS-08, Opik): what the Opik SDK is handed carries no token, whatever the prompt, call or result held.

Opik traces carry content by the operator's choice (prompts, tool arguments and results); a credential is not
content. The SDK's span and trace entry points are recorded in process (``opik_spans_recorded``): nothing is sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.kernel.core.context import RunContext
from harness_core.kernel.core.messages import AssistantMessage, TextPart, UserMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.types import Goal
from harness_core.models.transports.opik_tracing import (
    OpikTracingTransport,
    maybe_start_named_span,
    maybe_start_run_trace,
    maybe_start_tool_span,
    record_named_span_output,
    record_tool_span_output,
)
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.canary import Canaries, Surface, assert_no_canary
from harness_core.tools.base import ToolOutput
from tests.support.opik_client import opik_spans_recorded
from tests.support.token_vector import EXAMPLES


class _Transport:
    def __init__(self, answer: str) -> None:
        self._answer = answer

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def accepts_output_limit(self) -> bool:
        return False

    async def complete(self, **_: Any) -> ModelResponse:  # noqa: ANN401
        return ModelResponse(
            message=AssistantMessage(content=[TextPart(text=self._answer)]),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason=StopReason.END_TURN,
            model="fake-model",
        )


@pytest.mark.parametrize(("name", "example"), [(n, e) for n, e, _ in EXAMPLES], ids=[n for n, _, _ in EXAMPLES])
async def test_qa14_7_opik_receives_no_token(name: str, example: str) -> None:
    del name
    text = f"the call failed: {example}"
    with opik_spans_recorded() as recorded:
        with maybe_start_run_trace(enabled=True, run_ctx=RunContext(role_name="a", model="m"), goal=Goal(description=text)):
            with maybe_start_tool_span(enabled=True, name="jira_create", arguments={"title": text}) as span:  # pyright: ignore[reportUnknownVariableType]
                record_tool_span_output(span, result=ToolOutput(success=False, error=text, data={"echo": text}))
            with maybe_start_named_span(enabled=True, name="step", span_input={"q": text}) as span:  # pyright: ignore[reportUnknownVariableType]
                record_named_span_output(span, text)
        transport = OpikTracingTransport(_Transport(answer=text))  # type: ignore[arg-type]
        _ = await transport.complete(messages=[UserMessage(content=[TextPart(text=text)])], system=text)

    assert len(recorded) == 4
    assert_no_canary([Surface("Opik traces", recorded, owner_view=True)], Canaries(secrets=(example,)))
    assert SECRET_MASK in str(recorded[1].span.output), "the failure is still there to read, masked"
