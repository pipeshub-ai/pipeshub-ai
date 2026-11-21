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


def spike_then_fail(data: bytes, limit: int, cap: int, hold_seconds: float) -> str:
    """Runs in the worker: the load grows to 90 % of the allowance for *hold_seconds*, gives it back, then fails for its own reasons."""
    import time

    from app.modules.parsers.excel import workbook_model

    def extract(_data: bytes) -> object:
        spike = bytearray(int(limit * 0.9))  # address space counts against the cap at once
        time.sleep(hold_seconds)
        del spike
        raise TypeError("a half-built range")

    workbook_model._extract = extract
    try:
        load_workbook_model(data, limit, cap)
    except WorkbookRefused as refused:
        return f"refused:{refused.reason}"
    except Exception as exc:
        return f"error:{type(exc).__name__}"
    return "loaded"


def wedged_at_the_cap(data: bytes, limit: int, cap: int) -> str:
    """Runs in the worker: the load fills its allowance, then sits inside one C call that runs no
    signal handler, as openpyxl can at the cap (R60-FLAKE)."""
    import contextlib
    import hashlib

    from app.modules.parsers.excel import workbook_model

    def extract(_data: bytes) -> object:
        held: list[bytearray] = []
        with contextlib.suppress(MemoryError):
            while True:
                held.append(bytearray(256 * 1024))
        held.pop()  # room for the call itself, still well inside the pool's margin
        hashlib.pbkdf2_hmac("sha256", b"k", b"s", 10**9)  # minutes, never back in the eval loop
        raise AssertionError("the wedge ended on its own")

    workbook_model._extract = extract
    load_workbook_model(data, limit, cap)
    return "loaded"
