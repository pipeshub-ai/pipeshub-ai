"""The no-tools answer paths flag a final answer whose search was degraded, like the formatter does."""
import logging
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.modules.retrieval import degraded_notice
from app.utils.streaming import call_aiter_llm_stream_simple, handle_simple_mode

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _tracking() -> Iterator[None]:
    token = degraded_notice._seen.set(None)
    degraded_notice.track()
    yield
    degraded_notice._seen.reset(token)


async def _complete(gen: AsyncGenerator[dict, None]) -> dict:
    return next(e for e in [e async for e in gen] if e["event"] == "complete")["data"]


def _fast_path() -> AsyncGenerator[dict, None]:
    return handle_simple_mode(llm=MagicMock(), messages=[AIMessage(content="plain")], final_results=[], records=[],
                              logger=logging.getLogger("test"))


async def _llm_path() -> list[dict]:
    async def llm_stream(llm, messages, parts=None):  # noqa: ANN202
        yield "answer"
    with patch("app.utils.streaming.aiter_llm_stream", side_effect=llm_stream), \
         patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("answer", [])):
        return [e async for e in call_aiter_llm_stream_simple(
            llm=MagicMock(), messages=[HumanMessage(content="q")], final_results=[], records=[],
            target_words_per_chunk=99, original_llm=MagicMock(), chat_mode="web_search")]


async def _llm_complete() -> dict:
    return next(e for e in await _llm_path() if e["event"] == "complete")["data"]


async def test_fast_path_answer_carries_the_flag_after_a_degraded_search() -> None:
    degraded_notice.note()
    assert (await _complete(_fast_path()))["searchDegraded"] is True


async def test_fast_path_answer_carries_none_for_a_complete_search() -> None:
    assert "searchDegraded" not in await _complete(_fast_path())


async def test_llm_path_answer_carries_the_flag_after_a_degraded_search() -> None:
    degraded_notice.note()
    assert (await _llm_complete())["searchDegraded"] is True


async def test_llm_path_answer_carries_none_for_a_complete_search() -> None:
    assert "searchDegraded" not in await _llm_complete()
