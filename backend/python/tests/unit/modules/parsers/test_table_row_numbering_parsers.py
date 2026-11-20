"""Excel and CSV row descriptions are matched by row number too.

The same fake model and scenarios as ``tests/unit/utils/test_table_row_descriptions.py``, run
through each parser's own ``get_rows_text``: a skipped, merged, duplicated or empty answer loses
at most its own rows and never gives a row another row's description. All data is synthetic.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.prompt_template import keyed_row_text_prompt
from app.services.paid_cache.access import set_ambient_cache
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import (
    KIND_TABLE_ROWS,
    params_digest,
    sha256_hex,
    template_text,
)
from app.utils.indexing_metrics import track_indexing_enrichment
from tests.support.paid_cache import MemoryResultStore, record_in_flight
from tests.support.table_model import FakeTableModel, assert_aligned, make_rows

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

LOG = logging.getLogger("test-table-row-numbering-parsers")
ROWS = 12
KEYS = ["Name", "Offset", "Reset"]

# sha256 of template_text(...) of the row prompts these parsers used before: the positional one, then the numbered one.
LEGACY_ROWS_PROMPT_DIGESTS = {
    "3841a151cd90f51d960ae9df937caa96cf644d8dca74df091dd02446dfeda056",
    "1ac236b953aae7000ab83f6115d444245a27c171ce3cdd4e49b42d035dc0ff56",
}


def csv_rows(count: int = ROWS) -> list[dict[str, str]]:
    return [dict(zip(KEYS, row, strict=True)) for row in make_rows(count)]


def excel_rows(count: int = ROWS) -> list[list[dict[str, object]]]:
    return [
        [{"header": key, "value": value, "row": i + 2} for key, value in zip(KEYS, row, strict=True)]
        for i, row in enumerate(make_rows(count))
    ]


async def csv_describe(model: FakeTableModel, count: int = ROWS) -> list[str]:
    return await CSVParser(MagicMock()).get_rows_text(model, csv_rows(count), "Register map", batch_size=50)


async def excel_describe(model: FakeTableModel, count: int = ROWS) -> list[str]:
    parser = ExcelParser(LOG, MagicMock())
    parser.llm = model  # type: ignore[assignment]
    return await parser.get_rows_text(excel_rows(count), "Register map")


@pytest.fixture(autouse=True)
def _pinned_row_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batches of 50 rows and a row budget of 1000, whatever the environment sets."""
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", "50")
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "1000")


PARSERS = pytest.mark.parametrize("describe", [csv_describe, excel_describe], ids=["csv", "excel"])


@PARSERS
@pytest.mark.asyncio
async def test_a_clean_answer_costs_one_call(describe: Callable) -> None:
    model = FakeTableModel()

    texts = await describe(model)

    assert texts == [f"desc of REG{i}" for i in range(ROWS)]
    assert model.calls == [("rows", ROWS)]


@PARSERS
@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["skip:3", "merge:3", "dup:3", "nothing", "malformed", "reverse", "long", "oor"])
async def test_a_bad_answer_never_gives_a_row_another_rows_description(describe: Callable, behavior: str) -> None:
    # The first call is bad, the retried halves answer well, so a bad batch is recovered by splitting.
    answered = []

    def behave(_kind: str, ids: list[str], _n: int) -> str:
        answered.append(len(ids))
        return behavior if len(answered) == 1 else "ok"

    texts = await describe(FakeTableModel(behave))

    merged = frozenset({3}) if behavior == "merge:3" else frozenset()
    assert_aligned(texts, ROWS, skip=merged)
    if behavior == "dup:3":
        assert not any("WRONG" in t for t in texts), "the first answer for a number must win"


@PARSERS
@pytest.mark.asyncio
async def test_a_skipped_row_falls_back_alone_and_the_next_rows_keep_their_own_text(describe: Callable) -> None:
    model = FakeTableModel(lambda _k, _i, _n: "skip:3")

    texts = await describe(model, 30)  # 1 of 30 missing: within the tolerance, so no retry

    assert model.calls == [("rows", 30)]
    assert texts[3] == "Name: REG3, Offset: 0x00C, Reset: 0"
    assert texts[4] == "desc of REG4"
    assert_aligned(texts, 30, skip=frozenset({3}))


@PARSERS
@pytest.mark.asyncio
async def test_an_empty_answer_is_split_in_half_and_retried(describe: Callable) -> None:
    calls = []

    def behave(_kind: str, ids: list[str], _n: int) -> str:
        calls.append(len(ids))
        return "nothing" if len(calls) == 1 else "ok"

    texts = await describe(FakeTableModel(behave))

    assert calls == [12, 6, 6]
    assert texts == [f"desc of REG{i}" for i in range(ROWS)]


