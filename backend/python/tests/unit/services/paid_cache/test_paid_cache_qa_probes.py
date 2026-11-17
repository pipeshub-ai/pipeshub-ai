"""QA S7a round 1 probes, promoted into the suite (ledger rows QA-S7a-*). Each probe states what it attacks.

Seeds: QA_SEED (default fixed, so a failure reproduces; set it to explore).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.excel.prompt_template import RowDescriptions
from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.services.paid_cache import access
from app.services.paid_cache.access import cached_model_call, set_ambient_cache
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import KIND_TABLE_ROWS, CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.retention import sweep_record
from app.services.paid_cache.run import begin_run, clear_run
from app.utils import table_enrichment
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

SEED = int(os.environ.get("QA_SEED") or 1698002757)
print(f"QA_SEED={SEED}")
RNG = random.Random(SEED)
PAGES = 8

if TYPE_CHECKING:
    from collections.abc import Iterator


def _rid() -> str:
    return "%08x-%04x" % (RNG.getrandbits(32), RNG.getrandbits(16))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, **_k: cache)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    set_ambient_cache(cache)
    yield memory
    set_ambient_cache(None)
    clear_run()


def _strategy(model: Any, pages: int = PAGES, render: str = "r") -> VLMOCRStrategy:  # noqa: ANN401
    s = VLMOCRStrategy(logging.getLogger("qa-s7a"), MagicMock())
    s.PAGE_RENDER_BATCH_SIZE = 4
    s.CONCURRENCY_LIMIT = 3
    s.llm = model
    s.doc = MagicMock(pages=[MagicMock(width=10, height=20) for _ in range(pages)])
    s._pdf_path = "/unused.pdf"
    s._render_page_batch_to_base64 = lambda numbers: {n: f"data:image/png;base64,{render}-{n}" for n in numbers}
    return s


# ---- Q1: every key field changes the digest and the path (fresh random values) ---------------------------

def test_q1_every_field_changes_the_key() -> None:
    fields = ["org_id", "record_id", "kind", "kind_version", "model_identity", "prompt_digest", "params_digest", "input_digest"]
    kinds = ["ocr_page", "image_description", "table_summary", "table_rows", "excel_headers"]
    for _ in range(200):
        base = {"org_id": _rid(), "record_id": _rid(), "kind": RNG.choice(kinds), "kind_version": RNG.randint(1, 3),
                "model_identity": _rid(), "prompt_digest": _rid(), "params_digest": _rid(), "input_digest": _rid()}
        k0 = CacheKey(**base)
        for f in fields:
            changed = dict(base)
            changed[f] = RNG.choice([k for k in kinds if k != base["kind"]]) if f == "kind" else (
                base[f] + 1 if f == "kind_version" else _rid())
            k1 = CacheKey(**changed)
            assert k1.digest() != k0.digest(), f
            assert k1.path() != k0.path(), f


def test_q1b_separator_injection_does_not_collide() -> None:
    a = CacheKey("o|r", "x", "ocr_page", 1, "m", "p", "q", "i")
    b = CacheKey("o", "r|x", "ocr_page", 1, "m", "p", "q", "i")
    assert a.digest() != b.digest()


# ---- Q2: at the VLM site, a model switch, a prompt edit, a params change each re-pay; vrid change hits -----

async def test_q2_vlm_model_prompt_params_switch_repays(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    org, rec = _rid(), _rid()
    m1 = CountingModel("vlm-a")
    with record_in_flight(org, rec):
        await _strategy(m1)._preprocess_document()
        await _strategy(m1)._preprocess_document()                 # same everything: hit
        assert m1.total == PAGES
        m2 = CountingModel("vlm-b")                                 # model switch
        await _strategy(m2)._preprocess_document()
        assert m2.total == PAGES
        m3 = CountingModel("vlm-a")
        m3.temperature = 0.9                                        # params change on the model
        await _strategy(m3)._preprocess_document()
        assert m3.total == PAGES
        monkeypatch.setattr(VLMOCRStrategy, "DEFAULT_PROMPT", VLMOCRStrategy.DEFAULT_PROMPT + "\nBe terse.")
        m4 = CountingModel("vlm-a")                                 # prompt edit
        await _strategy(m4)._preprocess_document()
        assert m4.total == PAGES
        monkeypatch.setattr(VLMOCRStrategy, "RENDER_DPI", VLMOCRStrategy.RENDER_DPI + 50)
        m5 = CountingModel("vlm-a")                                 # DPI change (same render string here)
        await _strategy(m5)._preprocess_document()
        assert m5.total == PAGES


async def test_q2b_reedit_new_vrid_hits_within_record_only(store: MemoryResultStore) -> None:
    """The vrid is not part of the key: a re-update with the same pages hits; another record pays its own."""
    org, rec, other = _rid(), _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        await _strategy(m)._preprocess_document()
        await _strategy(m)._preprocess_document()                   # new vrid, same bytes: the key does not see it
    assert m.total == PAGES
    with record_in_flight(org, other):
        await _strategy(m)._preprocess_document()
    assert m.total == 2 * PAGES
    assert m.duplicates == 0                                        # counted per (org, record): the other record's are its own


# ---- Q3: truncated VLM page is never stored, and the next run pays again ----------------------------------

@pytest.mark.parametrize("meta", [{"finish_reason": "length"}, {"stop_reason": "max_tokens"},
                                  {"finish_reason": "MAX_TOKENS"}, {"stopReason": "max_tokens"}, {"done_reason": "length"}])
async def test_q3_truncated_page_not_stored(store: MemoryResultStore, meta: dict) -> None:
    org, rec = _rid(), _rid()
    m = CountingModel("vlm", metadata=meta)
    with record_in_flight(org, rec):
        with pytest.raises(Exception):  # noqa: B017 - which error a truncated page raises is the strategy's business
            await _strategy(m, pages=2)._preprocess_document()
    assert store.results(org, rec) == []


# ---- Q4: two orgs with identical corpora and the SAME record id share nothing ------------------------------

async def test_q4_two_orgs_identical_corpora_share_nothing(store: MemoryResultStore) -> None:
    rec = _rid()
    a, b = _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(a, rec):
        await _strategy(m)._preprocess_document()
    store.touched.clear()
    with record_in_flight(b, rec):
        await _strategy(m)._preprocess_document()
    assert m.total == 2 * PAGES                                     # each org pays its own
    assert store.touched == {(b, rec)}                              # org b's run never named org a
    assert set(store.results(a, rec)).isdisjoint(set()) and len(store.results(a, rec)) == PAGES
    assert len(store.results(b, rec)) == PAGES
    assert all(k[0] in (a, b) for k in store.files)


# ---- Q5: a corrupt / foreign / older-format object is a miss, never served -- and is it ever replaced? -----

@pytest.mark.parametrize("raw", [b"{\"v\":1,\"kind\":\"ocr_page\",\"val", b"\x00\xff", b"[]",
                                 b'{"v":2,"kind":"ocr_page","value":"x"}', b'{"v":1,"kind":"image_description","value":"x"}'])
async def test_q5_corrupt_object_is_a_miss_and_never_served(store: MemoryResultStore, raw: bytes) -> None:
    org, rec = _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        await _strategy(m, pages=1)._preprocess_document()
        (path,) = store.results(org, rec)
        store.files[(org, rec, path)] = (raw, store.now_ms)          # corrupt it in place
        out = await _strategy(m, pages=1)._preprocess_document()
    assert m.total == 2                                             # a miss: paid again, the bad bytes not served
    assert "val" not in json.dumps(out, default=str)


async def test_q5b_a_corrupt_object_is_replaced_so_only_one_run_pays(store: MemoryResultStore) -> None:
    """QA-S7a-4: the bad object is replaced by the compute that found it; the runs after it hit."""
    org, rec = _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        await _strategy(m, pages=1)._preprocess_document()
        (path,) = store.results(org, rec)
        store.files[(org, rec, path)] = (b'{"v":1,"kind":"ocr_page","val', store.now_ms)
        for _ in range(3):
            await _strategy(m, pages=1)._preprocess_document()
    assert m.total == 2                                             # 1 + the repair; the 2 runs after hit


# ---- Q6: a stored value that no longer decodes (schema change) is not treated as a miss -------------------

async def test_q6_undecodable_value_is_a_miss(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def rows(llm: object, messages: list, model: type) -> RowDescriptions:
        calls["n"] += 1
        return RowDescriptions(descriptions=["row one", "row two"])

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", rows)
    llm = CountingModel("indexing")
    table = {"grid": [["a", "b"], ["1", "2"], ["3", "4"]]}
    org, rec = _rid(), _rid()
    with record_in_flight(org, rec):
        first = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])
        (path,) = store.results(org, rec)
        # An object written by an older release whose schema differs (same kind, same FORMAT):
        store.files[(org, rec, path)] = (json.dumps({"v": 1, "kind": KIND_TABLE_ROWS, "value": {"descriptions": "x"}}).encode(), store.now_ms)
        second = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])
    print("first", first, "second", second, "calls", calls["n"])
    # A miss: the model is paid again and the record gets real descriptions.
    assert calls["n"] == 2, f"decode failure was not a miss: calls={calls['n']} second={second}"


# ---- Q7: single flight with a lock backend that yields (as Redis does), holder lapses, two waiters ---------

class _YieldingLocks(InProcessLocks):
    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        await asyncio.sleep(0)
        return await super().acquire(name, ttl_ms)

    async def release(self, name: str, token: str) -> None:
        await asyncio.sleep(0)
        await super().release(name, token)


class _YieldingStore(MemoryResultStore):
    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        await asyncio.sleep(0)
        return await super().get(org_id, record_id, path)


async def test_q7_lapsed_holder_two_waiters_pay_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import cache as cache_mod
    monkeypatch.setattr(cache_mod, "POLL_FIRST_S", 0.01)
    monkeypatch.setattr(cache_mod, "POLL_MAX_S", 0.01)
    store, locks = _YieldingStore(), _YieldingLocks()
    replicas = [PaidResultCache(store, locks) for _ in range(3)]    # three processes: one store, one Redis
    key = CacheKey(_rid(), _rid(), "ocr_page", 1, "m", "p", "q", "i")
    from app.services.paid_cache.locks import lock_name
    # A holder that died mid-call: its lock is held, then lapses.
    assert await locks.acquire(lock_name(key.org_id, key.record_id, key.digest()), 120_000)
    paid = {"n": 0}

    async def compute() -> str:
        paid["n"] += 1
        await asyncio.sleep(0.05)
        return "text"

    waiters = [asyncio.create_task(r.get_or_compute(key, compute, wait_s=5)) for r in replicas[:2]]
    await asyncio.sleep(0.03)
    locks.expire_all()
    results = await asyncio.gather(*waiters)
    assert results == ["text", "text"]
    assert paid["n"] == 1, f"{paid['n']} paid calls after the holder lapsed"


async def test_q7b_live_slow_holder_past_wait_s_is_paid_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA-S7a-5: the waiter's wait is bounded by the holder's lease, not by wait_s."""
    from app.services.paid_cache import cache as cache_mod
    monkeypatch.setattr(cache_mod, "POLL_FIRST_S", 0.01)
    monkeypatch.setattr(cache_mod, "POLL_MAX_S", 0.02)
    store, locks = MemoryResultStore(), InProcessLocks()
    a, b = PaidResultCache(store, locks), PaidResultCache(store, locks)
    key = CacheKey(_rid(), _rid(), "ocr_page", 1, "m", "p", "q", "i")
    paid = {"n": 0}

    async def slow() -> str:
        paid["n"] += 1
        await asyncio.sleep(0.4)                                    # a rate-limited provider: longer than wait_s
        return "text"

    t1 = asyncio.create_task(a.get_or_compute(key, slow, wait_s=0.2))
    await asyncio.sleep(0.02)
    t2 = asyncio.create_task(b.get_or_compute(key, slow, wait_s=0.2))
    await asyncio.gather(t1, t2)
    assert paid["n"] == 1, f"{paid['n']} paid calls with a live holder"


