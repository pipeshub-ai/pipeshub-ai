"""``import app.capacity`` loads only the standard library, and not the resource governor."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[3]

SCRIPT = textwrap.dedent(
    """
    import sys
    import app  # the package's own init, which has its own imports
    before = set(sys.modules)
    import app.capacity, app.capacity.apply, app.capacity.cells, app.capacity.cgroup
    import app.capacity.facts, app.capacity.knobs, app.capacity.plan, app.capacity.policy
    import app.capacity.sources, app.capacity.__main__
    import app.utils.runtime_threads
    added = sorted(set(sys.modules) - before)
    foreign = [
        m for m in added
        if m.split(".")[0] not in sys.stdlib_module_names
        and not m.startswith(("app.capacity", "app.utils.runtime_threads", "__mp_main__"))
    ]
    print(foreign)
    """
)


def test_stdlib_only() -> None:
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", SCRIPT], cwd=PYTHON_ROOT, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PYTHON_ROOT)},
    )
    assert out.stdout.strip().splitlines()[-1] == "[]", f"loaded outside the standard library: {out.stdout}"


def test_does_not_import_the_resource_governor() -> None:
    script = "import sys, app.capacity.apply, app.capacity.cells; print(any(m.startswith('app.services') for m in sys.modules))"
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", script], cwd=PYTHON_ROOT, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PYTHON_ROOT)},
    )
    assert out.stdout.strip() == "False"
