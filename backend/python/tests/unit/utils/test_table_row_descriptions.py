"""Keyed table descriptions: alignment, fallbacks, summary survival, adaptive split, cost.

The fake model answers over the real call path (prompt -> structured-output call -> parse), so
these tests exercise the code a provider's answer goes through. It reads the rows out of the
prompt it is sent and answers in the shape that prompt asks for, so the same scenarios apply to
any prompt revision: a skipped, merged, duplicated, reordered, empty, over-long, malformed or
out-of-range answer must lose at most the rows it got wrong and never hand one row another
row's text. All data is synthetic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers.excel.prompt_template import (
    KeyedRowDescription,
    KeyedRowDescriptions,
    TableEnrichment,
)
from app.services.paid_cache.access import set_ambient_cache
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import (
    KIND_TABLE_ROWS,
    KIND_TABLE_SUMMARY,
    params_digest,
    sha256_hex,
    template_text,
)
from app.utils import indexing_helpers, table_enrichment
from app.utils.concurrency import TABLE_ROW_BATCH_SIZE, table_row_batch_size
from app.utils.indexing_metrics import track_indexing_enrichment
from tests.support.paid_cache import MemoryResultStore, record_in_flight
from tests.support.table_model import (
    HEAD,
    FakeTableModel,
    assert_aligned,
    by_first_row,
    count_hashed_bytes,
    make_rows,
)

if TYPE_CHECKING:
    from app.utils.table_enrichment import TableEnrichmentResult

if TYPE_CHECKING:
    from collections.abc import Iterator

LOG = logging.getLogger("test-table-row-descriptions")


@pytest.fixture(autouse=True)
def _pinned_row_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The batch size (50) and the row budget (1000) these tests count calls and rows by, whatever the environment sets."""
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", "50")
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "1000")
GRID_120 = [HEAD, *make_rows(120)]


async def enrich(
    model: FakeTableModel, grid: list[list[str]] = GRID_120, *, known: int | None = 1, **kw: object,
) -> TableEnrichmentResult:
    return await table_enrichment.enrich_table_grid(model, grid, logger=LOG, known_header_row_count=known, **kw)


# ---------------------------------------------------------------------------------------------------------
# Alignment: one wrong answer loses its own rows, never another row's text
# ---------------------------------------------------------------------------------------------------------

LATER = "REG50"  # first row of the second batch of GRID_120 (the first call carries REG0..REG49)


@pytest.mark.asyncio
async def test_a_skipped_row_in_a_later_batch_is_not_shifted_onto_the_next_rows() -> None:
    """B1: the shorter list used to be taken as is, so REG53 read 'desc of REG54'."""
    model = FakeTableModel(by_first_row(LATER, "skip:3", size=50))

    result = await enrich(model)

    assert result.descriptions[53] != "desc of REG54"
    assert result.descriptions[54] == "desc of REG54"
    assert_aligned(result)


@pytest.mark.asyncio
async def test_a_skipped_row_in_the_first_call_is_not_shifted_either() -> None:
    model = FakeTableModel(by_first_row("REG0", "skip:3", kind="combined"))

    result = await enrich(model)

    assert result.descriptions[4] == "desc of REG4"
    assert_aligned(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["first", "later"])
@pytest.mark.parametrize(
    "behavior",
    ["skip:3", "merge:3", "dup:3", "reverse", "nothing", "long", "malformed", "foreign", "counted", "lowerkeys", "bracketed"],
)
async def test_a_bad_answer_never_gives_a_row_another_rows_description(behavior: str, where: str) -> None:
    if where == "first":
        model = FakeTableModel(by_first_row("REG0", behavior, kind="combined"))
    else:
        model = FakeTableModel(by_first_row(LATER, behavior, size=50))

    result = await enrich(model)

    merged = frozenset({3 if where == "first" else 53}) if behavior == "merge:3" else frozenset()  # the model itself wrote two rows into row 3
    assert_aligned(result, skip=merged)
    if behavior == "dup:3":
        assert not any("WRONG" in t for t in result.descriptions), "the first answer for a key must win"
    if behavior != "merge:3":
        assert result.summary == "Register map"


@pytest.mark.asyncio
async def test_a_reordered_answer_still_lands_on_the_right_rows() -> None:
    model = FakeTableModel(by_first_row(LATER, "reverse", size=50))

    result = await enrich(model)

    assert [result.descriptions[i] for i in (50, 75, 99)] == [f"desc of REG{i}" for i in (50, 75, 99)]
    assert model.calls == [("combined", 50), ("rows", 50), ("rows", 20)], "a reorder needs no retry"
    assert not result.degraded


@pytest.mark.asyncio
async def test_a_long_answer_with_extras_and_duplicates_is_trimmed_not_failed() -> None:
    model = FakeTableModel(by_first_row(LATER, "long", size=50))

    result = await enrich(model)

    assert not any(t == "EXTRA" for t in result.descriptions)
    assert not result.degraded
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_an_unparsable_batch_falls_back_to_column_value_text_never_a_dict_repr() -> None:
    """B2: a later batch that did not parse kept ``str(row)``, a Python dict repr, unflagged."""

    def always_bad_rows(kind: str, _ids: list[str], _n: int) -> str:
        return "malformed" if kind == "rows" else "ok"

    result = await enrich(FakeTableModel(always_bad_rows))

    assert result.degraded
    assert result.descriptions[60] == "Name: REG60, Offset: 0x0F0, Reset: 0"
    assert_aligned(result, skip=frozenset())
    assert result.descriptions[10] == "desc of REG10"  # the first call's rows are kept


# ---------------------------------------------------------------------------------------------------------
# A failed first call keeps the summary and headers
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", ["malformed", "nothing", "foreign"])
async def test_a_failed_first_call_keeps_the_summary_and_describes_the_rows(combined: str) -> None:
    model = FakeTableModel(by_first_row("REG0", combined, kind="combined"))

    result = await enrich(model)

    assert result.summary == "Register map"
    assert result.headers == HEAD
    assert_aligned(result)
    assert result.descriptions[0] == "desc of REG0"
    assert model.count("rows") >= 3, "the rows are described in batches after the summary is settled"


@pytest.mark.asyncio
async def test_a_first_call_that_gave_its_summary_does_not_ask_for_it_again() -> None:
    model = FakeTableModel(by_first_row("REG0", "nothing", kind="combined"))

    await enrich(model)

    assert model.count("summary") == 0
    assert model.count("combined") == 1


@pytest.mark.asyncio
async def test_unparsed_first_call_asks_for_the_summary_alone() -> None:
    model = FakeTableModel(by_first_row("REG0", "malformed", kind="combined"))

    await enrich(model)

    assert model.count("summary") == 1


@pytest.mark.asyncio
async def test_a_table_falls_back_as_a_whole_only_when_summary_and_headers_fail_too() -> None:
    def everything_fails(_kind: str, _ids: list[str], _n: int) -> str:
        return "malformed"

    model = FakeTableModel(everything_fails)

    result = await enrich(model)

    assert result.degraded
    assert result.summary == "A table with 120 rows and columns: Name, Offset, Reset."
    assert model.count("rows") == 0, "no row calls once summary and headers are lost"
    assert result.descriptions[7] == "Name: REG7, Offset: 0x01C, Reset: 0"