# ---- Q8: the legacy in-process path stores objects but writes no manifest: the sweep never removes them ---

# (q8, "the default pipeline writes no manifest", is tests/unit/events/test_paid_cache_run.py::test_the_in_process_pipeline_keeps_two_revisions_over_five)


async def test_q8b_parsing_service_path_keeps_two_revisions(store: MemoryResultStore) -> None:
    """Control for Q8: the same five revisions through start_run/complete_run keep only two."""
    org, rec = _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        for rev in range(5):
            run = access.start_run(f"rev{rev}", "parse")
            await _strategy(m, render=f"rev{rev}")._preprocess_document()
            await run.finish(store)
            store.now_ms += 2 * 3600 * 1000
    await sweep_record(store, org, rec, now_ms=store.now_ms)
    assert len(store.results(org, rec)) == 2 * PAGES


# ---- Q9: a record delete that lands while a parse is still running leaves objects behind ------------------

async def test_q9_purge_during_inflight_parse_leaves_objects(store: MemoryResultStore) -> None:
    org, rec = _rid(), _rid()
    gate = asyncio.Event()
    m = CountingModel("vlm")
    original = m.ainvoke

    async def gated(messages: object, **kw: object) -> object:
        await gate.wait()
        return await original(messages, **kw)

    m.ainvoke = gated  # type: ignore[method-assign]
    cache = access.paid_cache_for(None)                             # the process's cache: one marker, one store

    async def orphan_parse() -> None:
        with record_in_flight(org, rec):
            run = access.start_run("rev", "parse")
            await _strategy(m)._preprocess_document()
            await run.finish(cache.store)

    task = asyncio.create_task(orphan_parse())
    await asyncio.sleep(0.05)
    await cache.delete_record(org, rec)                             # the deleteRecord handler
    gate.set()
    await task
    left = store.paths(org, rec)
    print("left after delete:", len(left))
    assert left == [], f"{len(left)} objects of a deleted record remain"


