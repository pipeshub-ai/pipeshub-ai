"""Jobs shipped to a parse worker by tests (the worker can import this module by name)."""

from __future__ import annotations

from app.modules.parsers.excel.workbook_model import (
    WorkbookRefused,
    load_workbook_model,
)

MB = 1024 * 1024


def after_a_big_job_then_load(data: bytes, limit: int, cap: int) -> str:
    """Runs in the worker: a job that peaked at 200 MB and gave it back, then a load that fails for its own reasons."""
    big = bytearray(200 * MB)
    for i in range(0, len(big), 4096):
        big[i] = 1
    del big
    try:
        load_workbook_model(data, limit, cap)
    except WorkbookRefused as refused:
        return f"refused:{refused.reason}"
    except Exception as exc:
        return f"error:{type(exc).__name__}"
    return "loaded"
