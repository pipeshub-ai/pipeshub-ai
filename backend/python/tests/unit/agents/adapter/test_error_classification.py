"""`classify_error` (`app/agents/agent_loop/error_classification.py`) — maps
raw agent/transport error strings to a stable `errorCode` + friendly
message, shared by `RespondPipeline` and `stream_bridge.py`."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.error_classification import classify_error


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (
            "LLM call failed: LangChain transport error (complete): "
            "Error code: 429 - {'error': {'message': 'rate limit exceeded'}}",
            "rate_limit",
        ),
        ("Too many requests, please slow down", "rate_limit"),
        ("401 Unauthorized: invalid api key", "auth_error"),
        ("Error code: 403 - permission denied", "auth_error"),
        ("Error code: 503 - Service Unavailable", "server_error"),
        ("upstream returned 502 bad gateway", "server_error"),
        ("Request timed out after 30s", "timeout"),
        ("connection timeout while calling provider", "timeout"),
        ("something totally unexpected happened", "unknown"),
    ],
)
def test_classify_error_returns_expected_code(raw: str, expected_code: str) -> None:
    error_code, _ = classify_error(raw)
    assert error_code == expected_code


def test_classify_error_user_message_never_leaks_raw_provider_text() -> None:
    """Regression guard for the original bug report — a raw 429 body must
    not be echoed back to the user verbatim."""
    raw = (
        "LLM call failed: LangChain transport error (complete): Error code: "
        "429 - {'error': {'message': 'Your requests to gpt-5.4 have exceeded rate limit.'}}"
    )
    _, message = classify_error(raw)
    assert "gpt-5.4" not in message
    assert "exceeded rate limit" not in message


def test_classify_error_is_case_insensitive() -> None:
    error_code, _ = classify_error("RATE LIMIT EXCEEDED")
    assert error_code == "rate_limit"


def test_classify_error_prioritizes_rate_limit_over_server_error_hints() -> None:
    """A 429 body that also happens to mention "503" in passing text should
    still classify as rate_limit — checked first since it's the most
    actionable code for the user (retry shortly vs. wait indefinitely)."""
    error_code, _ = classify_error("429 too many requests (peer also saw a 503 earlier)")
    assert error_code == "rate_limit"
