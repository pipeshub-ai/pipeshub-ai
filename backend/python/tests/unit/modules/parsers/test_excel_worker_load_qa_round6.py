"""QA round 6 (MEM-6b): the load's CPU limit and signal handler are put back on every exit.

A load refused for memory leaves the worker's soft RLIMIT_CPU at (CPU used + bound + 2) and the SIGXCPU
handler installed: the memory cap is still in force when the CPU context's ``finally`` runs, so the
restore itself runs out of memory. The next job on that worker (any parse type) is then stopped by
``_CpuLimit`` once the process has used that much CPU.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.support.excel_workbooks import empty_cells, workbook_zip

BACKEND = Path(__file__).resolve().parents[4]

_SNIPPET = textwrap.dedent(
    """
    import resource, signal, sys
    from app.modules.parsers.excel.workbook_model import WorkbookRefused, load_workbook_model

    data = open(sys.argv[1], "rb").read()
    before = resource.getrlimit(resource.RLIMIT_CPU), signal.getsignal(signal.SIGXCPU)
    leaked = []
    for mb in (100, 120):
        try:
            load_workbook_model(data, mb * 1024 * 1024, 400 * 1024 * 1024, 480 * 1024 * 1024, 240)
        except WorkbookRefused:
            pass
        after = resource.getrlimit(resource.RLIMIT_CPU), signal.getsignal(signal.SIGXCPU)
        if after != before:
            leaked.append((mb, after[0]))
    print("LEAKED", leaked)
    """
)


@pytest.mark.timeout(180)
def test_a_load_refused_for_memory_puts_back_the_cpu_limit_and_handler(tmp_path: Path) -> None:
    # 1 M numeric cells: a load capped at 100 or 120 MB is refused for memory with the cap still in force
    body = "<sheetData>" + empty_cells(20000, 50, lambda ref: f'<c r="{ref}"><v>1.5</v></c>') + "</sheetData>"
    path = tmp_path / "cells.xlsx"
    path.write_bytes(workbook_zip(body))
    run = subprocess.run(
        [sys.executable, "-c", _SNIPPET, str(path)],
        cwd=BACKEND, capture_output=True, text=True, timeout=600, check=True,
    )
    assert run.stdout.strip().endswith("LEAKED []"), run.stdout
