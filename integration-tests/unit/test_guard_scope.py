"""Every file the integration-suite guards read is one whose change runs them.

``integration-suite-guards.yml`` runs the guards only when a pull request changes
a file matching ``WATCHED_PATHS``, and reports green otherwise. A file the guards
read that the pattern misses (a helper a guard imports, the pytest config it runs
under) can then change with the check green and the guards never run. The list
is hand-written, so this runs the guards with every file they open recorded, and
fails on each repository file the pattern does not match.

A guard that starts reading a new file needs an edit to a file it already reads,
which is in scope, so this runs on the pull request that would leave it out.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github" / "workflows" / "integration-suite-guards.yml"

# Installed before pytest imports anything. A module imported from its cached
# .pyc never opens its source, so the cache path is mapped back to it.
_TRACER = textwrap.dedent("""
    import json, os, re, sys

    root, out, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    cached = re.compile(r"^(.*)/__pycache__/([^/.]+)\\.[^/]*\\.pyc$")
    opened = set()

    def record(event, event_args):
        if event != "open" or not event_args or not isinstance(event_args[0], (str, bytes, os.PathLike)):
            return
        path = os.path.realpath(os.fsdecode(event_args[0]))
        match = cached.match(path)
        if match:
            path = f"{match.group(1)}/{match.group(2)}.py"
        if path.startswith(root + os.sep):
            opened.add(os.path.relpath(path, root))

    sys.addaudithook(record)
    import pytest

    code = pytest.main(args)
    with open(out, "w") as f:
        json.dump({"exit": int(code), "opened": sorted(opened)}, f)
""")


def _workflow_env() -> tuple[str, list[str]]:
    steps = yaml.safe_load(_WORKFLOW.read_text())["jobs"]["integration-suite-guards"]["steps"]
    env: dict[str, str] = {}
    for step in steps:
        env.update(step.get("env") or {})
    return env["WATCHED_PATHS"], env["GUARDS"].split()


def _read_by_guards(guards: list[str], tmp_path: Path) -> dict:
    tracer = tmp_path / "tracer.py"
    tracer.write_text(_TRACER)
    out = tmp_path / "opened.json"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(_ROOT / "backend" / "python"), str(_ROOT / "integration-tests")]),
    }
    subprocess.run(
        [sys.executable, str(tracer), str(_ROOT), str(out), *guards,
         "--noconftest", "-c", "backend/python/pytest.ini", "-q", "-p", "no:cacheprovider"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=600, check=False,
    )
    return json.loads(out.read_text())


def _is_hidden(path: str) -> bool:
    return any(part.startswith(".") for part in Path(path).parts[:-1]) or Path(path).name.startswith(".")


def test_the_guard_list_names_files_that_exist() -> None:
    _pattern, guards = _workflow_env()
    assert guards
    assert [g for g in guards if not (_ROOT / g).is_file()] == []


# It runs every other guard, past the 30s per-test limit of backend/python/pytest.ini.
@pytest.mark.timeout(600)
def test_every_file_the_guards_read_is_watched(tmp_path: Path) -> None:
    pattern, guards = _workflow_env()
    others = [g for g in guards if Path(g).name != Path(__file__).name]

    run = _read_by_guards(others, tmp_path)

    # Guards that pass but read little would make this pass for the wrong reason.
    assert run["exit"] == 0, "the guards failed; run them to see why before trusting this check"
    watched = re.compile(pattern)
    missed = [p for p in run["opened"] if not _is_hidden(p) and not watched.search(p)]
    assert missed == [], (
        "integration-suite-guards would pass without running for a change to these, "
        "which the guards read. Add them to WATCHED_PATHS in "
        ".github/workflows/integration-suite-guards.yml:\n  " + "\n  ".join(missed)
    )
    assert any(p.startswith("integration-tests/helper/") for p in run["opened"])
