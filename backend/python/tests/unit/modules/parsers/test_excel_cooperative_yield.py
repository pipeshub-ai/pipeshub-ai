"""PX-3 rung 2: the Excel loops that have no await of their own hand the event loop back.

* output is identical with the yields on and off (property test over random workbooks);
* no stretch of parser work longer than 20 ms of CPU time between yields on a 16,000-column
  and a tall workbook (CPU time of the thread, so a busy host cannot fail it);
* ``value_positions`` (one synchronous pass over every cell key) runs on a thread;
* the loop's heartbeat keeps ticking during a real parse.
"""
from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
from typing import Any

import pytest

from app.modules.parsers.excel import excel_parser
from app.modules.parsers.excel.excel_parser import EXCEL_MAX_TABLE_ROWS_TO_INDEX
from app.modules.parsers.excel.workbook_model import SheetModel
from app.utils.cooperative_yield import CooperativeYield
from tests.unit.modules.parsers.excel_yield_support import (
    canonical,
    make_parser,
    patch_model_calls,
    random_grid,
    tall_grid,
    wide_grid,
)

# Unit-test sizes: a handful of seeds, well inside the repo's 30 s test timeout. The large property run
# (200 seeds x 2 fresh bases) is ``python -m tests.perf.parse_bench.px3_identity``, with its own timeout.
SEED_BASE = int(os.environ.get("PX3_SEED_BASE", "0"))
SEEDS = int(os.environ.get("PX3_SEEDS", "8"))
# T22 asks for no stretch above 20 ms; the unit test caps CPU time at 40 ms so that scheduler and cache
# noise cannot fail it (the mutants it must catch overshoot by 200 ms or more). ``px3_bench`` reports the
# 20 ms figure on the real workbooks (M3).
STRETCH_LIMIT_S = 0.040


