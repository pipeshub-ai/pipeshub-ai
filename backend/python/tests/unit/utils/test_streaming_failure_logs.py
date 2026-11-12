"""A model call through utils/streaming.py that fails is logged once, with its class and none of the provider's text.

A provider's error message is its response body, which can echo the prompt, the document or the account's email.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.utils.streaming import (
    aiter_llm_stream,
    call_aiter_llm_stream_simple,
    invoke_with_structured_output_and_reflection,
)
from tests.support.provider_errors import (
    MARK,
    assert_text_free,
    failure_line,
    httpx_error,
    openai_error,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LOGGER = "streaming"  # create_logger("streaming")


class _Answer(BaseModel):
    answer: str


def _structured_llm(*outcomes: object) -> MagicMock:
    llm = MagicMock(spec=ChatOpenAI)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=list(outcomes))
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


async def _structured(llm: MagicMock, **kwargs: object) -> _Answer | None:
    return await invoke_with_structured_output_and_reflection(llm, [HumanMessage(content="q")], _Answer, **kwargs)


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    return caplog


@pytest.mark.parametrize(
    ("error", "error_class", "level"),
    [
        (lambda: openai_error(400), "bad_request", logging.WARNING),
        (lambda: openai_error(401), "misconfigured", logging.ERROR),
        (lambda: httpx_error(400), "bad_request", logging.WARNING),
        (lambda: httpx_error(401), "auth_expired", logging.WARNING),
    ],
)
async def test_a_failed_structured_call_is_one_text_free_line(log, error, error_class, level) -> None:
    assert await _structured(_structured_llm(error())) is None

    fields = failure_line(log.records, "structured_llm.failed", error_class, level)
    assert (fields["schema"], fields["attempt"]) == ("_Answer", 0)
    assert_text_free(log.records)


async def test_an_error_level_failure_keeps_its_frames_without_the_text(log) -> None:
    await _structured(_structured_llm(openai_error(401)))

    (line,) = [r for r in log.records if r.getMessage() == "structured_llm.failed"]
    assert "Traceback" in line.exc_text
    assert "AuthenticationError: <message omitted>" in line.exc_text


async def test_a_caller_that_asked_for_the_error_gets_it_unlogged(log) -> None:
    error = openai_error(400)
    with pytest.raises(type(error)) as raised:
        await _structured(_structured_llm(error), raise_call_errors=True)

    assert raised.value is error
    assert not [r for r in log.records if r.getMessage() == "structured_llm.failed"]
    assert_text_free(log.records)


async def test_an_answer_that_does_not_fit_the_schema_stays_a_warning(log) -> None:
    await _structured(_structured_llm(OutputParserException(f"Invalid json output: {MARK}")))

    failure_line(log.records, "structured_llm.failed", "bad_request", logging.WARNING)
    assert_text_free(log.records)


async def test_our_own_bug_is_still_an_error_with_frames(log) -> None:
    await _structured(_structured_llm(KeyError("missing")))

    (line,) = [r for r in log.records if r.getMessage() == "structured_llm.failed"]
    assert line.levelno == logging.ERROR
    assert "KeyError: <message omitted>" in line.exc_text


async def test_a_failed_reflection_call_is_logged_with_its_attempt(log) -> None:
    # The first answer does not parse; the reflection call is then refused. (4xx only here: the gateway
    # retries a 429 with real sleeps and counts a 5xx against the provider's breaker.)
    llm = _structured_llm(AIMessage(content="not json"), openai_error(401))

    assert await _structured(llm, max_retries=1) is None

    fields = failure_line(log.records, "structured_llm.failed", "misconfigured", logging.ERROR)
    assert fields["attempt"] == 1
    assert_text_free(log.records)


async def test_a_reflection_answer_that_does_not_fit_stays_a_warning_without_it(log) -> None:
    llm = _structured_llm(AIMessage(content="not json"), AIMessage(content=f'{{"wrong": "{MARK}"}}'))

    assert await _structured(llm, max_retries=1) is None

    failure_line(log.records, "structured_llm.failed", "bad_request", logging.WARNING)
    assert_text_free(log.records)


async def test_a_last_reflection_timeout_is_raised_unlogged(log) -> None:
    llm = _structured_llm(AIMessage(content="not json"), TimeoutError())

    with pytest.raises(TimeoutError):
        # A caller's own budget: the gateway raises its TimeoutError as is (no breaker count).
        await _structured(llm, max_retries=1, raise_call_errors=True, call_timeout=5)

    assert not [r for r in log.records if r.getMessage() == "structured_llm.failed"]


async def test_a_refused_cache_parameter_is_named_without_the_refusal(log, monkeypatch) -> None:
    import app.utils.streaming as streaming

    monkeypatch.setattr(streaming, "refused_cache_params", lambda exc, sent: frozenset({"cache_control"}) if
                        isinstance(exc, Exception) and "F24BMARK" in str(exc) else frozenset())
    monkeypatch.setattr(streaming, "forget_cache_params", lambda *_a, **_k: None)
    llm = _structured_llm(openai_error(400), _Answer(answer="ok"))

    assert await _structured(llm) == _Answer(answer="ok")

    (note,) = [r for r in log.records if "rejected; resending" in r.getMessage()]
    assert note.levelno == logging.WARNING
    assert note.error_type == "BadRequestError"
    assert "cache_control" in note.getMessage()
    assert_text_free(log.records)


async def test_a_failed_chat_stream_is_one_line_and_still_reported(log) -> None:
    llm = MagicMock(spec=ChatOpenAI)

    async def _refuse(*_a: object, **_k: object) -> AsyncIterator[str]:
        raise openai_error(400)
        yield  # pragma: no cover - an async generator that fails on its first step

    llm.astream = _refuse
    events = [e async for e in call_aiter_llm_stream_simple(llm, [HumanMessage(content="q")], [])]

    assert events[-1]["event"] == "error"
    failure_line(log.records, "llm_stream.failed", "bad_request", logging.WARNING)
    assert not [r for r in log.records if "aiter_llm_stream" in r.getMessage()]


class _Chunk(BaseModel):
    role: str
    content: str


async def test_a_skipped_chunk_is_noted_without_the_chunk(log) -> None:
    """A chunk that fails role validation is skipped. Pydantic's text quotes each failing field's value
    (``input_value``), here the chunk's content, the model's output: the note names the type only. The OSS
    formatters mask ``input_value=`` when they render, so the check is on the record itself, which any other
    handler or formatter reads."""
    try:
        _Chunk.model_validate({"role": None, "content": {"text": MARK}})
    except ValueError as exc:
        rejected = exc
    assert MARK in str(rejected)
    llm = MagicMock(spec=ChatOpenAI)

    async def _stream(*_a: object, **_k: object) -> AsyncIterator[AIMessage]:
        yield AIMessage(content="first")
        raise rejected

    llm.astream = _stream
    chunks = [c async for c in aiter_llm_stream(llm, [HumanMessage(content="q")])]

    assert chunks == ["first"]
    (note,) = [r for r in log.records if "Skipping chunk" in r.getMessage()]
    assert (note.levelno, note.error_type) == (logging.WARNING, "ValidationError")
    assert "F24BMARK" not in note.getMessage()
    assert_text_free(log.records)