@pytest.mark.asyncio
async def test_a_failed_call_is_an_error_not_a_retry() -> None:
    model = FakeTableModel(lambda _k, _i, _n: "raise")

    result = await enrich(model)

    assert result.degraded
    assert len(model.calls) == 1, "an outage is not met with more calls"


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", ["malformed", "foreign"])
async def test_the_header_row_stays_a_header_when_the_rows_fail(combined: str) -> None:
    """Unknown headers: the LLM says row 1 is the header. Failed rows must not turn it into data."""
    model = FakeTableModel(by_first_row("HDR", combined, kind="combined"), header_row_count=1)

    result = await enrich(model, GRID_120, known=None)

    assert result.header_row_count == 1
    assert result.headers == HEAD
    assert result.summary == "Register map"
    assert len(result.descriptions) == 120, "120 data rows: the header row is not one of them"
    assert result.descriptions[0] == "desc of REG0"
    assert_aligned(result)


@pytest.mark.asyncio
async def test_unknown_headers_align_the_first_call_by_key_past_the_header_row() -> None:
    model = FakeTableModel(header_row_count=1)

    result = await enrich(model, GRID_120, known=None)

    assert result.header_row_count == 1
    assert_aligned(result)
    assert not result.degraded
    assert model.calls == [("combined", 50), ("rows", 50), ("rows", 21)]


# ---------------------------------------------------------------------------------------------------------
# Adaptive split and its cost
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_table_costs_one_call_per_batch() -> None:
    model = FakeTableModel()

    result = await enrich(model)

    assert model.calls == [("combined", 50), ("rows", 50), ("rows", 20)]
    assert not result.degraded
    assert_aligned(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["malformed", "nothing", "foreign"])
async def test_one_bad_batch_is_split_in_half_and_only_it_costs_more(bad: str) -> None:
    model = FakeTableModel(by_first_row(LATER, bad, size=50))

    result = await enrich(model)

    assert sorted(model.row_calls) == [20, 25, 25, 50]
    assert model.count("combined") == 1
    assert not result.degraded, "both halves answered, so no row fell back"
    assert result.descriptions[50] == "desc of REG50" and result.descriptions[99] == "desc of REG99"
    assert_aligned(result)


@pytest.mark.asyncio
async def test_a_batch_missing_a_few_rows_is_not_split_those_rows_alone_fall_back() -> None:
    model = FakeTableModel(by_first_row(LATER, "skip:3", size=50))  # 1 of 50 rows missing: within the tolerance

    result = await enrich(model)

    assert len(model.calls) == 3
    assert result.degraded
    assert result.descriptions[53] == "Name: REG53, Offset: 0x0D4, Reset: 0"


@pytest.mark.asyncio
async def test_a_failed_half_splits_again_down_to_the_floor_then_falls_back() -> None:
    def bad_for_the_first_rows(kind: str, ids: list[str], _n: int) -> str:
        if kind == "rows" and ids[0] == "REG50" and len(ids) in (50, 25):
            return "nothing"
        return "ok"

    model = FakeTableModel(bad_for_the_first_rows)

    result = await enrich(model)

    assert sorted(model.row_calls) == [12, 13, 20, 25, 25, 50]
    assert min(model.row_calls) >= 5, "never below the floor"
    assert not result.degraded
    assert_aligned(result)


@pytest.mark.asyncio
async def test_a_small_failed_batch_is_not_split_below_the_floor() -> None:
    model = FakeTableModel(lambda kind, _i, _n: "nothing" if kind == "rows" else "ok")

    result = await enrich(model, [HEAD, *make_rows(8)], known=1, batch_size=4)

    # first call carries 4 rows; the next 4 go in one batch of 4 (< 10): no split, plain text
    assert model.row_calls == [4]
    assert result.degraded
    assert_aligned(result, 8)


@pytest.mark.asyncio
async def test_the_extra_calls_per_table_are_bounded_when_every_batch_fails() -> None:
    """1000 rows, a provider that answers every row call with nothing: calls stay within the budget."""
    model = FakeTableModel(lambda kind, _i, _n: "nothing" if kind == "rows" else "ok")
    grid = [HEAD, *make_rows(1000)]

    result = await enrich(model, grid)

    batches = 19  # 950 rows after the first call, 50 per batch
    budget = max(4, batches + 1)
    assert model.count("combined") == 1
    assert len(model.row_calls) <= batches + budget
    assert len(model.row_calls) > batches, "the budget is spent where batches fail"
    assert result.degraded
    assert_aligned(result, 1000, skip=frozenset(range(50, 1000)))
    assert all(t.startswith("Name: REG") for t in result.descriptions[50:])


@pytest.mark.asyncio
async def test_calls_per_table_match_the_nominal_count_when_nothing_fails() -> None:
    for rows, expected in ((10, 1), (50, 1), (51, 2), (120, 3), (1000, 20)):
        model = FakeTableModel()
        await enrich(model, [HEAD, *make_rows(rows)])
        assert len(model.calls) == expected, rows


# ---------------------------------------------------------------------------------------------------------
# Configurable batch size
# ---------------------------------------------------------------------------------------------------------


