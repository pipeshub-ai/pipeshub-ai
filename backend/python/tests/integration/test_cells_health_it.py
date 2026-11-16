"""Two uvicorn workers answer the same merged ``/health`` body, and a killed cell turns it into a 503."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("CELLS_IT_PORT", "19101"))
pytestmark = pytest.mark.integration


def _get(path: str = "/health") -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait(predicate, timeout: float = 30.0):  # noqa: ANN202
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            got = predicate()
            if got:
                return got
        except OSError:
            pass
        time.sleep(0.3)
    raise AssertionError("condition not reached")


def test_two_workers_serve_the_same_merged_body_and_a_dead_cell_is_503(tmp_path: Path) -> None:
    env = {**os.environ, "CELLS_IT_DIR": str(tmp_path), "CELLS_IT_WORKERS": "2", "PYTHONPATH": str(PYTHON_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-W", "ignore", "-m", "uvicorn", "tests.support.cells_health_app:app", "--workers", "2",
         "--port", str(PORT), "--host", "127.0.0.1",
         # The supervisor replaces a worker that misses its health check (5 s by default); the stopped cell
         # must stay in place for the whole observation window below.
         "--timeout-worker-healthcheck", "60"],
        cwd=PYTHON_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait(lambda: _get()[0] == 200)
        bodies = {json.dumps(_get()[1], sort_keys=True) for _ in range(30)}
        assert len(bodies) == 1, f"workers answered different bodies: {bodies}"
        cells = json.loads(bodies.pop())["cells"]
        assert [c["cell"] for c in cells] == [0, 1]
        child = cells[1]["pid"]
        os.kill(child, signal.SIGSTOP)  # alive, lock held, file goes stale
        try:
            _wait(lambda: _get()[0] == 503, timeout=30)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGCONT)
    finally:
        proc.terminate()
        proc.wait(timeout=20)