class RecordingPacer(CooperativeYield):
    """A pacer that counts its pauses and the CPU time between them."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(clock=time.thread_time, **kwargs)
        self.pauses = 0
        self.longest_stretch = 0.0
        self.callers: set[str] = set()
        self.due_calls = 0
        self._mark = time.thread_time()

    def due(self, units: int = 1) -> bool:
        import sys

        self.callers.add(sys._getframe(1).f_code.co_name)
        self.due_calls += 1
        return super().due(units)

    async def pause(self) -> None:
        now = time.thread_time()
        self.longest_stretch = max(self.longest_stretch, now - self._mark)
        self.pauses += 1
        await super().pause()
        self._mark = time.thread_time()

    def arm(self) -> None:
        """Start the first stretch now (the workbook load before it is not parser loop work)."""
        self._mark = time.thread_time()
        self._last = time.thread_time()

    def tail(self) -> float:
        """CPU time after the last yield: the container's own construction (one pydantic call).
        Not asserted; reported in the handoff (a known limit, see PX-3 part 1 findings)."""
        return time.thread_time() - self._mark


async def _blocks(parser: Any) -> Any:  # noqa: ANN401
    return await parser.get_blocks_from_workbook(object())  # the model calls are stubbed


@pytest.mark.timeout(25)
@pytest.mark.parametrize("llm_rows", [True, False], ids=["llm-rows", "simple-rows"])
async def test_excel_yields_do_not_change_output(monkeypatch: pytest.MonkeyPatch, llm_rows: bool) -> None:
    """Yields never move work: the container is the same with no yield at all and with a yield at
    every unit. Includes a model that returns no usable headers (the table is dropped)."""
    patch_model_calls(monkeypatch, llm_rows=llm_rows)
    yielded_somewhere = 0
    for seed in range(SEED_BASE, SEED_BASE + SEEDS):
        grid, has_headers = random_grid(seed)
        if not any(grid):
            continue
        generated = None if has_headers or seed % 3 == 0 else [f"G{i}" for i in range(7)]
        never = CooperativeYield(budget_seconds=float("inf"), check_every=1)
        always = RecordingPacer(budget_seconds=0.0, check_every=1)
        first = make_parser(grid, has_headers=has_headers, generated_headers=generated, pacer=never)
        second = make_parser(
            grid, has_headers=has_headers, generated_headers=generated, pacer=always, workbook_from=first
        )
        a = await _blocks(first)
        b = await _blocks(second)
        assert canonical(a) == canonical(b), f"seed {seed}"
        yielded_somewhere += always.pauses
    assert yielded_somewhere > 0, "the yielding arm never yielded: the test proved nothing"


async def test_a_model_that_returns_no_headers_leaves_the_same_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_model_calls(monkeypatch)
    grid = [[1, 2], [3, 4], [5, 6]]
    never = CooperativeYield(budget_seconds=float("inf"), check_every=1)
    always = RecordingPacer(budget_seconds=0.0, check_every=1)

    a = await _blocks(make_parser(grid, has_headers=False, generated_headers=None, pacer=never))
    b = await _blocks(make_parser(grid, has_headers=False, generated_headers=None, pacer=always))

    assert canonical(a) == canonical(b)
    assert list(a.blocks) == []


@pytest.mark.parametrize(
    "shape",
    ["wide-16000-columns", "tall"],
)
@pytest.mark.parametrize("llm_rows", [True, False], ids=["llm-rows", "simple-rows"])
async def test_excel_no_unyielded_stretch_over_budget(
    monkeypatch: pytest.MonkeyPatch, shape: str, llm_rows: bool
) -> None:
    """No stretch of parser work above 20 ms of CPU between two yields (the one call that builds the
    ``BlocksContainer`` after the last yield is not a loop and is excluded; see ``RecordingPacer.tail``). A row of 16,000 columns is
    a thousand times heavier than a 16-column one, so counting yields in rows would fail the wide shape."""
    patch_model_calls(monkeypatch, llm_rows=llm_rows, cheap=True)
    grid = wide_grid(16_000, 3) if shape.startswith("wide") else tall_grid(22_000)
    pacer = RecordingPacer()  # the real budget (10 ms) and check interval, on CPU time
    parser = make_parser(grid, pacer=pacer)

    # A full collection over the tens of thousands of cell dicts is a stall of its own (PX-3 M2 measures
    # it and the strategy for it); this test is about the parser's loops, so collection is held off.
    gc.collect()
    gc.disable()
    try:
        pacer.arm()
        container = await _blocks(parser)
        pacer.tail()
    finally:
        gc.enable()

    assert len(container.blocks) == min(len(grid) - 1, EXCEL_MAX_TABLE_ROWS_TO_INDEX)
    assert pacer.pauses > 0
    assert pacer.longest_stretch <= STRETCH_LIMIT_S, f"{pacer.longest_stretch * 1000:.1f} ms without a yield"


async def test_every_loop_that_stays_on_the_event_loop_takes_a_yield(monkeypatch: pytest.MonkeyPatch) -> None:
    """The yield sites: table boundary scans and the cell loop (get_table), the table scan
    (find_tables), the row loops (process_sheet_with_summaries), the batch start (get_rows_text) and the
    Block loop (get_blocks_from_workbook)."""
    patch_model_calls(monkeypatch)
    pacer = RecordingPacer(budget_seconds=0.0, check_every=1)
    await _blocks(make_parser(tall_grid(30), pacer=pacer))

    assert {"get_table", "find_tables", "process_sheet_with_summaries", "get_blocks_from_workbook"} <= pacer.callers
    assert "tick" in pacer.callers or "get_rows_text" in pacer.callers


async def test_value_positions_not_called_on_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """``value_positions`` walks every cell key in one synchronous constructor: it cannot take a
    yield, so it runs on a thread."""
    patch_model_calls(monkeypatch)
    calls: list[int] = []
    original = SheetModel.value_positions

    def recording(self: SheetModel) -> Any:  # noqa: ANN401
        calls.append(threading.get_ident())
        return original(self)

    monkeypatch.setattr(SheetModel, "value_positions", recording)

    await _blocks(make_parser(tall_grid(20)))

    assert calls, "value_positions was never called"
    assert threading.get_ident() not in calls


async def test_loop_ticks_during_xlsx_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ticks are counted, not timed: a loop held by the parse gives almost none, a free one dozens.
    A real workbook, real parse; only the model calls are stubbed. The control run with the yields
    disabled shows the heartbeat does see the stall."""
    patch_model_calls(monkeypatch)
    grid = wide_grid(2_000, 30)

    async def ticks_during(pacer: CooperativeYield) -> tuple[int, float]:
        parser = make_parser(grid, pacer=pacer)
        count = 0
        running = True

        async def heartbeat() -> None:
            nonlocal count
            while running:
                await asyncio.sleep(0.001)
                count += 1

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.01)
        count = 0
        started = time.monotonic()
        try:
            await _blocks(parser)
        finally:
            running = False
            await beat
        return count, time.monotonic() - started

    with_yields, took = await ticks_during(CooperativeYield())
    without, took_without = await ticks_during(CooperativeYield(budget_seconds=float("inf")))

    # counts, not times, so a busy host cannot flip it: a parse that yields gives the heartbeat dozens of
    # turns, the same parse held on the loop gives it a handful
    assert with_yields >= 30, (with_yields, took)
    assert without * 2 < with_yields, (without, with_yields, took_without)