def test_the_batch_size_defaults_to_fifty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_TABLE_ROW_BATCH_SIZE", raising=False)
    assert table_row_batch_size() == TABLE_ROW_BATCH_SIZE == 50


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("20", 20), (" 30 ", 30), ("", 50), ("abc", 50), ("0", 50), ("-4", 50), ("9999", 500), ("2.5", 50)],
)
def test_the_batch_size_is_validated(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", raw)
    assert table_row_batch_size() == expected


@pytest.mark.asyncio
async def test_the_setting_changes_how_a_table_is_batched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", "20")
    model = FakeTableModel()

    result = await enrich(model)

    assert [n for _k, n in model.calls] == [20, 20, 20, 20, 20, 20]
    assert_aligned(result)


# ---------------------------------------------------------------------------------------------------------
# Paid-cache key separation: an older positional or numbered answer is never read as a keyed one
# ---------------------------------------------------------------------------------------------------------

# sha256 of template_text(...) of the prompts as earlier releases shipped them: positional answers, then numbered ones.
LEGACY_COMBINED_PROMPT_DIGESTS = {
    "c999920566be0c299241dd304f7a1ae1b694b20e011c24b4d0db727002e47911",  # positional
    "e945cca27bb22e56c13abb67efdb17154ec5ef53a822ac6b3c4386213cb1619d",  # numbered
}
LEGACY_ROWS_PROMPT_DIGESTS = {
    "963bd6bb59ada3f6921a9da1be81b22ed97debd1be4fa0005e6973f1a24da637",  # positional
    "1ac236b953aae7000ab83f6115d444245a27c171ce3cdd4e49b42d035dc0ff56",  # numbered
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr("app.services.paid_cache.access.paid_cache_for", lambda *_a, **_k: cache)
    set_ambient_cache(cache)
    yield memory
    set_ambient_cache(None)


def test_new_prompts_and_params_give_new_cache_keys() -> None:
    """The key covers the prompt text and the params, so both differ from what older answers were stored under."""
    from app.modules.parsers.excel.prompt_template import (
        keyed_row_text_prompt,
        table_enrichment_prompt,
    )

    assert sha256_hex(template_text(table_enrichment_prompt)) not in LEGACY_COMBINED_PROMPT_DIGESTS
    assert sha256_hex(template_text(keyed_row_text_prompt)) not in LEGACY_ROWS_PROMPT_DIGESTS
    legacy_first = {params_digest({"flow": "count_validated", "count": 50}), params_digest({"flow": "numbered_v2", "count": 50})}
    legacy_rows = {params_digest({"flow": "structured"}), params_digest({"flow": "numbered_v2"}), params_digest({})}
    assert params_digest({"flow": table_enrichment._FLOW_COMBINED, "count": 50}) not in legacy_first
    assert params_digest({"flow": table_enrichment._FLOW_ROWS}) not in legacy_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [KIND_TABLE_SUMMARY, KIND_TABLE_ROWS])
@pytest.mark.parametrize("legacy_shape", ["positional", "numbered"])
async def test_a_stored_older_answer_is_a_miss_never_read_as_keyed(store: MemoryResultStore, kind: str, legacy_shape: str) -> None:
    """Even at the very key a keyed answer would use, a positional or numbered payload is refused and replaced."""
    model = FakeTableModel()
    with record_in_flight("org-1", "rec-1"):
        await enrich(model, [HEAD, *make_rows(60)])
        first_calls = len(model.calls)
        (path,) = [p for p in store.results("org-1", "rec-1") if p.startswith(kind)]
        descriptions = ["positional one", "positional two"] if legacy_shape == "positional" else [
            {"row": 1, "description": "numbered one"}, {"row": 2, "description": "numbered two"},
        ]
        legacy = {"descriptions": descriptions}
        if kind == KIND_TABLE_SUMMARY:
            legacy |= {"summary": "S", "headers": HEAD, "header_row_count": 0}
        store.files[("org-1", "rec-1", path)] = (
            json.dumps({"v": 1, "kind": kind, "value": legacy}).encode(), store.now_ms,
        )
        result = await enrich(model, [HEAD, *make_rows(60)])

    assert len(model.calls) == first_calls + 1, "the legacy payload was a miss: the model was paid for again"
    assert not any("positional" in t or "numbered" in t for t in result.descriptions)
    assert_aligned(result, 60)


@pytest.mark.asyncio
async def test_a_keyed_answer_is_reused_from_the_cache(store: MemoryResultStore) -> None:
    model = FakeTableModel()
    with record_in_flight("org-1", "rec-1"):
        first = await enrich(model, [HEAD, *make_rows(60)])
        calls = len(model.calls)
        second = await enrich(model, [HEAD, *make_rows(60)])

    assert len(model.calls) == calls
    assert second == first


def test_the_strict_decoder_refuses_the_older_shapes_the_tolerant_model_would_empty() -> None:
    decode = table_enrichment._strict_decode_keyed(KeyedRowDescriptions)
    assert KeyedRowDescriptions.model_validate({"descriptions": ["a", "b"]}).descriptions == []  # tolerant by design
    for older in (["a", "b"], [{"row": 1, "description": "a"}]):
        with pytest.raises(ValueError, match="not keyed"):
            decode({"descriptions": older})
    with pytest.raises(ValueError, match="not keyed"):
        table_enrichment._strict_decode_keyed(TableEnrichment)({"summary": "s", "descriptions": "x"})
    assert decode({"descriptions": [{"key": "AB2C", "description": "a"}]}).descriptions[0].key == "AB2C"


# ---------------------------------------------------------------------------------------------------------
# Metrics and logs
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_rows_and_tables_are_counted_by_reason(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    small = [HEAD, *make_rows(8)]
    with track_indexing_enrichment(LOG) as counters, record_in_flight("org-1", "rec-9"):
        # count: the model answered but left rows out of a batch too small to split
        await enrich(FakeTableModel(by_first_row("REG4", "skip:0", kind="rows")), small, batch_size=4)  # 1 of 4 missing
        count_rows = counters.degraded_rows_by_reason["count"]
        # parse: no usable answer for a small batch
        await enrich(FakeTableModel(lambda k, _i, _n: "malformed" if k == "rows" else "ok"), small, batch_size=4)
        # error: the call raised
        await enrich(FakeTableModel(lambda _k, _i, _n: "raise"), small, batch_size=4)
        # budget: a failing provider on a big table exhausts the retry budget
        await enrich(FakeTableModel(lambda k, _i, _n: "nothing" if k == "rows" else "ok"), [HEAD, *make_rows(300)])

    assert count_rows == 1
    assert counters.degraded_rows_by_reason["parse"] == 4
    assert counters.degraded_rows_by_reason["error"] == 8, "a failed first call degrades the whole table"
    assert counters.degraded_rows_by_reason["budget"] > 0
    assert counters.degraded_tables == 4
    assert counters.degraded_tables_by_reason["error"] == 1
    assert sum(counters.degraded_tables_by_reason.values()) == 4
    assert counters.degraded_rows == sum(counters.degraded_rows_by_reason.values())

    batch_logs = [r.getMessage() for r in caplog.records if "row batch degraded" in r.getMessage()]
    assert batch_logs and all("record=rec-9" in m for m in batch_logs)
    assert any("reason=count" in m and "rows=4 described=3 fallback=1" in m for m in batch_logs)
    assert not any("REG" in r.getMessage() for r in caplog.records), "row text is never logged"


@pytest.mark.asyncio
async def test_a_clean_table_counts_nothing_degraded() -> None:
    with track_indexing_enrichment(LOG) as counters:
        await enrich(FakeTableModel())

    assert counters.degraded_rows == 0 and counters.degraded_tables == 0
    assert counters.tables_succeeded == 1


def test_the_enrichment_summary_line_reports_the_reasons(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with track_indexing_enrichment(LOG) as counters:
        counters.note_degraded_rows("parse", 3)
        counters.note_table(succeeded=False, degraded=True, reason="parse")

    line = next(r.getMessage() for r in caplog.records if "Indexing enrichment" in r.getMessage())
    assert "degraded_rows=3" in line and "degraded_tables_by_reason=parse=1" in line and "degraded_rows_by_reason=parse=3" in line


# ---------------------------------------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rows_text_is_keyed_and_falls_back_per_row() -> None:
    model = FakeTableModel(lambda _k, _i, _n: "skip:1")
    table = {"grid": [HEAD, *make_rows(4)]}

    descriptions, rows = await table_enrichment.get_rows_text(model, table, "A table", HEAD)

    assert descriptions == ["desc of REG0", "Name: REG1, Offset: 0x004, Reset: 0", "desc of REG2", "desc of REG3"]
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_get_rows_text_never_returns_a_dict_repr() -> None:
    model = FakeTableModel(lambda _k, _i, _n: "malformed")

    descriptions, _ = await table_enrichment.get_rows_text(model, {"grid": [HEAD, *make_rows(2)]}, "A table", HEAD)

    assert descriptions == ["Name: REG0, Offset: 0x000, Reset: 0", "Name: REG1, Offset: 0x004, Reset: 0"]


def test_format_rows_numbers_rows_from_one() -> None:
    text = indexing_helpers.format_rows_with_index([{"a": 1}, {"a": 2}])
    assert text.startswith("Row 1: ") and "\nRow 2: " in text


@pytest.mark.asyncio
async def test_rows_over_the_row_budget_are_counted_apart_and_the_table_is_not_degraded() -> None:
    model = FakeTableModel()
    with track_indexing_enrichment(LOG) as counters:
        result = await enrich(model, describe_rows=False)

    assert counters.rows_over_row_budget == 120
    assert counters.degraded_rows == 0 and counters.degraded_tables == 0
    assert counters.tables_succeeded == 1
    assert not result.degraded
    assert model.count("rows") == 0
    assert result.descriptions[5] == "Name: REG5, Offset: 0x014, Reset: 0"


def test_the_summary_line_reports_rows_over_the_row_budget(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with track_indexing_enrichment(LOG) as counters:
        counters.note_rows_over_budget(7)
        counters.note_table(succeeded=True)

    line = next(r.getMessage() for r in caplog.records if "Indexing enrichment" in r.getMessage())
    assert "rows_over_row_budget=7" in line


# ---------------------------------------------------------------------------------------------------------
# Odd entries, cancellation, env, and the rules that pin tolerance, salvage, caching and logging
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_model_that_also_describes_the_header_keeps_its_first_call_rows() -> None:
    model = FakeTableModel(header_row_count=1)

    result = await enrich(model, [HEAD, *make_rows(30)], known=None)

    assert model.calls == [("combined", 31)]
    assert not result.degraded
    assert_aligned(result, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["dictdesc:3"])
async def test_a_non_text_description_falls_back_for_that_row_alone(behavior: str) -> None:
    """A description that is an object used to be stored as its Python repr."""
    model = FakeTableModel(by_first_row(LATER, behavior, size=50))

    result = await enrich(model)

    assert result.descriptions[53] == "Name: REG53, Offset: 0x0D4, Reset: 0"
    assert result.descriptions[54] == "desc of REG54"
    assert len(model.calls) == 3, "one bad entry within the tolerance is not retried"
    assert not any("{'" in t for t in result.descriptions)


@pytest.mark.asyncio
async def test_a_sibling_half_is_cancelled_when_the_other_raises_a_propagated_error() -> None:
    """The other half's call used to run on after the load had ended."""
    from app.exceptions.non_retryable import NonRetryableError

    finished: list[str] = []

    class Slow(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            import asyncio

            ids = [rid for _, rid in self._rows(messages[-1].content)]
            if len(ids) == 6 and ids[0] == "REG6":
                raise NonRetryableError("refused")
            if len(ids) == 6:
                await asyncio.sleep(0.2)
                finished.append(ids[0])
            return await super().ainvoke(messages, **kwargs)

    model = Slow(lambda _k, ids, n: "nothing" if len(ids) == 12 else "ok")
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(12)]

    with pytest.raises(NonRetryableError):
        await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)
    import asyncio

    await asyncio.sleep(0.4)

    assert finished == [], "the first half's call must not complete after the load was refused"


def test_a_batch_size_below_one_uses_the_default_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """0 or a negative value used to mean one row per call."""
    from app.utils import concurrency

    monkeypatch.setattr(concurrency, "_warned_bad_batch_size", False, raising=False)
    caplog.set_level(logging.WARNING)
    for raw in ("0", "-3", "0", "-3"):
        monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", raw)
        assert table_row_batch_size() == 50

    warnings = [r for r in caplog.records if "INDEXING_TABLE_ROW_BATCH_SIZE" in r.getMessage()]
    assert len(warnings) == 1


# -- tolerance, salvage, caching and logging ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_five_missing_of_fifty_are_tolerated_but_six_are_split_and_retried() -> None:
    tolerated = FakeTableModel(by_first_row(LATER, "dropmany:5", size=50))
    split = FakeTableModel(by_first_row(LATER, "dropmany:6", size=50))

    result_a = await enrich(tolerated)
    result_b = await enrich(split)

    assert len(tolerated.calls) == 3 and result_a.degraded
    assert sorted(split.row_calls) == [20, 25, 25, 50] and not result_b.degraded
    assert_aligned(result_b)


@pytest.mark.asyncio
async def test_rows_a_failed_attempt_answered_are_kept_when_the_retry_fails(store: MemoryResultStore) -> None:
    calls: list[int] = []

    def behave(_kind: str, ids: list[str], _n: int) -> str:
        calls.append(len(ids))
        if len(calls) == 1:
            return "keepfirst:6"  # 6 of 12 answered: more than the tolerance
        return "nothing" if ids[0] == "REG0" else "ok"  # the retry of the answered half fails

    model = FakeTableModel(behave)
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(12)]

    texts = await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)

    assert calls == [12, 6, 6]
    assert texts[:6] == [f"desc of REG{i}" for i in range(6)], "the first attempt's answers are salvaged"
    assert texts[6:] == [f"desc of REG{i}" for i in range(6, 12)]


@pytest.mark.asyncio
async def test_an_incomplete_answer_is_not_cached_and_is_asked_for_again(store: MemoryResultStore) -> None:
    model = FakeTableModel(lambda _k, _i, _n: "skip:3")  # 1 of 30: tolerated, but incomplete
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(30)]
    with record_in_flight("org-1", "rec-1"):
        await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)
        await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)

    assert len(model.calls) == 2
    assert store.results("org-1", "rec-1") == []


