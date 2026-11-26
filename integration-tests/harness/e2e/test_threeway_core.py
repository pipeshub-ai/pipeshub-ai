"""The 3-wise trust-core array's rows (04 §4.4), one test per row, parametrized by ``<stack>-<row id>``."""

from __future__ import annotations

from rows import run_row

ARRAY = "threeway-core"


def test_row(row, stacks, live_run) -> None:  # noqa: ANN001 - the conftest's row, stacks and live opt-in
    run_row(row, stacks, live_run)
