"""One bounded retry for a vector search on a transient store error."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_db import search_retry
from app.services.vector_db.models import SearchFailureKind
from app.services.vector_db.search_retry import (
    VectorSearchUnavailableError,
    classify_search_error,
    query_nearest_points_with_retry,
)
from app.telemetry.backend import METRICS_BACKEND

pytestmark = pytest.mark.asyncio

_LOGGER = logging.getLogger("test-search-retry")


class _TransientError(Exception):
    """What a provider's client raises when the store times out."""


def _service(side_effect: Any, *, kind: SearchFailureKind = SearchFailureKind.TIMEOUT, name: str = "qdrant") -> MagicMock:  # noqa: ANN401
    service = MagicMock()
    service.query_nearest_points = AsyncMock(side_effect=side_effect)
    service.transient_search_error = MagicMock(
        side_effect=lambda error: kind if isinstance(error, _TransientError) else None
    )
    service.get_service_name = MagicMock(return_value=name)
    return service


def _retries(provider: str, outcome: str) -> float:
    for line in METRICS_BACKEND.serialize().splitlines():
        if (
            line.startswith("pipeshub_vector_search_retries_total{")
            and f'provider="{provider}"' in line
            and f'outcome="{outcome}"' in line
        ):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


@pytest.fixture(autouse=True)
def _no_backoff() -> Iterator[None]:
    with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock):
        yield


async def test_a_transient_failure_then_success_returns_the_results_and_counts_the_retry(caplog) -> None:
    results = [[MagicMock()]]
    service = _service([_TransientError("Operation 'search' timed out"), results], name="p-recover")

    with caplog.at_level(logging.WARNING, logger=_LOGGER.name):
        got = await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    assert got is results
    assert service.query_nearest_points.await_count == 2
    assert _retries("p-recover", "recovered") == 1
    assert "recovered on retry" in caplog.text


async def test_a_non_transient_error_is_not_retried() -> None:
    error = ValueError("Wrong input: vector dimension error")
    service = _service(error, name="p-bad-request")

    with pytest.raises(ValueError) as raised:
        await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    assert raised.value is error
    assert service.query_nearest_points.await_count == 1
    assert _retries("p-bad-request", "failed") == 0


async def test_failing_both_attempts_raises_the_failure_kind() -> None:
    service = _service(
        [_TransientError("down"), _TransientError("still down")],
        kind=SearchFailureKind.UNAVAILABLE, name="p-fail",
    )

    with pytest.raises(VectorSearchUnavailableError) as raised:
        await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    assert raised.value.kind is SearchFailureKind.UNAVAILABLE
    assert raised.value.collection_name == "records"
    assert service.query_nearest_points.await_count == 2
    assert _retries("p-fail", "failed") == 1


async def test_a_transient_error_then_a_non_transient_one_raises_the_second() -> None:
    bug = RuntimeError("serialisation bug")
    service = _service([_TransientError("down"), bug])

    with pytest.raises(RuntimeError) as raised:
        await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    assert raised.value is bug


async def test_no_retry_when_the_first_attempt_used_up_the_budget() -> None:
    """A retry after a long timeout doubles a latency the user already paid."""
    service = _service(_TransientError("timed out after 44s"), name="p-budget")

    with pytest.raises(VectorSearchUnavailableError) as raised:
        await query_nearest_points_with_retry(
            service, "records", ["req"], logger=_LOGGER, budget_seconds=1.0,
        )

    assert raised.value.kind is SearchFailureKind.TIMEOUT
    assert service.query_nearest_points.await_count == 1
    assert _retries("p-budget", "skipped_budget") == 1


async def test_the_retry_is_cut_off_at_what_is_left_of_the_budget(monkeypatch) -> None:
    monkeypatch.setattr(search_retry, "MIN_RETRY_SECONDS", 0.0)
    calls = 0

    async def _first_fails_then_hangs(**_kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _TransientError("timed out")
        # Not asyncio.sleep: the fixture stubs it out to skip the backoff.
        await asyncio.Event().wait()

    service = _service(_first_fails_then_hangs)
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(VectorSearchUnavailableError) as raised:
        await query_nearest_points_with_retry(
            service, "records", ["req"], logger=_LOGGER, budget_seconds=0.5,
        )

    assert raised.value.kind is SearchFailureKind.TIMEOUT
    assert calls == 2
    assert loop.time() - started < 5


async def test_the_first_attempt_is_not_cut_off_by_the_budget() -> None:
    """The first attempt runs under the store client's own timeout; a slow
    search that answers is an answer, whatever the retry budget."""
    loop = asyncio.get_running_loop()
    results = [[MagicMock()]]

    async def _slow(**_kwargs: Any) -> list:  # noqa: ANN401
        answered = loop.create_future()
        # Not asyncio.sleep: the fixture stubs it out to skip the backoff.
        loop.call_later(0.2, answered.set_result, results)
        return await answered

    service = _service(_slow)

    got = await query_nearest_points_with_retry(
        service, "records", ["req"], logger=_LOGGER, budget_seconds=0.05,
    )

    assert got is results
    assert service.query_nearest_points.await_count == 1


async def test_builtin_timeout_and_connection_errors_are_transient_for_every_provider() -> None:
    service = MagicMock()
    service.transient_search_error = MagicMock(return_value=None)

    assert classify_search_error(service, asyncio.TimeoutError()) is SearchFailureKind.TIMEOUT
    assert classify_search_error(service, ConnectionResetError()) is SearchFailureKind.UNAVAILABLE
    assert classify_search_error(service, ValueError()) is None


async def test_a_classifier_answer_that_is_not_a_failure_kind_is_ignored() -> None:
    service = MagicMock()  # transient_search_error returns a MagicMock

    assert classify_search_error(service, RuntimeError("boom")) is None