@pytest.mark.asyncio
async def test_the_tolerance_path_logs_counts_and_never_row_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeTableModel(lambda _k, _i, _n: "skip:3")
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(30)]

    await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)

    assert any("reason=count" in r.getMessage() for r in caplog.records)
    assert not any("REG" in r.getMessage() or "0x" in r.getMessage() for r in caplog.records)




# ---------------------------------------------------------------------------------------------------------
# Row keys: opaque, unique, stable, and the only way a description finds its row
# ---------------------------------------------------------------------------------------------------------


def test_row_keys_are_four_base32_characters_unique_in_a_batch_and_stable() -> None:
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(500)]

    keys = table_enrichment.row_keys(rows)

    assert len(keys) == len(set(keys)) == 500
    assert all(re.fullmatch(r"[A-Z2-7]{4}", k) for k in keys)
    assert keys == table_enrichment.row_keys(rows), "the same batch always gets the same keys (stable prompt, stable cache entry)"
    assert keys != table_enrichment.row_keys([*rows[:499], {"Name": "other"}]), "another batch gets other keys"


def test_row_keys_carry_nothing_of_the_row_position() -> None:
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(50)]

    keys = table_enrichment.row_keys(rows)

    assert keys != sorted(keys), "keys are not in order, so they cannot be counted"
    assert keys != sorted(keys, reverse=True)
    assert set(keys).isdisjoint(str(i) for i in range(1, 51)), "no key is a row number"


def test_the_prompt_shows_each_row_next_to_its_key() -> None:
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(3)]
    keys = table_enrichment.row_keys(rows)

    plain = table_enrichment.format_rows_with_keys(rows, keys)
    numbered = table_enrichment.format_rows_with_keys(rows, keys, numbered=True)

    for i, key in enumerate(keys, 1):
        assert f"[{key}]: {{" in plain
        assert f"Row {i} [{key}]: {{" in numbered
    assert "Row 1" not in plain, "the rows prompt gives no number to count"