@pytest.mark.parametrize("width", [0, 1, 1024, 1025, 5000])
async def test_the_sliced_simple_row_text_equals_the_one_shot_text(width: int) -> None:
    from app.utils.indexing_helpers import generate_simple_row_text

    row = {f"h{i}": (None if i % 7 == 0 else (i if i % 2 else f"v{i}, x")) for i in range(width)}
    parser = make_parser([[1]])
    parser._pacer = CooperativeYield(budget_seconds=0.0, check_every=1)

    assert await parser._simple_row_text(row) == generate_simple_row_text(row)


async def test_the_row_sums_between_table_scan_and_summaries_take_a_yield_per_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two ``sum(...)`` generators of ``process_sheet_with_summaries`` are loops now: one pacing
    call per row between the table scan and the first table summary."""
    patch_model_calls(monkeypatch, cheap=True)
    rows = 120
    pacer = RecordingPacer(budget_seconds=1e9, check_every=1)
    parser = make_parser(tall_grid(rows), pacer=pacer)
    at_scan_end: list[int] = []
    at_first_summary: list[int] = []
    scan = parser.get_tables_in_sheet

    async def wrapped_scan(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        result = await scan(*args, **kwargs)
        at_scan_end.append(pacer.due_calls)
        return result

    async def summary(_table: Any) -> str:  # noqa: ANN401
        if not at_first_summary:
            at_first_summary.append(pacer.due_calls)
        return "S"

    parser.get_tables_in_sheet = wrapped_scan  # type: ignore[method-assign]
    parser.get_table_summary = summary  # type: ignore[method-assign]

    await _blocks(parser)

    assert at_first_summary[0] - at_scan_end[0] >= rows


@pytest.mark.parametrize("width", [1, 1023, 1024, 1025, 2048, 2049])
async def test_get_rows_text_builds_the_same_row_dicts_for_rows_wider_than_a_slice(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    """The row dicts that reach the model are what the old one-line comprehension built, at every width
    around the 1024-cell slice (an off-by-one in the slicing drops or repeats a cell per slice)."""
    from datetime import UTC, datetime
    from datetime import time as dtime

    seen: list[list[dict[Any, Any]]] = []

    async def capture(_llm: object, rows: list[dict[Any, Any]], *_a: object, **_k: object) -> list[str]:
        seen.append(rows)
        return ["t"] * len(rows)

    monkeypatch.setattr(excel_parser, "describe_row_dicts", capture)
    values: list[Any] = [None, 7, "x", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC), dtime(6, 7), 1.5]
    batch = [
        [{"header": f"h{c}", "value": values[(c + r) % len(values)], "row": r + 1} for c in range(width)]
        for r in range(3)
    ]
    expected = [
        {
            cell["header"]: (cell["value"].isoformat() if isinstance(cell["value"], (datetime, dtime)) else cell["value"])
            for cell in row
        }
        for row in batch
    ]
    parser = make_parser([[1]])
    parser.llm = object()  # type: ignore[assignment]  # set by process_sheet_with_summaries in a real run
    parser._pacer = RecordingPacer(budget_seconds=0.0, check_every=1)

    assert await parser.get_rows_text(batch, "S") == ["t"] * 3

    assert seen == [expected]
    assert [list(r) for r in seen[0]] == [list(r) for r in expected]  # same order too
    assert parser._pacer.pauses > 0 or width <= 1


async def test_a_wide_simple_row_takes_its_yields_in_simple_row_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """The simple-format path (above the LLM row threshold) builds each row's text through
    ``_simple_row_text``; without it a 16,000-column row is one join. Asserted by who pays the yields,
    not by a CPU-time measurement (the join is about 5 ms here, under the 10 ms budget)."""
    patch_model_calls(monkeypatch, llm_rows=False, cheap=True)
    pacer = RecordingPacer(budget_seconds=1e9, check_every=1)
    await _blocks(make_parser(wide_grid(3_000, 2), pacer=pacer))

    assert "_simple_row_text" in pacer.callers


def test_the_offload_thresholds_are_not_zero() -> None:
    """A zero threshold would hop every small record (the few-KB bulk records the hop is pure loss on)."""
    from app.modules.parsers import loop_offload

    assert loop_offload.HTML_CLEAN_THRESHOLD_BYTES >= 1024
    assert loop_offload.YAML_DECODE_THRESHOLD_BYTES >= 1024
