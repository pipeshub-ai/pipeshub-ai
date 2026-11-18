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