def test_alignment_matches_on_keys_only() -> None:
    keys = ["AAAA", "BBBB", "CCCC", "DDDD"]
    entries = [
        KeyedRowDescription(key="CCCC", description="three"),
        KeyedRowDescription(key="AAAA", description="one"),
        KeyedRowDescription(key="AAAA", description="one again"),
        KeyedRowDescription(key="ZZZZ", description="a key the batch never had"),
        KeyedRowDescription(key="BBBB", description="   "),
        KeyedRowDescription(key="2", description="a counted number"),
    ]

    assert table_enrichment.align_by_key(entries, keys) == ["one", None, "three", None]


def test_a_copied_key_survives_case_brackets_and_quotes() -> None:
    keys = ["AB2C", "DE3F"]
    entries = [KeyedRowDescription(key="[ab2c]", description="one"), KeyedRowDescription(key=' "de3f" ', description="two")]

    assert table_enrichment.align_by_key(entries, keys) == ["one", "two"]


def test_the_model_drops_entries_with_no_usable_key_or_text() -> None:
    parsed = KeyedRowDescriptions.model_validate_json(json.dumps({"descriptions": [
        "bare string", {"description": "no key"}, {"key": ["AB2C"], "description": "list key"}, {"key": True, "description": "bool"},
        {"key": "  ", "description": "blank key"}, {"key": "AB2C", "description": {"text": "object"}},
        {"key": 2345, "description": "integer key"}, {"key": "DE3F", "description": None},
    ]}))

    assert [(d.key, d.description) for d in parsed.descriptions] == [("2345", "integer key"), ("DE3F", "")]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["counted:-2", "counted:-1", "counted:0", "counted:1", "counted:2", "foreign"])
@pytest.mark.parametrize("where", ["first", "later"])
async def test_an_answer_that_counts_instead_of_copying_keys_never_shifts_a_row(fault: str, where: str) -> None:
    """A model that numbers its descriptions (from 0, from 1, one off, two off) carries keys the batch never had."""
    if where == "first":
        model = FakeTableModel(by_first_row("REG0", fault, kind="combined"))
    else:
        model = FakeTableModel(by_first_row(LATER, fault, size=50))

    result = await enrich(model)

    assert_aligned(result)
    assert result.summary == "Register map"


@pytest.mark.asyncio
async def test_a_model_that_skips_a_row_then_counts_the_rest_never_shifts() -> None:
    """Right keys for the first rows, then a skip, then plain numbers: the numbers match no key."""
    seen: list[int] = []

    def behave(kind: str, ids: list[str], _n: int) -> str:
        seen.append(len(ids))
        return "counted:0" if kind == "rows" and ids[0] == "REG50" else "ok"

    result = await enrich(FakeTableModel(behave))

    assert_aligned(result)


@pytest.mark.asyncio
async def test_a_small_batch_with_counted_numbers_falls_back_row_by_row() -> None:
    model = FakeTableModel(lambda kind, _i, _n: "counted:0" if kind == "rows" else "ok")

    result = await enrich(model, [HEAD, *make_rows(8)], batch_size=4)

    assert_aligned(result, 8, skip=frozenset(range(4, 8)))
    assert all(t.startswith("Name: REG") for t in result.descriptions[4:])


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["lowerkeys", "bracketed"])
async def test_keys_copied_with_small_changes_still_find_their_rows(behavior: str) -> None:
    model = FakeTableModel(lambda _k, _i, _n: behavior)

    result = await enrich(model)

    assert not result.degraded
    assert len(model.calls) == 3
    assert result.descriptions == [f"desc of REG{i}" for i in range(120)]


def _paraphrase(_rid: str) -> str:
    return "an entry of the table"


def _compare_with_previous(rid: str) -> str:
    n = int(rid[3:])
    return f"This register is a bit more than REG{n - 1}" if n else "The first register"


def _inflected_with_neighbour(rid: str) -> str:
    n = int(rid[3:])
    return f"testing stage number {n}, run after REG{n - 1}" if n else "the first stage"


