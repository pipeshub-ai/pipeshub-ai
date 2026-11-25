"""WP-61 (C6.12): every engine ends its run with ``egress_volume_exceeded`` when the run's egress is
refused by the cap.

Past the cap the egress proxy marks the run's Model Gateway calls and the gateway refuses each one (403) in the
engine's own wire, with the code in the error and tagged in its message. Each engine passes the refusal on in its own
way; its mapper reads the code from what the engine kept (the shapes the other gateway refusals take,
``account_not_owned`` and the budget codes), and the kernel fails the run with it (``test_wp61_egress_volume_run_end``).

R11D-EVE-1 Claude Code (Messages wire): the code from the result text Claude Code reports.
R11D-EVE-2 Codex (Responses wire): the code from the message Codex keeps of a 403.
R11D-EVE-3 opencode (Chat Completions wire): the code from the response body's ``error.code``.
R11D-EVE-4 ACP agents, Gemini CLI (Gemini wire) and opencode's ACP server (Chat Completions wire): the code from the
           message the agent passes on.
"""

from __future__ import annotations

import json

from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.errors import EgressVolumeExceeded
from harness_core.gateway.gemini_wire import GEMINI_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.responses_wire import RESPONSES_WIRE
from harness_engines.acp.mapper import error_code as acp_error_code
from harness_engines.claude_code.mapper import error_code as claude_error_code
from harness_engines.codex.mapper import error_code as codex_error_code
from harness_engines.opencode.mapper import error_code as opencode_error_code

REFUSED = EgressVolumeExceeded()
CODE = REFUSED.reason_code
STATUS = REFUSED.http_status


def _body(wire: object) -> dict[str, object]:
    body, _ = wire.error(STATUS, CODE, str(REFUSED))  # type: ignore[attr-defined]
    return body  # type: ignore[no-any-return]


def _message(body: dict[str, object]) -> str:
    error = body["error"]
    assert isinstance(error, dict)
    return str(error["message"])


def test_r11d_eve_1_claude_code_ends_with_the_code() -> None:
    text = f"API Error: {STATUS} {json.dumps(_body(MESSAGES_WIRE))}"
    assert claude_error_code({"subtype": "success", "is_error": True, "api_error_status": STATUS, "result": text}) == CODE


def test_r11d_eve_2_codex_ends_with_the_code() -> None:
    message = (
        f"unexpected status {STATUS} Forbidden: {_message(_body(RESPONSES_WIRE))}, "
        "url: http://model-gateway:8097/v1/responses"
    )
    info = {"httpConnectionFailed": {"httpStatusCode": STATUS}}
    assert codex_error_code({"message": message, "codexErrorInfo": info}) == CODE


def test_r11d_eve_3_opencode_ends_with_the_code() -> None:
    props = {
        "error": {
            "name": "APIError",
            "data": {"message": "Forbidden", "statusCode": STATUS, "responseBody": json.dumps(_body(CHAT_WIRE))},
        }
    }
    assert opencode_error_code(props) == CODE


def test_r11d_eve_4_acp_agents_end_with_the_code() -> None:
    gemini = _body(GEMINI_WIRE)
    assert acp_error_code(_message(gemini), None) == CODE
    assert acp_error_code("Internal error", {"details": _message(gemini)}) == CODE
    assert acp_error_code(f"Internal error: {_message(_body(CHAT_WIRE))}", None) == CODE
