"""Two orgs with identical corpora share nothing: no cache read, no object, no key, and each pays its own calls."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.services.model_identity.call_context import peek_call_context
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

if TYPE_CHECKING:
    import pytest

PAGES = 200


class SpyStore(MemoryResultStore):
    """Records, for every call, the org the work was running for and the org the call named."""

    def __init__(self) -> None:
        super().__init__()
        self.crossings: list[tuple[str | None, str]] = []

    def _call(self, op: str, org: str, record: str | None = None) -> None:
        ctx = peek_call_context()
        ambient = ctx.principal.org_id if ctx else None
        if ambient != org:
            self.crossings.append((ambient, org))
        super()._call(op, org, record)


def _strategy(model: CountingModel) -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-isolation"), MagicMock())
    strategy.PAGE_RENDER_BATCH_SIZE = 20
    strategy.CONCURRENCY_LIMIT = 8
    strategy.llm = model  # type: ignore[assignment]
    strategy.doc = MagicMock(pages=[MagicMock(width=10, height=20) for _ in range(PAGES)])
    strategy._pdf_path = "/unused.pdf"
    strategy._render_page_batch_to_base64 = lambda numbers: {  # type: ignore[method-assign]
        n: f"data:image/png;base64,identical-page-{n}" for n in numbers
    }
    return strategy


async def test_identical_corpora_share_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SpyStore()
    cache = PaidResultCache(store, InProcessLocks())
    monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, **_k: cache)
    model = CountingModel("vlm")

    async def org_run(org: str) -> None:
        with record_in_flight(org, "record-1"):                 # the same record id in both orgs
            await _strategy(model)._preprocess_document()

    await asyncio.gather(org_run("org-a"), org_run("org-b"))

    assert store.crossings == []                                # no call ever named an org it was not running for
    assert {o for (o, _r) in store.touched} == {"org-a", "org-b"}
    assert model.total == 2 * PAGES and model.duplicates == 0   # each org paid for its own pages
    a, b = store.results("org-a", "record-1"), store.results("org-b", "record-1")
    assert len(a) == len(b) == PAGES
    assert set(a).isdisjoint(b)                                 # not even a shared key: the org is inside the digest
    assert {o for (o, _r, _p) in store.files} == {"org-a", "org-b"}


async def test_an_orgs_reads_never_see_another_orgs_result() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    key_a = CacheKey("org-a", "rec", "ocr_page", 1, "m", "p", "q", "same-page")
    key_b = CacheKey("org-b", "rec", "ocr_page", 1, "m", "p", "q", "same-page")
    paid: list[str] = []

    async def compute_for(org: str) -> str:
        paid.append(org)
        return f"text of {org}"

    assert await cache.get_or_compute(key_a, lambda: compute_for("org-a")) == "text of org-a"
    assert await cache.get_or_compute(key_b, lambda: compute_for("org-b")) == "text of org-b"
    assert paid == ["org-a", "org-b"]
    assert key_a.digest() != key_b.digest()


async def test_a_hand_planted_object_in_another_orgs_folder_is_not_read() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    key_a = CacheKey("org-a", "rec", "ocr_page", 1, "m", "p", "q", "page")
    # org-b has an object at org-a's path in org-b's own folder: a lookup for org-a never reads org-b's folder.
    store.files[("org-b", "rec", key_a.relative_path())] = (b'{"v":1,"kind":"ocr_page","value":"B secret"}', 1)

    async def compute() -> str:
        return "A's own"

    assert await cache.get_or_compute(key_a, compute) == "A's own"
