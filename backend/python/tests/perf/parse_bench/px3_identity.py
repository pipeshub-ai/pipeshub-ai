"""Large output-identity run for the Excel cooperative yields (PX-3 rung 2): the property test at scale.

    python -m tests.perf.parse_bench.px3_identity --seeds 200 --base 777000 [--timeout 900]

For every seed a random workbook (one to three tables, blanks, text and numbers, a model that sometimes
returns no headers) is parsed twice, once with a pacer that never yields and once with a yield at every
unit, in both row modes (model-written rows and simple rows); the two containers must be equal after id
normalisation. The unit test ``test_excel_yields_do_not_change_output`` runs 8 seeds inside the repo's
30 s test timeout; this is the 200-seed x fresh-base run the QA step and the runbook use. It has its own
overall deadline (``--timeout``) and exits 1 on the first mismatch (printing the seed) and 2 on timeout.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from unittest.mock import patch

from app.modules.parsers.excel import excel_parser
from app.utils.cooperative_yield import CooperativeYield
from tests.unit.modules.parsers.excel_yield_support import (
    canonical,
    cheap_describe_row_dicts,  # noqa: F401  (re-exported for the runbook's stretch variant)
    fake_describe_row_dicts,
    make_parser,
    random_grid,
)


class _Counting(CooperativeYield):
    __slots__ = ("count",)

    def __init__(self, **kwargs: float) -> None:
        super().__init__(**kwargs)
        self.count = 0

    async def pause(self) -> None:
        self.count += 1
        await super().pause()


async def _check(seeds: int, base: int) -> int:
    pauses = 0
    for llm_rows in (True, False):
        with (
            patch.object(excel_parser, "describe_row_dicts", fake_describe_row_dicts),
            patch.object(excel_parser, "max_table_rows_for_llm", lambda llm_rows=llm_rows: 10**9 if llm_rows else 0),
        ):
            for seed in range(base, base + seeds):
                grid, has_headers = random_grid(seed)
                if not any(grid):
                    continue
                generated = None if has_headers or seed % 3 == 0 else [f"G{i}" for i in range(7)]
                never = CooperativeYield(budget_seconds=float("inf"), check_every=1)
                always = _Counting(budget_seconds=0.0, check_every=1)
                first = make_parser(grid, has_headers=has_headers, generated_headers=generated, pacer=never)
                second = make_parser(
                    grid, has_headers=has_headers, generated_headers=generated, pacer=always, workbook_from=first
                )
                a = await first.get_blocks_from_workbook(object())  # type: ignore[arg-type]
                b = await second.get_blocks_from_workbook(object())  # type: ignore[arg-type]
                if canonical(a) != canonical(b):
                    print(f"MISMATCH seed={seed} llm_rows={llm_rows}", file=sys.stderr)
                    return 1
                pauses += always.count
    print(f"ok: {seeds} seeds x 2 row modes from base {base}, {pauses} yields taken")
    return 0 if pauses else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--base", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=900.0, help="overall deadline in seconds")
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    started = time.monotonic()
    try:
        rc = asyncio.run(asyncio.wait_for(_check(args.seeds, args.base), args.timeout))
    except asyncio.TimeoutError:
        print(f"timeout after {args.timeout}s", file=sys.stderr)
        return 2
    print(f"{time.monotonic() - started:.1f}s", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
