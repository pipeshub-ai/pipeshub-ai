"""Only a result worth reusing is stored: never empty, truncated, failed or missing output."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.paid_cache.cache import NotCached, PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.utils.is_truncated import is_truncated
from tests.support.paid_cache import MemoryResultStore

KEY = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")


def _response(**metadata: object) -> SimpleNamespace:
    return SimpleNamespace(content="text", response_metadata=metadata, additional_kwargs={})


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        # OpenAI, Azure OpenAI, Groq, Mistral, OpenAI-compatible servers
        ({"finish_reason": "length"}, True),
        ({"finish_reason": "stop"}, False),
        ({"finish_reason": "tool_calls"}, False),
        # Anthropic
        ({"stop_reason": "max_tokens"}, True),
        ({"stop_reason": "end_turn"}, False),
        ({"stop_reason": "model_context_window_exceeded"}, True),
        # Gemini and Vertex: a string or an enum's repr
        ({"finish_reason": "MAX_TOKENS"}, True),
        ({"finish_reason": "FinishReason.MAX_TOKENS"}, True),
        ({"finish_reason": "STOP"}, False),
        # Bedrock Converse
        ({"stopReason": "max_tokens"}, True),
        ({"stopReason": "end_turn"}, False),
        # Ollama
        ({"done_reason": "length"}, True),
        ({"done_reason": "stop"}, False),
        # nothing said, or a shape not listed
        ({}, False),
        ({"finish_reason": None}, False),
        ({"unrelated": "length"}, False),
    ],
)
def test_is_truncated_for_every_provider(metadata: dict[str, object], expected: bool) -> None:
    assert is_truncated(_response(**metadata)) is expected


def test_is_truncated_reads_an_enum_and_a_plain_mapping_and_raw_kwargs() -> None:
    enum_like = SimpleNamespace(name="MAX_TOKENS")
    assert is_truncated(_response(finish_reason=enum_like))
    assert is_truncated({"finish_reason": "length"})
    assert is_truncated(SimpleNamespace(response_metadata={}, additional_kwargs={"stop_reason": "max_tokens"}))
    assert not is_truncated(None)
    assert not is_truncated(SimpleNamespace())


@pytest.mark.parametrize("bad", ["", "  \n", None, [], {}, NotCached("cut off at the limit"), NotCached("")])
async def test_bad_outputs_are_not_cached(bad: object) -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls = 0

    async def compute() -> object:
        nonlocal calls
        calls += 1
        return bad

    first = await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]
    second = await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]

    expected = bad.value if isinstance(bad, NotCached) else bad
    assert first == expected
    assert second == expected
    # Both calls paid: nothing was stored, so nothing was reused.
    assert calls == 2
    assert store.results("org-1", "rec-1") == []


async def test_an_empty_result_is_never_put() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)

    async def empty() -> object:
        return NotCached("")

    await cache.get_or_compute(KEY, empty)
    assert store.ops["put"] == 0
    assert store.files == {}


async def test_a_good_result_is_stored_once_and_reused() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return "# Page one"

    assert await cache.get_or_compute(KEY, compute) == "# Page one"
    assert await cache.get_or_compute(KEY, compute) == "# Page one"
    assert calls == 1
    assert store.results("org-1", "rec-1") == [KEY.relative_path()]


async def test_an_object_that_is_not_ours_or_not_whole_is_a_miss_that_is_paid_for() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    for damaged in (b"{not json", b'{"v":9,"kind":"ocr_page","value":"x"}', b'{"v":1,"kind":"classify","value":"x"}', b"[]"):
        store.files[("org-1", "rec-1", KEY.relative_path())] = (damaged, 1)

        async def compute() -> str:
            return "fresh"

        assert await cache.get_or_compute(KEY, compute) == "fresh"
