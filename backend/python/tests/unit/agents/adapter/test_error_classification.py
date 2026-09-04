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
        (
            # Real Azure OpenAI prompt-shield rejection body (400 with
            # code=content_filter / ResponsibleAIPolicyViolation).
            "LangChain transport error (stream): Error code: 400 - {'error': "
            "{'message': \"The response was filtered due to the prompt triggering "
            "Azure OpenAI's content management policy. Please modify your prompt "
            "and retry.\", 'code': 'content_filter', 'status': 400, 'innererror': "
            "{'code': 'ResponsibleAIPolicyViolation', 'content_filter_result': "
            "{'jailbreak': {'detected': True, 'filtered': True}}}}}",
            "content_filter",
        ),
        ("openai content_policy_violation: request rejected", "content_filter"),
        (
            "LangChain transport error (stream): Error code: 400 - "
            "{'error': {'message': 'invalid Qwen3.8 reasoning_effort', "
            "'type': 'invalid_request_error'}}",
            "invalid_request",
        ),
        ("Error code: 400 - {'error': {'message': 'bad schema'}}", "invalid_request"),
        (
            "LangChain transport error (stream): Error code: 413 - "
            "{'error': {'message': 'Request too large for model'}}",
            "request_too_large",
        ),
        ("context length exceeded: 128000 max, got 150000", "request_too_large"),
        ("maximum context length is 4096 tokens", "request_too_large"),
    ],
)
def test_classify_error_returns_expected_code(raw: str, expected_code: str) -> None:
    error_code, _ = classify_error(raw)
    assert error_code == expected_code


def test_content_filter_wins_over_status_code_hints() -> None:
    """An Azure content-filter body carries `'status': 400` and often other
    numeric fragments — it must still classify as content_filter (telling
    the user "try again" for a deterministic rejection is actively wrong)."""
    raw = "Error code: 400 - content management policy violation (429 mentioned in passing)"
    error_code, message = classify_error(raw)
    assert error_code == "content_filter"
    assert "rephrase" in message.lower() or "content filter" in message.lower()


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


def test_invalid_request_surfaces_provider_message() -> None:
    """A 400 invalid_request must show the provider's own message — retrying
    the same request cannot succeed, unlike rate-limit/5xx. Regression for
    Groq Qwen3.8 rejecting an unsupported reasoning_effort value while the
    UI only showed the generic 'unknown' apology."""
    raw = (
        "LangChain transport error (stream): Error code: 400 - "
        "{'error': {'message': 'invalid Qwen3.8 reasoning_effort', "
        "'type': 'invalid_request_error'}}"
    )
    error_code, message = classify_error(raw)
    assert error_code == "invalid_request"
    assert "invalid Qwen3.8 reasoning_effort" in message
    assert "LangChain transport error" not in message
    assert "invalid_request_error" not in message


def test_invalid_request_without_extractable_message_uses_canned_text() -> None:
    error_code, message = classify_error("Error code: 400 - malformed payload")
    assert error_code == "invalid_request"
    assert message == (
        "The AI service rejected this request. Please check the model configuration "
        "and try again."
    )


# -- request_too_large (413 / context-length) --


def test_request_too_large_wins_over_rate_limit_hints() -> None:
    """Groq returns 413 with `code: rate_limit_exceeded` and "rate limit" in
    the body.  That must classify as request_too_large — "try again in a
    moment" is wrong advice for a permanently oversized request."""
    raw = (
        "LangChain transport error (stream): Error code: 413 - "
        "{'error': {'message': 'Request too large for model `qwen/qwen3.8-27b` "
        "in organization `org_01jvkxz4twekr9rrb94kdf9qpk` service tier `on_demand` "
        "on input tokens per minute (ITPM): Limit 7000, Requested 13657, please "
        "reduce your message size and try again. Need more tokens? Upgrade to Dev "
        "Tier today at https://console.groq.com/settings/billing', "
        "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )
    error_code, message = classify_error(raw)
    assert error_code == "request_too_large"
    assert "Limit 7000" in message
    assert "rate limited" not in message.lower()


def test_request_too_large_surfaces_provider_message() -> None:
    raw = (
        "LangChain transport error (stream): Error code: 413 - "
        "{'error': {'message': 'Request too large for model `qwen/qwen3.8-27b`', "
        "'type': 'tokens'}}"
    )
    error_code, message = classify_error(raw)
    assert error_code == "request_too_large"
    assert "Request too large" in message
    assert "LangChain transport error" not in message


def test_request_too_large_context_length_exceeded() -> None:
    """OpenAI-style `context length exceeded` error."""
    raw = (
        "Error code: 400 - {'error': {'message': "
        "'This model\\'s maximum context length is 128000 tokens. "
        "However, your messages resulted in 150000 tokens.', "
        "'type': 'invalid_request_error', 'code': 'context_length_exceeded'}}"
    )
    error_code, message = classify_error(raw)
    assert error_code == "request_too_large"
    assert "128000" in message


def test_request_too_large_without_extractable_message_uses_canned_text() -> None:
    raw = "something about payload too large without structured body"
    error_code, message = classify_error(raw)
    assert error_code == "request_too_large"
    assert message == (
        "The request exceeds the model's token limit. Please shorten your message "
        "or start a new conversation to reduce context size."
    )
