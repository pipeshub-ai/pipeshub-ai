"""is_provider_unavailable: an outage, not a bad request, across provider SDK shapes."""

import httpx
import pytest

from app.utils.llm import is_provider_unavailable


class _WithStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class APIConnectionError(Exception):
    """Named like openai/anthropic's, which carry no status code."""


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "unavailable"),
    [
        (ConnectionRefusedError("refused"), True),
        (TimeoutError(), True),
        (httpx.ConnectError("refused"), True),
        (httpx.ReadTimeout("slow"), True),
        (_WithStatus(503), True),
        (_WithStatus(429), True),
        (_WithStatus(400), False),
        (_WithStatus(401), False),
        (APIConnectionError(), True),
        (RateLimitError(), True),
        (ValueError("the output was not JSON"), False),
    ],
)
def test_classifies_provider_failures(exc: BaseException, unavailable: bool) -> None:
    assert is_provider_unavailable(exc) is unavailable


def test_follows_the_cause_chain() -> None:
    try:
        try:
            raise ConnectionRefusedError("refused")
        except ConnectionRefusedError as inner:
            raise RuntimeError("wrapped by the framework") from inner
    except RuntimeError as outer:
        assert is_provider_unavailable(outer)
