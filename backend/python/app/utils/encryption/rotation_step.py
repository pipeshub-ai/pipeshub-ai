"""What every Python step of the ``SECRET_KEY`` rotation shares (ADR-KEY-01).

Node's ``rotate-secret-key`` command runs each step as ``python -m <module> [--apply]`` in its own environment and
reads the last line of its stdout: one JSON object of counts (``RekeyReport``), or ``{"step", "error"}``. Never a
value or a key.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence


@dataclass(slots=True)
class RekeyReport:
    step: str
    total: int = 0
    current: int = 0
    previous: int = 0
    moved: int = 0
    unreadable: int = 0
    draining: int = 0


def parse_apply(prog: str, description: str | None, argv: Sequence[str]) -> bool:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    _ = parser.add_argument("--apply", action="store_true", help="rewrite what is under the previous key")
    return bool(parser.parse_args(list(argv)).apply)


async def report_step(
    step: str,
    work: Callable[[], Awaitable[RekeyReport]],
    out: TextIO,
    own: Callable[[Exception], bool],
) -> int:
    """Runs ``work`` and writes its one line: the counts, or the error. Only messages ``own`` vouches for are
    printed; any other error by type (``describe_error``), since a store's error can quote what it was given."""
    try:
        report = await work()
    except Exception as exc:
        _ = out.write(json.dumps({"step": step, "error": str(exc) if own(exc) else describe_error(exc)}) + "\n")
        return 1
    _ = out.write(json.dumps(asdict(report), sort_keys=True) + "\n")
    return 0


def run_cli(main: Callable[[TextIO], Awaitable[int]]) -> int:
    """The process entry: stdout holds the report line and nothing else, as the Node step reads it.

    The shared loggers write to stdout, and one of them can write after the report (the configuration cache's
    invalidation listener did, KEYRING-FU-1: the step "printed no report"). So the report goes to a copy of the
    original stdout and descriptor 1, which every such handler writes through, becomes stderr for the run."""
    sys.stdout.flush()
    report = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    try:
        return asyncio.run(main(report))
    finally:
        report.flush()


def describe_error(exc: BaseException) -> str:
    """An error by type; an ``OSError`` also by errno, the call that raised it and the kind of path, so an operator
    can act on it (KEYRING-FU-1: a bare ``OSError`` hid a read-only working directory). Never the path or a value."""
    if not isinstance(exc, OSError):
        return type(exc).__name__
    frames = traceback.extract_tb(exc.__traceback__)
    operation = f"{_module_of(frames[-1].filename)}.{frames[-1].name}" if frames else "an operation"
    reason = errno.errorcode.get(exc.errno or 0, "no errno")
    if exc.filename is None:
        where = "no path"
    elif os.path.isabs(os.fsdecode(exc.filename)):
        where = "an absolute path"
    else:
        where = "a path under the working directory"
    return f"{type(exc).__name__} {reason} in {operation} on {where}"


def _module_of(filename: str) -> str:
    # Python 3.11+ names a frozen stdlib module's frames "<frozen os>".
    if filename.startswith("<frozen ") and filename.endswith(">"):
        return filename[len("<frozen ") : -1]
    return Path(filename).stem


__all__ = ["RekeyReport", "describe_error", "parse_apply", "report_step", "run_cli"]