# ---- Q10: cache off: every call computes, nothing is stored, start_run is None -------------------------------

async def test_q10_cache_off_is_base_behaviour(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_CACHE", "off")
    org, rec = _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        assert access.start_run("rev", "parse") is None
        await _strategy(m)._preprocess_document()
        await _strategy(m)._preprocess_document()
    assert m.total == 2 * PAGES and store.files == {} and sum(store.ops.values()) == 0


# ---- Q11: refresh re-pays; a plain (forceReindex) run hits; refresh does not leak to another record -------

async def test_q11_refresh_repays_force_hits(store: MemoryResultStore) -> None:
    org, rec, other = _rid(), _rid(), _rid()
    m = CountingModel("vlm")
    with record_in_flight(org, rec):
        await _strategy(m)._preprocess_document()
        await _strategy(m)._preprocess_document()                   # forceReindex: same content -> hit
        assert m.total == PAGES
        begin_run(org, rec, None, "index", refresh=True)
        await _strategy(m)._preprocess_document()                   # refresh: every page paid again
        assert m.total == 2 * PAGES
    with record_in_flight(org, other):
        await _strategy(m)._preprocess_document()                   # another record: its own, refresh not applied
        await _strategy(m)._preprocess_document()
    assert m.total == 3 * PAGES


# ---- Q12: cached_model_call with no record in flight never touches storage -------------------------------

async def test_q12_no_record_in_flight_never_touches_storage(store: MemoryResultStore) -> None:
    async def call() -> str:
        return "summary"

    for _ in range(3):
        out = await cached_model_call("table_summary", llm=CountingModel("x"), template="t", rendered="r",
                                      call=call, encode=lambda v: v, decode=str)
        assert out == "summary"
    assert sum(store.ops.values()) == 0
