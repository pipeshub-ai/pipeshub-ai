"""WP77-MG-1..3: a provider failure's log line says what the provider answered.

The two calls that ended the engine child logged only ``status=provider_unavailable released=True``: no upstream
status, no provider error code, no exception type, no phase. Whether Azure had answered a 5xx, dropped the
connection or failed the stream after a 200 could not be told afterwards. The line now carries the upstream HTTP
status, the provider's own error code (a short identifier, never its message or body), the exception's type and
whether the call failed opening or mid-stream.
"""

from __future__ import annotations

import logging

import pytest

from harness_core.gateway.model_gateway import ProviderCallFailed
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.models.transports.openai import provider_error_code
from tests.unit.harness_core.gateway.test_model_gateway import (  # noqa: F401 - the fixture
    GatewayRig,
    body,
    chat,
    rig,
)


def _failed_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "model_gateway.call" in r.getMessage() and "status=ok" not in r.getMessage()]


@pytest.mark.parametrize("streaming", [False, True])
async def test_wp77_mg_1_an_http_failure_logs_its_status_and_type(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture, streaming: bool  # noqa: F811
) -> None:
    caplog.set_level(logging.INFO)
    rig.transport.fail = TransportError("Error code: 503 - secret body sk-abc", status_code=503)
    with pytest.raises(ProviderCallFailed):
        await chat(rig, rig.token(rig.pin()), body(stream=streaming))
    (line,) = _failed_lines(caplog)
    for field in ("status=provider_unavailable", "upstreamStatus=503", "errorType=TransportError", "phase=open"):
        assert field in line, line
    assert "sk-abc" not in caplog.text and "secret body" not in caplog.text


async def test_wp77_mg_2_a_stream_failed_after_a_200_logs_the_providers_code(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture  # noqa: F811
) -> None:
    caplog.set_level(logging.INFO)
    rig.transport.fail = TransportError(
        "OpenAI API error (stream): An error occurred while processing your request.", provider_code="server_error"
    )
    with pytest.raises(ProviderCallFailed):
        await chat(rig, rig.token(rig.pin()), body(stream=True))
    (line,) = _failed_lines(caplog)
    for field in ("upstreamCode=server_error", "errorType=TransportError", "phase=open"):
        assert field in line, line
    assert "upstreamStatus" not in line, "no status was answered: none is made up"
    assert "processing your request" not in caplog.text


async def test_wp77_mg_2b_a_failure_after_output_is_logged_mid_stream(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture  # noqa: F811
) -> None:
    caplog.set_level(logging.INFO)
    rig.transport.events = [TextDeltaEvent(delta="a"), TextDeltaEvent(delta="b")]
    rig.transport.fail = TransportError("stream died", provider_code="server_error")
    rig.transport.fail_after = 1
    outcome = await chat(rig, rig.token(rig.pin()), body(stream=True))
    chunks = [chunk async for chunk in outcome.chunks]  # type: ignore[union-attr]
    assert chunks
    lines = [r.getMessage() for r in caplog.records if "model_gateway.provider_failed" in r.getMessage()]
    assert len(lines) == 1 and "phase=stream" in lines[0] and "upstreamCode=server_error" in lines[0], lines


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ({"code": "server_error", "message": "An error occurred"}, "server_error"),
        ({"type": "rate_limit_exceeded"}, "rate_limit_exceeded"),
        ({"code": "a b<script>"}, None),
        ("not a mapping", None),
        ({"code": "x" * 200}, None),
    ],
)
def test_wp77_mg_3_only_a_short_identifier_is_kept_of_the_providers_error(error: object, code: str | None) -> None:
    assert provider_error_code(error) == code
