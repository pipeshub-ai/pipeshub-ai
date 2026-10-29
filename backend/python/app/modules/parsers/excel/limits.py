"""The numbers that bound one Excel document, in one place.

Imported by the Excel parser, the worker's loader and the resource governor, so what the parser
may hold and what the governor assumes a heavy parse holds cannot drift apart. Standard library and
``env_config`` only.
"""
from __future__ import annotations

from app.utils.env_config import env_positive_int

# Parent memory per cell dict the parser builds (measured), and how many times the text bound the
# parent's output budget is. The parser charges cells by the first and sizes its budget by the
# second; the merged-range cap in the loader is derived from the same two numbers.
CELL_DICT_BYTES = 1106
OUTPUT_BUDGET_FACTOR = 3

_MB = 1024 * 1024


def excel_max_text_bytes() -> int:
    """``EXCEL_MAX_TEXT_MB`` (default 480) in bytes; read each call."""
    return env_positive_int("EXCEL_MAX_TEXT_MB", 480) * _MB


def excel_load_max_bytes() -> int:
    """``EXCEL_LOAD_MAX_MB`` (default 1536) in bytes: how far the loading worker may grow."""
    return env_positive_int("EXCEL_LOAD_MAX_MB", 1536) * _MB


def excel_document_memory_bytes() -> int:
    """The most one Excel document holds above the base process: the larger of its two phases.

    The worker loads first (up to ``EXCEL_LOAD_MAX_MB``) and ends before the parent builds blocks
    (up to the output budget, ``OUTPUT_BUDGET_FACTOR`` x the text bound), so the document's peak is
    the larger of the two, not their sum. The governor sizes its heavy-parse gate from this.
    """
    return max(excel_load_max_bytes(), OUTPUT_BUDGET_FACTOR * excel_max_text_bytes())


# Seconds a workbook may spend loading (CPU in the worker, wall clock in the pool). Memory does not
# bound everything openpyxl does: ranges that overlap, hyperlinks on merged cells and the like spend
# unbounded time on a flat amount of memory. Measured loads of the heaviest workbooks the memory
# limit admits (2.9 M numeric cells, 1.3 GB of worker): 72 s on a host with a load average of 5; a
# merged-range sheet exactly at the position cap: 36 s. The default is more than three times the
# slowest, and an explicit EXCEL_LOAD_MAX_SECONDS wins.
DEFAULT_LOAD_MAX_SECONDS = 240


def excel_load_max_seconds() -> int:
    """``EXCEL_LOAD_MAX_SECONDS`` (default 240); read each call."""
    return env_positive_int("EXCEL_LOAD_MAX_SECONDS", DEFAULT_LOAD_MAX_SECONDS)
