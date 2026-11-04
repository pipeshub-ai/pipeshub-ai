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


async def test_a_first_attempt_that_hangs_is_cut_off_with_room_left_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clients wait 300 s by default; waiting that out would leave no
    budget for the retry, so the first attempt stops early enough for one."""
    monkeypatch.setattr(search_retry, "MIN_RETRY_SECONDS", 0.2)
    loop = asyncio.get_running_loop()
    results = [[MagicMock()]]
    calls = 0

    async def _hangs_then_answers(**_kwargs: Any) -> list:  # noqa: ANN401
        nonlocal calls
        calls += 1
        if calls == 1:
            # Not asyncio.sleep: the fixture stubs it out to skip the backoff.
            await asyncio.Event().wait()
        return results

    service = _service(_hangs_then_answers, name="p-hang")
    started = loop.time()

    got = await query_nearest_points_with_retry(
        service, "records", ["req"], logger=_LOGGER, budget_seconds=1.0,
    )

    assert got is results
    assert calls == 2
    # Cut at the budget less the 0.45 s reserve (0.2 s retry + 0.25 s delay).
    assert 0.4 < loop.time() - started < 1.0
    assert _retries("p-hang", "recovered") == 1


async def test_a_budget_too_small_for_two_attempts_gives_the_first_all_of_it() -> None:
    loop = asyncio.get_running_loop()
    results = [[MagicMock()]]

    async def _slow(**_kwargs: Any) -> list:  # noqa: ANN401
        answered = loop.create_future()
        loop.call_later(0.2, answered.set_result, results)
        return await answered

    service = _service(_slow)

    got = await query_nearest_points_with_retry(
        service, "records", ["req"], logger=_LOGGER, budget_seconds=1.0,
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


async def _hang(**_: Any) -> list[list[Any]]:  # noqa: ANN401
    # asyncio.sleep is patched out for the back-off, so wait on an event instead.
    await asyncio.Event().wait()
    return []


async def test_under_a_request_deadline_the_budget_is_cut_to_fit_it() -> None:
    from app.utils.request_deadline import deadline_after

    service = _service(None, name="p-deadline")
    service.query_nearest_points = AsyncMock(side_effect=_hang)
    loop = asyncio.get_running_loop()

    with deadline_after(search_retry.DEADLINE_RESERVE_SECONDS + 1.5):
        started = loop.time()
        with pytest.raises(VectorSearchUnavailableError) as raised:
            await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    # Not the 60 s budget: what the deadline left after the reserve.
    assert loop.time() - started < 5
    assert raised.value.kind is SearchFailureKind.TIMEOUT
    assert service.query_nearest_points.await_count == 1


async def test_with_too_little_left_it_fails_at_once_without_asking_the_store(caplog) -> None:
    from app.utils.request_deadline import deadline_after

    service = _service([[[MagicMock()]]], name="p-no-time")

    with deadline_after(search_retry.DEADLINE_RESERVE_SECONDS + 0.2), caplog.at_level(logging.WARNING, logger=_LOGGER.name):
        with pytest.raises(VectorSearchUnavailableError) as raised:
            await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)

    assert raised.value.kind is SearchFailureKind.TIMEOUT
    service.query_nearest_points.assert_not_awaited()
    assert "before the request's deadline" in caplog.text


async def test_without_a_deadline_the_budget_is_unchanged() -> None:
    results = [[MagicMock()]]
    service = _service([results], name="p-no-deadline")
    with patch.object(search_retry.asyncio, "wait_for", wraps=asyncio.wait_for) as wait_for:
        assert await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER) is results
    timeout = wait_for.call_args.kwargs["timeout"]
    assert timeout > search_retry._first_attempt_seconds(search_retry.SEARCH_RETRY_BUDGET_SECONDS) - 1


async def test_the_search_is_timed_as_a_vector_span() -> None:
    from app.utils.stage_timer import collect_spans

    service = _service([[[MagicMock()]]], name="p-span")
    with collect_spans() as spans:
        await query_nearest_points_with_retry(service, "records", ["req"], logger=_LOGGER)
    assert len(spans["vector"]) == 1