@PARSERS
@pytest.mark.asyncio
async def test_a_model_that_never_answers_gives_column_value_text_never_a_dict_repr(describe: Callable) -> None:
    model = FakeTableModel(lambda _k, _i, _n: "malformed")

    with track_indexing_enrichment(LOG) as counters:
        texts = await describe(model)

    assert texts == [f"Name: REG{i}, Offset: 0x{i * 4:03X}, Reset: 0" for i in range(ROWS)]
    assert counters.degraded_rows == ROWS
    assert counters.degraded_rows_by_reason["parse"] + counters.degraded_rows_by_reason["budget"] == ROWS
    assert max(model.row_calls) == ROWS and min(model.row_calls) >= 5


@PARSERS
@pytest.mark.asyncio
async def test_a_failed_call_degrades_with_reason_error(describe: Callable) -> None:
    with track_indexing_enrichment(LOG) as counters:
        texts = await describe(FakeTableModel(lambda _k, _i, _n: "raise"))

    assert len(texts) == ROWS
    assert counters.degraded_rows_by_reason["error"] == ROWS


@pytest.mark.asyncio
async def test_the_csv_parser_shares_one_retry_budget_across_a_tables_batches() -> None:
    parser = CSVParser(MagicMock())
    model = FakeTableModel(lambda _k, _i, _n: "nothing")
    rows = csv_rows(200)

    texts = await parser.get_rows_text(model, rows, "Register map", batch_size=50)

    # 4 batches: budget max(4, 4 + 1) = 5 retry calls (2 per split) -> at most 2 splits
    assert len(model.row_calls) <= 4 + 5
    assert len(texts) == 200


@pytest.mark.asyncio
async def test_excel_refuses_through_the_output_budget_instead_of_degrading() -> None:
    from app.modules.parsers.excel.excel_parser import _OutputBudget

    parser = ExcelParser(LOG, MagicMock())
    parser.llm = FakeTableModel()  # type: ignore[assignment]
    parser._output = _OutputBudget(1)
    long_rows = [[{"header": "Name", "value": "x" * 500, "row": 2}]]

    with pytest.raises(Exception, match="output budget|too large|EXCEL"):
        await parser.get_rows_text(long_rows, "Register map")


# -- Paid-cache separation for these paths ------------------------------------------------------------------


def test_the_new_prompt_and_params_give_these_paths_new_cache_keys() -> None:
    assert sha256_hex(template_text(keyed_row_text_prompt)) not in LEGACY_ROWS_PROMPT_DIGESTS
    # The parsers first cached with no params, then under the numbered flow; the keyed flow is tagged.
    from app.utils import table_enrichment

    assert params_digest({"flow": table_enrichment._FLOW_ROWS}) not in {params_digest({}), params_digest({"flow": "numbered_v2"})}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr("app.services.paid_cache.access.paid_cache_for", lambda *_a, **_k: cache)
    set_ambient_cache(cache)
    yield memory
    set_ambient_cache(None)


@PARSERS
@pytest.mark.asyncio
@pytest.mark.parametrize("older", [["positional"] * ROWS, [{"row": i + 1, "description": "numbered"} for i in range(ROWS)]], ids=["positional", "numbered"])
async def test_a_stored_older_answer_is_a_miss_for_these_paths(store: MemoryResultStore, describe: Callable, older: list) -> None:
    model = FakeTableModel()
    with record_in_flight("org-1", "rec-1"):
        await describe(model)
        (path,) = store.results("org-1", "rec-1")
        store.files[("org-1", "rec-1", path)] = (
            json.dumps({"v": 1, "kind": KIND_TABLE_ROWS, "value": {"descriptions": older}}).encode(),
            store.now_ms,
        )
        texts = await describe(model)
        await describe(model)

    assert len(model.calls) == 2, "the older payload was a miss and was replaced; the next call is a hit"
    assert "positional" not in " ".join(texts) and "numbered" not in " ".join(texts)
    assert texts == [f"desc of REG{i}" for i in range(ROWS)]


@PARSERS
@pytest.mark.asyncio
async def test_a_keyed_answer_is_reused_by_a_retry(store: MemoryResultStore, describe: Callable) -> None:
    model = FakeTableModel()
    with record_in_flight("org-1", "rec-1"):
        first = await describe(model)
        second = await describe(model)

    assert first == second and len(model.calls) == 1


# -- Review follow-ups: a retry budget owned by each table, and column titles of unlike types ---------------


