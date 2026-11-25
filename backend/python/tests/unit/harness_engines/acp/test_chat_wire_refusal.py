"""ACP-CW: a chat-completions refusal names the gateway's code in its message, as the Responses and Messages wires
do, so an agent that passes on only the message (opencode's ACP server) still stops the run for that reason."""

from __future__ import annotations

import pytest

from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_engines.acp.mapper import error_code


@pytest.mark.parametrize(("status", "code"), [(402, "budget_insufficient"), (402, "budget_exhausted"), (403, "model_not_granted")])
def test_acp_cw1_the_code_travels_in_the_message_and_the_envelope(status: int, code: str) -> None:
    body, headers = CHAT_WIRE.error(status, code, "less token budget is left than the call needs")
    error = body["error"]  # type: ignore[index]
    assert error["code"] == code  # type: ignore[index]
    assert error["message"] == f"less token budget is left than the call needs [gateway:{code}]"  # type: ignore[index]
    assert headers == {}
    # What opencode's ACP server answers session/prompt with, as the ACP driver reads it.
    assert error_code(f"Internal error: {error['message']}", {"errorName": "APIError"}) == code  # type: ignore[index]


def test_acp_cw2_a_code_that_is_not_one_is_not_tagged() -> None:
    body, _ = CHAT_WIRE.error(500, "Not A Code!", "boom")
    assert body["error"]["message"] == "boom"  # type: ignore[index]
