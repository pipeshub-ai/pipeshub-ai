"""Model and embedding calls in app/utils log a failure or a retry without the provider's text.

A provider's error message is its response body, which can echo the prompt, the query or the account's email.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import custom_embeddings, embedding_retry
from app.utils import record_extract as extract_module
from app.utils.pattern_match import GrepCommandResult, generate_grep_command_via_llm
from tests.support.provider_errors import (
    MARK,
    assert_text_free,
    failure_line,
    httpx_error,
    openai_error,
    schema_mismatch,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.mark.parametrize(
    ("error", "error_class", "level"),
    [
        (lambda: openai_error(400), "bad_request", logging.WARNING),
        (lambda: openai_error(401), "misconfigured", logging.ERROR),
        (lambda: httpx_error(400), "bad_request", logging.WARNING),
        # An answer that does not fit the schema is the model's, not our bug.
        (lambda: schema_mismatch(GrepCommandResult), "bad_request", logging.WARNING),
    ],
)
async def test_each_failed_grep_generation_attempt_is_one_text_free_line(log, error, error_class, level) -> None:
    structured = MagicMock(ainvoke=AsyncMock(side_effect=[error(), error()]))
    with patch("app.utils.streaming._apply_structured_output", return_value=structured), patch(
        "app.agent_loop_lib.transport.opik_tracing.build_langchain_opik_callbacks", return_value=[],
    ):
        assert await generate_grep_command_via_llm("q", MagicMock(), logging.getLogger("test.grep")) is None

    lines = [r for r in log.records if r.getMessage() == "grep_generation.failed"]
    assert [r.attempt for r in lines] == [1, 2]
    assert {(r.error_class, r.levelno) for r in lines} == {(error_class, level)}
    assert_text_free(log.records)


async def test_a_failed_record_extract_returns_the_plain_read_with_one_text_free_line(log) -> None:
    record = {"id": "r-1", "virtual_record_id": "v-r-1", "context_metadata": "Record ID: r-1",
              "block_containers": {"blocks": [{"index": 0, "type": "text", "parent_index": None, "data": "x"}],
                                   "block_groups": []}}
    llm = MagicMock(ainvoke=AsyncMock(side_effect=openai_error(400)))
    with patch.object(extract_module, "get_llm_for_role", AsyncMock(return_value=(llm, {}))):
        result = await extract_module.extract_record(
            record, query="q", config_service=MagicMock(), logger=logging.getLogger("test.extract"),
            fallback_chars=40_000,
        )

    assert result["extract"]["error"] == "BadRequestError"
    assert failure_line(log.records, "record_extract.failed", "bad_request", logging.WARNING)["record_id"] == "r-1"
    assert_text_free(log.records)


def _flaky(error: Exception) -> Callable[[], str]:
    calls: list[int] = []

    def call() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return "ok"

    return call


def test_a_retried_sync_embedding_names_the_type_only(log) -> None:
    with patch.object(embedding_retry, "retry_delay_seconds", return_value=0), patch.object(embedding_retry.time, "sleep"):
        assert embedding_retry.call_with_retry(
            _flaky(openai_error(429)), max_retries=2, operation="embed_query", service_name="embedding-server",
        ) == "ok"

    (note,) = [r for r in log.records if "retrying" in r.getMessage()]
    assert note.levelno == logging.WARNING
    assert note.error_type == "RateLimitError"
    assert "failed (attempt 1/2): RateLimitError;" in note.getMessage()
    assert_text_free(log.records)


async def test_a_retried_async_embedding_names_the_type_only(log) -> None:
    call = _flaky(openai_error(429))

    async def fn() -> str:
        return call()

    with patch.object(embedding_retry, "retry_delay_seconds", return_value=0):
        assert await embedding_retry.await_with_retry(
            fn, max_retries=2, operation="aembed_query", service_name="retrieval",
        ) == "ok"

    (note,) = [r for r in log.records if "retrying" in r.getMessage()]
    assert note.error_type == "RateLimitError"
    assert_text_free(log.records)


async def test_an_embedding_error_that_is_not_retried_is_raised_without_a_line(log) -> None:
    error = openai_error(400)

    async def fn() -> str:
        raise error

    with pytest.raises(type(error)):
        await embedding_retry.await_with_retry(fn, max_retries=3, operation="aembed_query", service_name="retrieval")

    assert not log.records


def test_a_retried_voyage_embedding_names_the_type_only(log, monkeypatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)
    responses = iter([{"detail": MARK}, {"data": [{"embedding": [0.1]}]}])
    monkeypatch.setattr(custom_embeddings.requests, "post", lambda **_k: SimpleNamespace(json=lambda: next(responses)))

    result = custom_embeddings.embed_with_retry(SimpleNamespace(max_retries=2), url="https://voyage.example", json={})

    assert result == {"data": [{"embedding": [0.1]}]}
    (note,) = [r for r in log.records if "retrying" in r.getMessage()]
    assert note.levelno == logging.WARNING
    assert note.error_type == "RuntimeError"
    assert_text_free(log.records)
