"""Shared helpers for the Excel cooperative-yield tests: hand-built workbooks, a parser whose model
calls are stubbed, and one canonical dump of what it produces."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.modules.parsers.excel import excel_parser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.prompt_template import ExcelHeaderDetection
from tests.perf.parse_bench import golden
from tests.support.excel_workbooks import column_letters, load, workbook_zip

if TYPE_CHECKING:
    from app.utils.cooperative_yield import CooperativeYield

LOG = logging.getLogger("excel-yield-test")
LOG.setLevel(logging.WARNING)  # the parser logs per table at INFO; captured logs dominate a property run


def _cell(ref: str, value: Any) -> str:  # noqa: ANN401
    if value is None:
        return ""
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    return f'<c r="{ref}"><v>{value}</v></c>'


def sheet_xml(grid: list[list[Any]]) -> str:
    rows = []
    for r, values in enumerate(grid, 1):
        cells = "".join(_cell(f"{column_letters(c)}{r}", v) for c, v in enumerate(values, 1))
        if cells:
            rows.append(f'<row r="{r}">{cells}</row>')
    return f"<sheetData>{''.join(rows)}</sheetData>"


def wide_grid(columns: int, data_rows: int) -> list[list[Any]]:
    """One header row and *data_rows* rows, *columns* wide: a row is as heavy as 16,000 cells."""
    return [[f"h{c}" for c in range(columns)]] + [[r * columns + c for c in range(columns)] for r in range(data_rows)]


def tall_grid(rows: int, columns: int = 3) -> list[list[Any]]:
    return [[f"h{c}" for c in range(columns)]] + [[r + c for c in range(columns)] for r in range(rows)]


def random_grid(seed: int) -> tuple[list[list[Any]], bool]:
    """A workbook with one to three tables separated by blank rows and columns, blanks and text inside.

    Returns the grid and whether the stubbed model should say it has headers.
    """
    rng = random.Random(seed)
    grid: list[list[Any]] = []
    columns = rng.randint(1, 7)
    has_headers = rng.random() < 0.7
    for _table in range(rng.randint(1, 3)):
        width = rng.randint(1, columns)
        offset = rng.randint(0, 2)
        for r in range(rng.randint(2, 30)):
            row: list[Any] = [None] * offset
            for c in range(width):
                if r == 0 and has_headers:
                    row.append(f"H{c}")
                elif rng.random() < 0.15:
                    row.append(None)
                elif rng.random() < 0.5:
                    row.append(rng.randint(-5, 10_000))
                else:
                    row.append(f"v{rng.randint(0, 99)}")
            grid.append(row)
        grid.extend([] for _ in range(rng.randint(1, 3)))
    return grid, has_headers


def make_parser(
    grid: list[list[Any]],
    *,
    has_headers: bool = True,
    generated_headers: list[str] | None = None,
    pacer: CooperativeYield | None = None,
    llm_rows: bool = True,
    workbook_from: ExcelParser | None = None,
) -> ExcelParser:
    """A parser over *grid* with its model calls stubbed.

    Loading goes through the parse pool (a worker process, a few hundred ms); *workbook_from* shares
    an already loaded workbook (read-only for the parse) so a comparison of two pacers loads once.
    """
    if workbook_from is not None:
        parser = ExcelParser(LOG, MagicMock())
        parser.workbook = workbook_from.workbook
        parser.file_binary = workbook_from.file_binary
    else:
        parser = load(workbook_zip(sheet_xml(grid)))
    parser.logger = LOG  # type: ignore[assignment]
    parser.detect_excel_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=ExcelHeaderDetection(
            has_headers=has_headers, num_header_rows=1 if has_headers else 0, confidence="high", reasoning="r"
        )
    )
    # None: the model gives back no usable headers, so the table is dropped.
    parser.generate_excel_headers_with_llm = AsyncMock(return_value=generated_headers or [])  # type: ignore[method-assign]
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]
    if pacer is not None:
        parser._pacer = pacer
    return parser


async def fake_describe_row_dicts(_llm: object, rows: list[dict[str, Any]], *_a: object, **_k: object) -> list[str]:
    return [json.dumps([[repr(k), v] for k, v in row.items()], default=str) for row in rows]


async def cheap_describe_row_dicts(_llm: object, rows: list[dict[str, Any]], *_a: object, **_k: object) -> list[str]:
    """Constant-time per row: the stretch tests measure the parser's own loops, not a stub's json.dumps."""
    return ["row"] * len(rows)


def patch_model_calls(monkeypatch: Any, *, llm_rows: bool = True, cheap: bool = False) -> None:  # noqa: ANN401
    monkeypatch.setattr(excel_parser, "describe_row_dicts", cheap_describe_row_dicts if cheap else fake_describe_row_dicts)
    monkeypatch.setattr(excel_parser, "max_table_rows_for_llm", lambda: 10**9 if llm_rows else 0)


def canonical(container: Any) -> str:  # noqa: ANN401
    return golden.canonicalize(json.loads(container.model_dump_json()))


def run(coro: Any) -> Any:  # noqa: ANN401
    return asyncio.run(coro)
