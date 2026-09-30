"""Which provider errors the LLM client retries."""

from __future__ import annotations

import litellm
import pytest

from benchmarks.harness.llm.client import _is_retryable_llm_error


def _error(cls: type[Exception], status: int) -> Exception:
    try:
        return cls(message="x", llm_provider="anthropic", model="m")
    except TypeError:
        return cls(status_code=status, message="x", llm_provider="anthropic", model="m")


@pytest.mark.parametrize(("cls", "status"), [
    (litellm.BadGatewayError, 502),  # a CDN blip once ended a stage hours in
    (litellm.ServiceUnavailableError, 503),
    (litellm.InternalServerError, 500),
    (litellm.RateLimitError, 429),
])
def test_transient_provider_errors_are_retried(cls: type[Exception], status: int) -> None:
    assert _is_retryable_llm_error(_error(cls, status))


def test_a_rejected_request_is_not_retried() -> None:
    assert not _is_retryable_llm_error(_error(litellm.BadRequestError, 400))
