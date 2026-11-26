"""QA20-28 (CPD-8 FB-3): a route module that reads seams from ``app.edition_config`` imports on its own.

``edition_config`` re-exported these routers at module load while their modules imported resolvers from it, so
entering through the route module (as ``patch("app.api.routes.toolsets...")`` or a single test file does) raised an
ImportError on the partially initialized module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# A hang guard, not a bound: the test imports the app in a subprocess, which a loaded host slows.
HEAVY_TIMEOUT_S = 180

PYTHON = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module", ["app.api.routes.toolsets", "app.api.routes.search", "app.connectors.api.router", "app.edition_config"]
)
@pytest.mark.timeout(HEAVY_TIMEOUT_S)
def test_the_module_imports_first_and_the_edition_reexports_its_router(module: str) -> None:
    code = (
        f"import {module}\n"
        "import importlib\n"
        "import app.edition_config as edition\n"
        # The router each name maps to, as the edition's own table says, not a fixed module.
        "for name in ('toolsets_router', 'search_router'):\n"
        "    assert getattr(edition, name) is importlib.import_module(edition._LAZY_ATTRS[name]).router, name\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=PYTHON, timeout=300
    )
    assert done.returncode == 0, done.stderr[-2000:]
