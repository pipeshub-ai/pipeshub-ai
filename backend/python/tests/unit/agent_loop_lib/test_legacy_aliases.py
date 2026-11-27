"""Modules moved to ``harness_core`` stay importable, identical, under their old paths."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agent_loop_lib.runtime.runtime import AgentRuntime

LIB = Path(__file__).resolve().parents[3] / "app" / "agent_loop_lib"
_TARGET = re.compile(r"^\"\"\"Legacy import path; this (?:module|package) lives in ``([\w.]+)``")


def _aliases() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(LIB.rglob("*.py")):
        match = _TARGET.match(path.read_text(encoding="utf-8"))
        if match:
            rel = path.relative_to(LIB.parents[1]).with_suffix("")
            parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
            found.append((".".join(parts), match.group(1)))
    return found


ALIASES = _aliases()


def test_every_moved_module_has_an_alias() -> None:
    assert len(ALIASES) >= 80


@pytest.mark.parametrize(("old", "new"), ALIASES, ids=[old for old, _ in ALIASES])
def test_old_path_resolves_to_the_moved_module(old: str, new: str) -> None:
    moved = importlib.import_module(new)
    legacy = importlib.import_module(old)
    if legacy.__name__ == new:
        assert legacy is moved
    else:
        # A package alias re-exports the moved package's namespace.
        public = [name for name in vars(moved) if not name.startswith("__")]
        assert all(getattr(legacy, name) is getattr(moved, name) for name in public)


# Imports the package aliases first, then every submodule of the moved packages by its new path: the order a run
# that never touches the old paths leaves behind, which made the parity check above depend on what ran before it.
_LATE_SUBMODULES = """
import importlib, pkgutil, sys
pairs = [(old, new) for old, new in {pairs!r} if importlib.import_module(old).__name__ != new]
for old, new in pairs:
    moved = importlib.import_module(new)
    for info in pkgutil.iter_modules(moved.__path__, new + "."):
        importlib.import_module(info.name)
    legacy = sys.modules[old]
    missing = [n for n in vars(moved) if not n.startswith("__") and getattr(legacy, n, None) is not getattr(moved, n)]
    assert not missing, (old, missing)
print(len(pairs))
"""


def test_a_package_alias_sees_submodules_imported_after_it_by_their_new_path() -> None:
    packages = [(old, new) for old, new in ALIASES if (LIB.parents[1] / old.replace(".", "/") / "__init__.py").exists()]
    done = subprocess.run(
        [sys.executable, "-c", _LATE_SUBMODULES.format(pairs=packages)],
        cwd=LIB.parents[1],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert int(done.stdout.split()[-1]) >= 2


def test_legacy_runtime_derives_the_ag_ui_aliases() -> None:
    runtime = AgentRuntime()
    assert runtime.derive_events is not None
    event = AgentEvent(
        event_type=EventType.TOOL_BLOCKED,
        run_context=RunContext(role_name="r", model="m"),
        payload={"x": 1},
    )

    derived = runtime.derive_events(event)

    assert [(e.event_type, e.payload) for e in derived] == [(EventType.TOOL_CALL_END, {"x": 1})]

