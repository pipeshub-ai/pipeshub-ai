"""A scan's pages are read once per record: a retried or replayed document pays only for the pages not done."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

if TYPE_CHECKING:
    from collections.abc import Iterator

PAGES = 30
ORG, REC = "org-1", "rec-1"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, **_k: cache)
    yield memory
    clear_run()


def _strategy(model: CountingModel) -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-vlm-cache"), MagicMock())
    strategy.PAGE_RENDER_BATCH_SIZE = 6
    strategy.CONCURRENCY_LIMIT = 3
    strategy.llm = model  # type: ignore[assignment]
    strategy.doc = MagicMock(pages=[MagicMock(width=10, height=20) for _ in range(PAGES)])
    strategy._pdf_path = "/unused.pdf"
    strategy._render_page_batch_to_base64 = lambda numbers: {  # type: ignore[method-assign]
        n: f"data:image/png;base64,render-of-page-{n}" for n in numbers
    }
    return strategy


class _FailAt:
    """A provider outage that starts at one page's request and stays until ``recover``."""

    def __init__(self, model: CountingModel, page: int) -> None:
        self.page, self.down = page, True
        original = model.ainvoke

        async def ainvoke(messages: object, **kw: object) -> object:
            if self.down and f"render-of-page-{self.page}'" in repr(messages):
                raise vlm_ocr_strategy.LLMUnavailableError("provider down")
            return await original(messages, **kw)

        model.ainvoke = ainvoke  # type: ignore[method-assign]