@pytest.mark.asyncio
async def test_concurrent_csv_tables_on_one_parser_each_spend_their_own_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parsing service shares one CSVParser; two tables in flight must not read each other's budget."""
    import asyncio
    from unittest.mock import AsyncMock

    from app.modules.parsers.csv import csv_parser
    from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

    seen: dict[str, set[int]] = {"A": set(), "B": set()}
    budgets: dict[int, object] = {}

    async def describe(_llm: object, rows: list[dict], _summary: str, _logger: object, *, budget: object = None, **_kw: object) -> list[str]:
        await asyncio.sleep(0)
        which = "A" if str(rows[0]["Name"]).startswith("A") else "B"
        seen[which].add(id(budget))
        budgets[id(budget)] = budget
        return ["t"] * len(rows)

    monkeypatch.setattr(csv_parser, "describe_row_dicts", describe)
    parser = CSVParser(MagicMock())
    detection = CSVHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")
    parser.detect_headers_with_llm = AsyncMock(return_value=detection)  # type: ignore[method-assign]
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]

    def table(prefix: str, n: int) -> dict:
        return {"raw_rows": [["Name"], *[[f"{prefix}{i}"] for i in range(n)]], "start_row": 1}

    await asyncio.gather(
        parser.get_blocks_from_csv_with_multiple_tables([table("A", 120)], AsyncMock()),
        parser.get_blocks_from_csv_with_multiple_tables([table("B", 400)], AsyncMock()),
    )

    assert len(seen["A"]) == 1 and len(seen["B"]) == 1 and seen["A"] != seen["B"]
    (a,) = seen["A"]
    (b,) = seen["B"]
    assert budgets[a] is not budgets[b]


@pytest.mark.asyncio
async def test_a_standalone_get_rows_text_does_not_inherit_the_budget_of_an_earlier_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.csv import csv_parser
    from app.utils.table_enrichment import RetryBudget

    got: list[object] = []

    async def describe(_llm: object, rows: list[dict], _summary: str, _logger: object, *, budget: object = None, **_kw: object) -> list[str]:
        got.append(budget)
        return ["t"] * len(rows)

    monkeypatch.setattr(csv_parser, "describe_row_dicts", describe)
    parser = CSVParser(MagicMock())
    spent = RetryBudget(0)

    await parser.get_rows_text(FakeTableModel(), csv_rows(3), "S", retry_budget=spent)
    await parser.get_rows_text(FakeTableModel(), csv_rows(3), "S")

    assert got[0] is spent
    assert got[1] is not spent and got[1].calls > 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_excel_batches_of_one_table_share_one_budget_and_other_documents_do_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.excel import excel_parser

    seen: list[object] = []

    async def describe(_llm: object, rows: list[dict], _summary: str, _logger: object, *, budget: object = None, **_kw: object) -> list[str]:
        seen.append(budget)
        return ["t"] * len(rows)

    monkeypatch.setattr(excel_parser, "describe_row_dicts", describe)
    parser = ExcelParser(LOG, MagicMock())
    parser.llm = FakeTableModel()  # type: ignore[assignment]
    first = excel_rows(3)

    await parser.get_rows_text(first, "S")
    await parser.get_rows_text(first, "S")

    assert seen[0] is not seen[1], "a standalone call gets a budget of its own, not the previous table's"


