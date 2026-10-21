"""Each route module that edition_config serves must import on its own, before edition_config.

The full suite imports edition_config early, which hides a loop between the two, so each
import runs in a fresh interpreter.
"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["app.api.routes.search", "app.api.routes.toolsets"])
def test_the_route_module_imports_first(module: str) -> None:
    code = f"import {module}; print('imported')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1:] == ["imported"], out.stderr[-2000:]
