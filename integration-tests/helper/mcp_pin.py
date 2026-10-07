"""The ``@pipeshub-ai/mcp`` version the Node backend serves on ``/mcp``."""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_PACKAGE_JSON = _REPO_ROOT / "backend" / "nodejs" / "apps" / "package.json"


def mcp_package_pin() -> str:
    """Exact pin, e.g. ``"2.3.3"``. A range would make the golden file ambiguous."""
    deps = json.loads(BACKEND_PACKAGE_JSON.read_text())["dependencies"]
    pin = deps["@pipeshub-ai/mcp"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", pin):
        raise RuntimeError(
            f"@pipeshub-ai/mcp must be an exact pin so the golden is unambiguous; got {pin!r}"
        )
    return pin