async def test_a_retry_pays_only_the_pages_not_done(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    outage = _FailAt(model, 25)

    with record_in_flight(ORG, REC):
        with pytest.raises(vlm_ocr_strategy.LLMUnavailableError):
            await _strategy(model)._preprocess_document()
        paid_before_retry = model.total
        stored = len(store.results(ORG, REC))
        assert stored == paid_before_retry                       # every answered page is a durable object
        assert 20 <= stored < PAGES                              # and the pages the outage cut off are not

        outage.down = False
        result = await _strategy(model)._preprocess_document()   # the retry

    assert result["total_pages"] == PAGES
    assert model.total - paid_before_retry == PAGES - stored     # only the pages not done are requested
    assert model.total == PAGES                                  # 30 pages, 30 paid requests in all
    assert model.duplicates == 0
    assert model.total - paid_before_retry <= 10


async def test_a_replay_of_a_finished_scan_pays_nothing(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    with record_in_flight(ORG, REC):
        first = await _strategy(model)._preprocess_document()
        second = await _strategy(model)._preprocess_document()

    assert model.total == PAGES
    assert [p["markdown"] for p in second["pages"]] == [p["markdown"] for p in first["pages"]]


async def test_another_record_with_the_same_pages_pays_for_its_own(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    with record_in_flight(ORG, "rec-1"):
        await _strategy(model)._preprocess_document()
    with record_in_flight(ORG, "rec-2"):
        await _strategy(model)._preprocess_document()

    assert model.total == 2 * PAGES
    assert len(store.results(ORG, "rec-1")) == PAGES
    assert len(store.results(ORG, "rec-2")) == PAGES


async def test_another_org_with_the_same_pages_pays_for_its_own(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    with record_in_flight("org-1", REC):
        await _strategy(model)._preprocess_document()
    with record_in_flight("org-2", REC):
        await _strategy(model)._preprocess_document()

    assert model.total == 2 * PAGES
    assert {o for (o, _r, _p) in store.files} == {"org-1", "org-2"}


async def test_a_different_model_does_not_reuse_the_pages(store: MemoryResultStore) -> None:
    with record_in_flight(ORG, REC):
        await _strategy(CountingModel("vlm-a"))._preprocess_document()
        other = CountingModel("vlm-b")
        await _strategy(other)._preprocess_document()

    assert other.total == PAGES


async def test_a_changed_prompt_does_not_reuse_the_pages(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = CountingModel("vlm")
    with record_in_flight(ORG, REC):
        await _strategy(model)._preprocess_document()
        monkeypatch.setattr(VLMOCRStrategy, "DEFAULT_PROMPT", VLMOCRStrategy.DEFAULT_PROMPT + "\nAlso read footnotes.")
        await _strategy(model)._preprocess_document()

    assert model.total == 2 * PAGES


@pytest.mark.parametrize(
    "metadata",
    [{"finish_reason": "length"}, {"stop_reason": "max_tokens"}, {"finish_reason": "MAX_TOKENS"}, {"stopReason": "max_tokens"}],
)
async def test_a_truncated_page_is_never_stored_for_any_provider(
    store: MemoryResultStore, metadata: dict[str, str],
) -> None:
    model = CountingModel("vlm", metadata=metadata)
    with record_in_flight(ORG, REC):
        for _ in range(2):
            with pytest.raises(vlm_ocr_strategy.DocumentProcessingError, match="truncated"):
                await _strategy_one_page(model)._preprocess_document()

    assert store.results(ORG, REC) == []
    assert model.total == 2 * 2          # each document paid for its page twice (a cut-off page gets one more send): nothing was reused


def _strategy_one_page(model: CountingModel) -> VLMOCRStrategy:
    strategy = _strategy(model)
    strategy.doc = MagicMock(pages=[MagicMock(width=10, height=20)])
    return strategy


async def test_an_empty_page_is_not_stored(store: MemoryResultStore) -> None:
    model = CountingModel("vlm", reply="")
    with record_in_flight(ORG, REC):
        await _strategy_one_page(model)._preprocess_document()

    assert store.results(ORG, REC) == []


async def test_with_no_record_in_flight_the_pages_are_read_as_before(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    await _strategy(model)._preprocess_document()

    assert model.total == PAGES
    assert store.files == {}
    assert store.ops["get"] == 0


async def test_a_forced_refresh_pays_every_page_again(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    with record_in_flight(ORG, REC):
        await _strategy(model)._preprocess_document()
        await store.purge_record(ORG, REC)             # the handler purges the set first ...
        begin_run(ORG, REC, "rev", "index", refresh=True)  # ... and the run bypasses lookups
        await _strategy(model)._preprocess_document()

    assert model.total == 2 * PAGES
    assert len(store.results(ORG, REC)) == PAGES


async def test_a_refresh_run_bypasses_lookups_even_when_the_objects_are_there(store: MemoryResultStore) -> None:
    model = CountingModel("vlm")
    with record_in_flight(ORG, REC):
        await _strategy(model)._preprocess_document()
        begin_run(ORG, REC, "rev", "index", refresh=True)   # no purge: the stored set is still in place
        await _strategy(model)._preprocess_document()
        clear_run()
        await _strategy(model)._preprocess_document()       # an ordinary run reuses what the first stored

    assert model.total == 2 * PAGES


async def test_the_pages_a_run_used_are_listed_in_its_manifest(store: MemoryResultStore) -> None:
    import json

    model = CountingModel("vlm")
    with record_in_flight(ORG, REC):
        scope = begin_run(ORG, REC, "rev1", "parse")
        await _strategy(model)._preprocess_document()
        await scope.finish(store)                                # the run completes: the final manifest replaces the open one

    listed = json.loads(store.files[(ORG, REC, "manifests/rev1/parse.json")][0])["keys"]
    stored = {path for path in store.paths(ORG, REC) if path.startswith("ocr_page/")}
    assert len(stored) == PAGES and set(listed) == stored        # every stored page is protected by the manifest


@pytest.mark.parametrize("batch", range(8))
async def test_a_page_paid_while_its_batch_is_cancelled_is_still_stored(monkeypatch: pytest.MonkeyPatch, batch: int) -> None:
    """TC-1 repeated: 8 x 5 fresh runs. A sibling's outage cancels the batch; every answered page is stored."""
    del batch
    for _ in range(5):
        memory = MemoryResultStore()
        cache = PaidResultCache(memory)
        monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, _c=cache, **_k: _c)
        try:
            await test_a_retry_pays_only_the_pages_not_done(memory)
        finally:
            clear_run()