@pytest.mark.asyncio
@pytest.mark.parametrize("describe", [_paraphrase, _compare_with_previous, _inflected_with_neighbour])
@pytest.mark.parametrize("rows", [2, 12, 50, 120])
async def test_correct_answers_are_kept_whatever_they_say_about_their_rows(describe: object, rows: int) -> None:
    """Descriptions that paraphrase their row or name a neighbour are still that row's description."""
    model = FakeTableModel(describe=describe)  # type: ignore[arg-type]

    result = await enrich(model, [HEAD, *make_rows(rows)])

    assert not result.degraded
    assert result.descriptions == [describe(f"REG{i}") for i in range(rows)]  # type: ignore[operator]
    assert len(model.calls) == -(-rows // 50) or (rows > 50 and len(model.calls) == -(-(rows - 50) // 50) + 1)


@pytest.mark.asyncio
async def test_unknown_headers_match_the_first_call_by_key_whether_or_not_the_header_is_described() -> None:
    describes_header = FakeTableModel(header_row_count=1)
    leaves_header = FakeTableModel(lambda kind, ids, _n: "skip:0" if kind == "combined" else "ok", header_row_count=1)

    for model in (describes_header, leaves_header):
        result = await enrich(model, [HEAD, *make_rows(30)], known=None)
        assert result.header_row_count == 1 and not result.degraded
        assert_aligned(result, 30)
        assert model.calls == [("combined", 31)]


@pytest.mark.asyncio
async def test_a_first_call_that_parses_with_the_wrong_keys_keeps_its_summary_and_describes_the_rows_again() -> None:
    model = FakeTableModel(by_first_row("REG0", "foreign", kind="combined"))

    result = await enrich(model)

    assert result.summary == "Register map"
    assert model.count("summary") == 0
    assert_aligned(result)
    assert result.descriptions[0] == "desc of REG0"


# ---------------------------------------------------------------------------------------------------------
# Worst case in model invocations, reflections included
# ---------------------------------------------------------------------------------------------------------


def _worst_case(rows: int) -> int:
    """2 x (first call + summary call + (K + 1) row batches + max(4, K + 3) retry calls), K = ceil((rows - 50) / 50)."""
    k = max(0, -(-(rows - 50) // 50))
    return 2 * (2 + (k + 1) + max(4, k + 3))


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [60, 120, 1000, 2000])
@pytest.mark.parametrize("first", ["malformed", "foreign", "ok"])
@pytest.mark.parametrize("known", [1, None])
async def test_invocations_per_table_stay_within_the_stated_worst_case(rows: int, first: str, known: int | None) -> None:
    """Every row call fails; the first call fails, answers with the wrong keys, or answers well."""

    def behave(kind: str, _ids: list[str], _n: int) -> str:
        return "malformed" if kind == "rows" else (first if kind == "combined" else "ok")

    model = FakeTableModel(behave, header_row_count=0 if known else 1)

    result = await enrich(model, [HEAD, *make_rows(rows)], known=known)

    assert model.invocations <= _worst_case(rows)
    assert len(result.descriptions) == rows
    assert result.degraded


# ---------------------------------------------------------------------------------------------------------
# A matrix of counting mistakes over differently shaped tables
# ---------------------------------------------------------------------------------------------------------


def _matrix_table(kind: str, n: int) -> tuple[list[str], list[list[str]]]:
    if kind == "rich":
        return ["Id", "Name", "Note"], [[f"K{i:03d}", f"name{i}", f"note{i}"] for i in range(n)]
    if kind == "numeric":  # every value under 3 characters
        return ["A", "B", "C"], [[str(i % 10), str(i // 10 % 10), str(i // 100)] for i in range(n)]
    if kind == "prose":  # every value over 64 characters
        return ["Text"], [["long text " * 7 + f"item {i}"] for i in range(n)]
    return ["Code", "Serial"], [[str(100 + i % 5), str(1000 + i)] for i in range(n)]  # repeated numbers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rich", "numeric", "prose", "repeated"])
@pytest.mark.parametrize("style", ["quote", "paraphrase"])
@pytest.mark.parametrize("fault", ["counted:-1", "counted:0", "counted:1", "counted:2", "partial:5", "partial:25", "partial:40", "partial:46", "skipcount:5", "skipcount:46"])
@pytest.mark.parametrize("where", ["combined", "rows"])
async def test_no_counting_mistake_moves_a_description_onto_another_row(kind: str, style: str, fault: str, where: str) -> None:
    """Whatever the cells look like and however the model miscounts, a row is never given another row's text.

    Each description carries a hidden marker ZZ<i> that is not a cell value, so a shift is measured
    even where nothing in the text could tell it apart.
    """
    head, rows = _matrix_table(kind, 120)
    index = {tuple(str(v) for v in r): i for i, r in enumerate(rows)}

    def identify(row: object) -> str:
        values = tuple(str(v) for v in (row.values() if isinstance(row, dict) else row))
        return f"ROW{index[values]}" if values in index else "HDR"

    def describe(rid: str) -> str:
        i = int(rid[3:])
        return f"ZZ{i}: " + (", ".join(rows[i]) if style == "quote" else "an entry of the table")

    def behave(k: str, ids: list[str], _n: int) -> str:
        return fault if k == where else "ok"

    model = FakeTableModel(behave, identify=identify, describe=describe)

    result = await table_enrichment.enrich_table_grid(model, [head, *rows], logger=LOG, known_header_row_count=1)

    assert len(result.descriptions) == 120
    for i, text in enumerate(result.descriptions):
        marks = [int(m) for m in re.findall(r"ZZ(\d+)", text)]
        assert marks in ([], [i]), f"row {i} got another row's description: {text[:60]!r}"


# ---------------------------------------------------------------------------------------------------------
# Keys a single typo cannot confuse, tolerated echo forms, and the redraw path
# ---------------------------------------------------------------------------------------------------------


def _distance(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize("rows", [50, 500])
@pytest.mark.parametrize("variant", range(5))
def test_no_two_keys_in_a_batch_are_one_character_apart(rows: int, variant: int) -> None:
    """A single-character copy typo must not land on another row's key."""
    batch = [{"v": f"{variant}-{i}"} for i in range(rows)]

    keys = table_enrichment.row_keys(batch)

    assert len(keys) == len(set(keys)) == rows
    assert min(_distance(a, b) for i, a in enumerate(keys) for b in keys[i + 1 :]) >= 2


def test_keys_stay_stable_across_calls_with_the_distance_rule() -> None:
    batch = [{"v": f"row-{i}"} for i in range(500)]

    assert table_enrichment.row_keys(batch) == table_enrichment.row_keys(batch)


def test_a_batch_whose_raw_key_stream_collides_still_gets_unique_keys() -> None:
    """These 500 rows draw the same key twice from the raw stream, so only the redraw keeps them unique."""
    batch = [{"v": f"row-{i}"} for i in range(500)]
    seed = table_enrichment._batch_seed(batch)
    raw = [table_enrichment._raw_key(seed, i) for i in range(500)]
    assert len(set(raw)) < 500, "the premise: the raw stream repeats a key within the first 500 draws"

    keys = table_enrichment.row_keys(batch)

    assert len(set(keys)) == 500


@pytest.mark.parametrize("echo", ["KEY: {k}", "key: {k}", "key={k}", "Row 3 [{k}]", "Row 12 [ {k} ]", "[{k}]", "id [{k}] (row 3)"])
def test_a_key_echoed_with_its_label_still_finds_its_row(echo: str) -> None:
    keys = ["AB2C", "DE3F"]
    entries = [KeyedRowDescription(key=echo.format(k="ab2c"), description="one"), KeyedRowDescription(key=echo.format(k="DE3F"), description="two")]

    assert table_enrichment.align_by_key(entries, keys) == ["one", "two"]


@pytest.mark.parametrize("echo", ["KEY: ZZZZ", "Row 3 [ZZZZ]", "row 3", "AB2", "AB2CD", "AB 2C", "the key AB2C"])
def test_an_echo_that_names_no_key_of_the_batch_drops_only_that_row(echo: str) -> None:
    keys = ["AB2C", "DE3F"]
    entries = [KeyedRowDescription(key=echo, description="bad"), KeyedRowDescription(key="DE3F", description="two")]

    assert table_enrichment.align_by_key(entries, keys) == [None, "two"]


# ---------------------------------------------------------------------------------------------------------
# Review follow-ups: key capacity, seed cost, a missing summary, row fallback versus a failed table
# ---------------------------------------------------------------------------------------------------------


def test_row_keys_refuse_more_rows_than_a_batch_can_hold() -> None:
    """With keys at least two apart there are at most 32**3 of them; past that the draw loop could never end."""
    too_many = [{"v": i} for i in range(table_enrichment.MAX_KEYED_ROWS + 1)]

    with pytest.raises(ValueError, match="at most"):
        table_enrichment.row_keys(too_many)


def test_row_keys_stop_drawing_when_the_key_space_is_used_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The draw loop is bounded even if the row cap is raised past what the key space can hold."""
    monkeypatch.setattr(table_enrichment, "MAX_KEYED_ROWS", 40_000)

    with pytest.raises(ValueError, match="key space"):
        table_enrichment.row_keys([{"v": i} for i in range(40_000)])


@pytest.mark.asyncio
async def test_an_explicit_batch_size_above_the_maximum_is_capped() -> None:
    model = FakeTableModel()

    result = await enrich(model, [HEAD, *make_rows(1200)], batch_size=100_000)

    assert max(n for _k, n in model.calls) <= table_enrichment.MAX_KEYED_ROWS
    assert not result.degraded and len(result.descriptions) == 1200


@pytest.mark.asyncio
async def test_a_caller_that_hands_over_more_rows_than_a_batch_has_them_split_not_refused() -> None:
    model = FakeTableModel()
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(1300)]

    texts = await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)

    assert texts == [f"desc of REG{i}" for i in range(1300)]
    assert max(model.row_calls) <= table_enrichment.MAX_KEYED_ROWS


@pytest.mark.asyncio
async def test_get_rows_text_splits_a_grid_past_the_maximum() -> None:
    model = FakeTableModel()

    texts, rows = await table_enrichment.get_rows_text(model, {"grid": [HEAD, *make_rows(1100)]}, "Register map", HEAD)

    assert len(texts) == len(rows) == 1100
    assert texts[1099] == "desc of REG1099"


def test_the_key_seed_reads_a_bounded_slice_of_each_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    """50 rows x 40 columns of 20,000 characters is 40 MB of cells; the keys must not serialise it."""
    big = "w" * 20_000
    rows = [{f"h{c}": big + str(r) for c in range(40)} for r in range(50)]
    hashed = count_hashed_bytes(monkeypatch)

    keys = table_enrichment.row_keys(rows)

    assert len(keys) == 50
    assert hashed[0] < 2_000_000, f"{hashed[0]} bytes were hashed"


def test_keys_are_stable_when_only_the_tail_of_a_huge_cell_differs() -> None:
    a = [{"v": "x" * 20_000 + "a"}]
    b = [{"v": "x" * 20_000 + "b"}]

    assert table_enrichment.row_keys(a) == table_enrichment.row_keys(b), "the seed reads a bounded slice"


def test_keys_do_not_depend_on_unlike_column_types() -> None:
    """A numeric column title next to a text one (a year header) used to raise TypeError from the sort."""
    rows = [{2024: "a", "Region": "b"}, {2024: "c", "Region": "d"}]

    keys = table_enrichment.row_keys(rows)

    assert len(set(keys)) == 2
    assert keys == table_enrichment.row_keys([{2024: "a", "Region": "b"}, {2024: "c", "Region": "d"}])
    assert keys != table_enrichment.row_keys([{"Region": "b", 2024: "a"}, {"Region": "d", 2024: "c"}]), "column order is part of the batch"


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [1, None])
async def test_a_reply_with_keyed_rows_and_a_header_but_no_summary_keeps_them_when_the_follow_up_fails(known: int | None) -> None:
    def behave(kind: str, _ids: list[str], _n: int) -> str:
        return "malformed" if kind == "summary" else "ok"

    model = FakeTableModel(behave, summary="", header_row_count=1 if known is None else 0)

    result = await enrich(model, [HEAD, *make_rows(120)], known=known)

    assert result.summary == "A table with 120 rows and columns: Name, Offset, Reset."
    assert result.headers == HEAD
    assert result.header_row_count == (1 if known is None else 1 if known else 0)
    assert len(result.descriptions) == 120
    assert result.descriptions == [f"desc of REG{i}" for i in range(120)], "the keyed rows of the first reply are kept"
    assert result.degraded and not result.failed
    assert model.count("rows") == 2, "only the rows after the first batch cost a call"


@pytest.mark.asyncio
async def test_a_summary_call_that_raises_keeps_the_first_replys_rows_too() -> None:
    def behave(kind: str, _ids: list[str], _n: int) -> str:
        return "raise" if kind == "summary" else "ok"

    model = FakeTableModel(behave, summary="")

    result = await enrich(model, [HEAD, *make_rows(60)])

    assert result.descriptions == [f"desc of REG{i}" for i in range(60)]
    assert result.degraded and not result.failed


@pytest.mark.asyncio
async def test_a_table_with_no_header_decision_to_keep_still_falls_back_as_a_whole() -> None:
    model = FakeTableModel(lambda _k, _i, _n: "malformed")

    result = await enrich(model, [HEAD, *make_rows(60)], known=None)

    assert result.failed and result.degraded


def _record_with_table(rows: int) -> tuple[object, object]:
    from app.models.blocks import (
        Block,
        BlockGroup,
        BlockGroupChildren,
        BlocksContainer,
        BlockType,
        DataFormat,
        GroupType,
        TableMetadata,
        TableRowMetadata,
    )

    blocks = [
        Block(
            id=f"b{i}", index=i, parent_index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
            data={"cells": make_rows(rows)[i], "row_natural_language_text": ""},
            table_row_metadata=TableRowMetadata(row_number=i + 1, is_header=False),
        )
        for i in range(rows)
    ]
    children = BlockGroupChildren()
    for block in blocks:
        children.add_block_index(block.index)
    group = BlockGroup(
        id="g", index=0, type=GroupType.TABLE, format=DataFormat.JSON,
        data={"table_markdown": "x", "column_headers": HEAD},
        table_metadata=TableMetadata(num_of_cols=3, has_header=False, column_names=HEAD), children=children,
    )
    return BlocksContainer(blocks=blocks, block_groups=[group]), group


@pytest.mark.asyncio
async def test_rows_that_fall_back_do_not_fail_the_record(caplog: pytest.LogCaptureFixture) -> None:
    """A one-table record whose later batches come back malformed used to raise 'All 1 tables failed'."""
    from unittest.mock import MagicMock

    caplog.set_level(logging.INFO)

    container, group = _record_with_table(120)
    model = FakeTableModel(lambda kind, _i, _n: "malformed" if kind == "rows" else "ok")

    stats = await table_enrichment.enhance_tables_with_llm(container, MagicMock(), LOG, llm=model)

    assert (stats.attempted, stats.succeeded, stats.failed) == (1, 1, 0)
    assert container.blocks[10].data["row_natural_language_text"] == "desc of REG10"
    assert container.blocks[100].data["row_natural_language_text"] == "Name: REG100, Offset: 0x190, Reset: 0"
    line = next(r.getMessage() for r in caplog.records if "Indexing enrichment (block-tables)" in r.getMessage())
    assert "degraded=1" in line and "degraded_rows=70" in line, "the fallback is still counted"


@pytest.mark.asyncio
async def test_a_table_whose_summary_and_first_call_both_fail_still_fails_the_record() -> None:
    from unittest.mock import MagicMock

    container, _group = _record_with_table(60)
    model = FakeTableModel(lambda _k, _i, _n: "malformed")

    with pytest.raises(RuntimeError, match="All 1 tables failed"):
        await table_enrichment.enhance_tables_with_llm(container, MagicMock(), LOG, llm=model)


@pytest.mark.asyncio
async def test_split_halves_run_in_the_callers_context() -> None:
    """Each half of a split batch is request child work: it sees the context (the org) of the caller."""
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="none")
    seen: list[str] = []

    class Watching(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            seen.append(marker.get())
            return await super().ainvoke(messages, **kwargs)

    model = Watching(lambda _k, ids, _n: "nothing" if len(ids) == 12 else "ok")
    rows = [dict(zip(HEAD, r, strict=True)) for r in make_rows(12)]
    marker.set("org-1")

    await table_enrichment.describe_row_dicts(model, rows, "Register map", LOG)

    assert seen == ["org-1", "org-1", "org-1"]


# ---------------------------------------------------------------------------------------------------------
# A deliberate stop (NonRetryableError) passes through every layer unchanged
# ---------------------------------------------------------------------------------------------------------


def _stops_on(where: str) -> object:
    def behave(kind: str, _ids: list[str], _n: int) -> str:
        return "nonretryable" if kind == where else "ok"

    return behave


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["rows", "combined", "summary"])
async def test_a_non_retryable_error_propagates_out_of_the_table_enrichment_unchanged(where: str) -> None:
    """The request can never succeed as sent: no plain-text fallback, no 'enriched' table."""
    from app.exceptions.non_retryable import NonRetryableError
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )

    stop = NonRetryableError("the request can never succeed as sent")
    if where == "summary":  # the follow-up is only asked for when the first reply has no summary
        model = FakeTableModel(_stops_on("summary"), summary="", stop=stop)  # type: ignore[arg-type]
    else:
        model = FakeTableModel(_stops_on(where), stop=stop)  # type: ignore[arg-type]

    with pytest.raises(NonRetryableError) as raised:
        await enrich(model)

    assert raised.value is stop, "the model's own exception, not a copy or a wrapper"
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["rows", "combined"])
async def test_a_non_retryable_error_propagates_out_of_enrich_tables_and_the_record(where: str) -> None:
    from unittest.mock import MagicMock

    from app.exceptions.non_retryable import NonRetryableError
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )

    grid = [HEAD, *make_rows(120)]
    with pytest.raises(NonRetryableError):
        await table_enrichment.enrich_tables(
            FakeTableModel(_stops_on(where)), [grid], logger=LOG, known_header_row_counts=[1],  # type: ignore[arg-type]
        )

    container, _group = _record_with_table(120)
    with pytest.raises(NonRetryableError) as raised:
        await table_enrichment.enhance_tables_with_llm(container, MagicMock(), LOG, llm=FakeTableModel(_stops_on(where)))  # type: ignore[arg-type]
    assert not isinstance(raised.value, RuntimeError), "not wrapped in 'All N tables failed'"
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL, "the record ends as non-retryable"


@pytest.mark.asyncio
async def test_a_non_retryable_error_cancels_the_sibling_batches_and_leaves_no_task_behind() -> None:
    import asyncio

    from app.exceptions.non_retryable import NonRetryableError

    finished: list[str] = []
    cleaned: list[str] = []
    sibling_started = asyncio.Event()

    class Slow(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            ids = [rid for _, rid in self._rows(messages[-1].content)]
            if ids and ids[0] == "REG50":
                await sibling_started.wait()  # the refusal arrives while a sibling is inside its call
                raise NonRetryableError("refused")
            if ids and ids[0] not in ("HDR", "REG0"):
                sibling_started.set()
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.05)  # cancellation cleanup that itself has to be awaited
                    cleaned.append(ids[0])
                    raise
                finished.append(ids[0])
            return await super().ainvoke(messages, **kwargs)

    before = asyncio.all_tasks()
    with pytest.raises(NonRetryableError):
        await enrich(Slow(), [HEAD, *make_rows(400)])
    # Looked at the moment the error reaches the caller, with no extra wait.
    assert cleaned, "a cancelled sibling's cleanup must have finished before the error left"
    assert asyncio.all_tasks() - before == set()

    await asyncio.sleep(0.2)
    assert finished == [], "batches still running when the stop arrived must not complete"


@pytest.mark.asyncio
async def test_a_non_retryable_error_in_one_table_cancels_the_other_tables() -> None:
    import asyncio
    from unittest.mock import MagicMock

    from app.exceptions.non_retryable import NonRetryableError

    finished: list[int] = []

    class Slow(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            await asyncio.sleep(0.05)
            ids = [rid for _, rid in self._rows(messages[-1].content)]
            if ids and ids[0] == "REG0" and len(ids) == 3:
                raise NonRetryableError("refused")
            await asyncio.sleep(0.3)
            finished.append(len(ids))
            return await super().ainvoke(messages, **kwargs)

    from app.models.blocks import BlocksContainer

    big, big_group = _record_with_table(40)
    small, small_group = _record_with_table(3)
    offset = len(big.blocks)
    for block in small.blocks:
        block.index += offset
        block.id = f"s{block.id}"
    for index in list(small_group.children.block_ranges):
        index.start += offset
        index.end += offset
    small_group.index = 1
    container = BlocksContainer(blocks=[*big.blocks, *small.blocks], block_groups=[big_group, small_group])

    with pytest.raises(NonRetryableError):
        await table_enrichment.enhance_tables_with_llm(container, MagicMock(), LOG, llm=Slow())
    await asyncio.sleep(0.6)

    assert finished == []


@pytest.mark.asyncio
async def test_get_rows_text_returns_all_texts_or_raises_never_a_partial_list() -> None:
    """One call per chunk of at most 500 rows, no retry: a call that fails after earlier chunks finished raises."""
    calls: list[int] = []

    def behave(_kind: str, ids: list[str], _n: int) -> str:
        calls.append(len(ids))
        return "raise" if len(calls) == 2 else "ok"

    model = FakeTableModel(behave)

    with pytest.raises(RuntimeError, match="provider down"):
        await table_enrichment.get_rows_text(model, {"grid": [HEAD, *make_rows(1100)]}, "Register map", HEAD)

    assert calls == [500, 500], "the third chunk is never asked for and the first chunk's texts are not returned"


@pytest.mark.asyncio
async def test_a_queued_job_is_not_started_after_a_stop() -> None:
    """With one slot, the job waiting for it must not start its call once the running job has stopped the record."""
    import asyncio

    from app.exceptions.non_retryable import NonRetryableError

    started: list[int] = []

    def job(i: int) -> object:
        async def run() -> int:
            started.append(i)
            await asyncio.sleep(0)
            if i == 0:
                raise NonRetryableError("refused")
            return i

        return run

    before = asyncio.all_tasks()
    with pytest.raises(NonRetryableError):
        await table_enrichment._gather_limited(1, [job(0), job(1), job(2)])  # type: ignore[list-item]
    await asyncio.sleep(0.05)

    assert started == [0]
    assert asyncio.all_tasks() - before == set()


@pytest.mark.asyncio
async def test_a_stop_in_one_tables_row_batch_keeps_the_records_queued_tables_from_starting() -> None:
    """While table A's row batches are still being cancelled, table B finishes and frees a slot of the
    record's run: queued table C must not start its call then."""
    import asyncio

    from app.exceptions.non_retryable import NonRetryableError

    a_sibling_started = asyncio.Event()
    refused = asyncio.Event()
    started: list[str] = []

    class Model(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            ids = [rid for _, rid in self._rows(messages[-1].content)]
            first = ids[0] if ids else ""
            started.append(first)
            if first == "REG50":
                await a_sibling_started.wait()
                refused.set()
                raise NonRetryableError("refused")
            if first == "REG100":
                a_sibling_started.set()
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.2)  # slow cleanup: table B finishes meanwhile
                    raise
            if first == "REG1000":
                await refused.wait()
            return await super().ainvoke(messages, **kwargs)

    table_a = [HEAD, *make_rows(120)]
    table_b = [HEAD, *[[f"REG{1000 + i}", "0x000", "0"] for i in range(3)]]
    table_c = [HEAD, *[[f"REG{2000 + i}", "0x000", "0"] for i in range(3)]]

    with pytest.raises(NonRetryableError):
        await table_enrichment.enrich_tables(
            Model(), [table_a, table_b, table_c], logger=LOG,  # type: ignore[arg-type]
            known_header_row_counts=[1, 1, 1], max_concurrent=2,
        )

    assert "REG1000" in started, "table B ran alongside table A"
    assert "REG2000" not in started, "a queued table must not start once a row batch has stopped the record"