@pytest.mark.asyncio
async def test_a_numeric_column_title_next_to_a_text_one_indexes_through_the_real_excel_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A year header (2024) beside "Region" gave row dicts with keys of two types, and the key seed sorted them."""
    from unittest.mock import AsyncMock

    from app.modules.parsers.excel.prompt_template import ExcelHeaderDetection
    from tests.support.excel_workbooks import load, workbook_zip

    rows = (
        '<row r="1"><c r="A1"><v>2024</v></c><c r="B1" t="inlineStr"><is><t>Region</t></is></c></row>'
        + "".join(
            f'<row r="{r}"><c r="A{r}"><v>{r * 10}</v></c><c r="B{r}" t="inlineStr"><is><t>REG{r - 2}</t></is></c></row>'
            for r in range(2, 8)
        )
    )
    parser = load(workbook_zip(f"<sheetData>{rows}</sheetData>"))
    parser.logger = LOG  # type: ignore[assignment]
    parser.detect_excel_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=ExcelHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")
    )
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]
    model = FakeTableModel()

    result = await parser.get_blocks_from_workbook(model)

    texts = [b.data["row_natural_language_text"] for b in result.blocks if b.type.value == "table_row"]
    assert texts == [f"desc of REG{i}" for i in range(6)]


@pytest.mark.asyncio
async def test_a_non_retryable_error_from_the_model_ends_a_csv_table_unchanged() -> None:
    from unittest.mock import AsyncMock

    from app.exceptions.non_retryable import NonRetryableError
    from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

    parser = CSVParser(MagicMock())
    parser.detect_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=CSVHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")
    )
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]
    table = {"raw_rows": [["Name"], *[[f"REG{i}"] for i in range(120)]], "start_row": 1}

    with pytest.raises(NonRetryableError):
        await parser.get_blocks_from_csv_with_multiple_tables([table], FakeTableModel(lambda _k, _i, _n: "nonretryable"))


@PARSERS
@pytest.mark.asyncio
async def test_a_non_retryable_error_from_the_model_passes_through_each_parser(describe: Callable) -> None:
    from app.exceptions.non_retryable import NonRetryableError

    with pytest.raises(NonRetryableError):
        await describe(FakeTableModel(lambda _k, _i, _n: "nonretryable"))


@pytest.mark.asyncio
async def test_no_model_call_completes_after_a_stop_in_a_csv_table() -> None:
    """The batches of a CSV table run side by side; once one is refused the others are cancelled, not left to finish."""
    import asyncio
    from unittest.mock import AsyncMock

    from app.exceptions.non_retryable import NonRetryableError
    from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

    finished: list[str] = []
    cleaned: list[str] = []
    sibling_started = asyncio.Event()

    class Slow(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            ids = [rid for _, rid in self._rows(messages[-1].content)]
            if ids[0] == "REG50":
                await sibling_started.wait()  # the refusal arrives while a sibling is inside its call
                raise NonRetryableError("refused")
            sibling_started.set()
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)  # cancellation cleanup that itself has to be awaited
                cleaned.append(ids[0])
                raise
            finished.append(ids[0])
            return await super().ainvoke(messages, **kwargs)

    parser = CSVParser(MagicMock())
    parser.detect_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=CSVHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")
    )
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]
    table = {"raw_rows": [["Name"], *[[f"REG{i}"] for i in range(300)]], "start_row": 1}

    before = asyncio.all_tasks()
    with pytest.raises(NonRetryableError):
        await parser.get_blocks_from_csv_with_multiple_tables([table], Slow())
    # Looked at the moment the error leaves the parser, with no extra wait.
    assert cleaned, "a cancelled sibling's cleanup must have finished before the error left"
    assert asyncio.all_tasks() - before == set()

    await asyncio.sleep(0.2)
    assert finished == [], "cancelled batches never finish normally"


@pytest.mark.asyncio
@pytest.mark.parametrize("stall_s", [0.0, 0.4], ids=["idle_loop", "loop_stalled_after_the_refusal_is_raised"])
async def test_no_task_is_pending_when_a_stop_leaves_an_excel_table(monkeypatch: pytest.MonkeyPatch, stall_s: float) -> None:
    """Excel cancels the batches still running and waits for them before the error leaves.

    No batch finishes on a timer here: a sibling that would complete after a fixed delay finishes when the event loop
    is stalled (a busy runner) for longer than that delay, with the refusal and the sibling's timer both due in one loop
    pass, so "cancelled batches never finish" held only on a quiet machine. The siblings wait on an event nobody sets
    until the test ends, and the refusal is raised once one of them is inside its call; ``stall_s`` blocks the loop
    right before the refusal is raised, which used to be enough to make the old timer-based sibling finish."""
    import asyncio
    import time
    from unittest.mock import AsyncMock

    from app.exceptions.non_retryable import NonRetryableError
    from app.modules.parsers.excel.prompt_template import ExcelHeaderDetection
    from tests.support.excel_workbooks import load, workbook_zip

    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c></row>' + "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>REG{r - 2}</t></is></c></row>' for r in range(2, 302)
    )
    finished: list[str] = []
    cleaned: list[str] = []
    sibling_started = asyncio.Event()
    never = asyncio.Event()  # set only by the test's own cleanup, after the assertions

    class Slow(FakeTableModel):
        async def ainvoke(self, messages: list, **kwargs: object) -> object:
            ids = [rid for _, rid in self._rows(messages[-1].content)]
            if ids[0] == "REG50":
                await sibling_started.wait()  # the refusal arrives while a sibling is inside its call
                time.sleep(stall_s)  # the stall under test: a loop that was busy elsewhere
                raise NonRetryableError("refused")
            sibling_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                cleaned.append(ids[0])
                raise
            finished.append(ids[0])
            return await super().ainvoke(messages, **kwargs)

    parser = load(workbook_zip(f"<sheetData>{rows}</sheetData>"))
    parser.logger = LOG  # type: ignore[assignment]
    parser.detect_excel_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=ExcelHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")
    )
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]

    before = asyncio.all_tasks()
    try:
        with pytest.raises(NonRetryableError):
            await parser.get_blocks_from_workbook(Slow())
        pending = asyncio.all_tasks() - before  # looked at the moment the error leaves, with no extra wait
        assert cleaned, "a cancelled sibling must have been cancelled before the error left"
        assert pending == set()
    finally:
        never.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert finished == [], "cancelled batches never finish normally"
