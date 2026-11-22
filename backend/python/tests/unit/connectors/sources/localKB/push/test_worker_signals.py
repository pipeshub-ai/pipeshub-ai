"""The push worker imports on platforms without SIGBUS (Windows), so the connector service starts there."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

from app.connectors.sources.localKB.push import worker as W

BACKEND_ROOT = Path(__file__).resolve().parents[6]


def test_worker_imports_without_sigbus() -> None:
    # A fresh interpreter, so removing SIGBUS cannot leak into other tests.
    script = (
        "import signal\n"
        "if hasattr(signal, 'SIGBUS'):\n"
        "    del signal.SIGBUS\n"
        "from app.connectors.sources.localKB.push import worker\n"
        "print(sorted(int(s) for s in worker._DEATH_SIGNALS))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(sorted([int(signal.SIGABRT), int(signal.SIGSEGV)]))


def test_death_signals_include_sigbus_where_defined() -> None:
    expected = {signal.SIGABRT, signal.SIGSEGV}
    if hasattr(signal, "SIGBUS"):
        expected.add(signal.SIGBUS)
    assert W._DEATH_SIGNALS == frozenset(expected)
