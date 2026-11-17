"""Every provider's SDK puts exactly one request on the wire for one call built for indexing (counted at the transports).

The independent check behind the call-site contract: it does not read client attributes. ``tests/support/sdk_wire_probe.py``
patches the HTTP transports in its own interpreter, builds every chat and embeddings provider through the factories with
``sdk_retries=0`` and counts what each puts on the wire for a 500, a 429 and a dropped connection. Nothing leaves the process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(900)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("status", ["500", "429", "0"])
def test_every_provider_sends_one_request_per_call(status: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AWS_EC2_METADATA_DISABLED": "true"}
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tests" / "support" / "sdk_wire_probe.py"), status],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=800,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith(("OK", "UNSEEN", "SDK-RETRIES", "BUILD-ERR", "bad="))]
    assert "bad=0" in lines, "\n".join(lines) + result.stderr[-2000:]
    assert not any(line.startswith("BUILD-ERR") for line in lines), "\n".join(lines)
    ok = [line for line in lines if line.startswith("OK")]
    assert len(ok) >= 30, f"only {len(ok)} provider rows were measured:\n" + "\n".join(lines)
