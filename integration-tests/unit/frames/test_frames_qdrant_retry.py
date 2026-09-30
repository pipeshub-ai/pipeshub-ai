"""Shared-Qdrant queries ride out a transient failure instead of ending a run."""

from __future__ import annotations

from typing import Any

import pytest
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from benchmarks.harness.systems.rag import retrieval


class _Client:
    def __init__(self, failures: list[BaseException]) -> None:
        self.failures = failures
        self.calls = 0

    def query_points(self, **_kwargs: Any) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "points"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval._query_with_retry.retry, "sleep", lambda _s: None)


def test_a_timeout_is_retried() -> None:
    client = _Client([ResponseHandlingException(TimeoutError("read timed out"))])
    assert retrieval._query_with_retry(client) == "points"
    assert client.calls == 2


def test_a_bad_request_is_not_retried() -> None:
    client = _Client([UnexpectedResponse(400, "Bad Request", b"", {})])
    with pytest.raises(UnexpectedResponse):
        retrieval._query_with_retry(client)
    assert client.calls == 1


def test_gives_up_after_a_bounded_number_of_attempts() -> None:
    client = _Client([ResponseHandlingException(TimeoutError("t")) for _ in range(10)])
    with pytest.raises(ResponseHandlingException):
        retrieval._query_with_retry(client)
    assert client.calls == retrieval._QDRANT_ATTEMPTS
