"""C5: regenerating every target from every contract root produces no diff.

Python: one package per group (harness core, the PipesHub extension, the control plane). TypeScript: the
Node agent-harness and control-plane trees and the frontend's two trees. ``PXH_REQUIRE_NODE=1`` (CI) turns
a skipped TypeScript half into a failure, so the half that needs Node cannot silently stop running.
An edition's schema overlay, when present, generates beneath each group's own directory, so the same comparison
covers it; absent, it generates nothing (``test_c5_overlay_roots``).
"""

from __future__ import annotations

import filecmp
import importlib.util
import os
from typing import TYPE_CHECKING

import pytest

from .support import NODE_APP, REPO, generator, node_available

if TYPE_CHECKING:
    from pathlib import Path

GENERATE = generator()


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _assert_same(committed: Path, fresh: Path) -> None:
    a, b = _tree(committed), _tree(fresh)
    assert sorted(a) == sorted(b), f"generated file set differs under {committed}; run contracts/harness/generate.sh"
    stale = [name for name in a if a[name] != b[name]]
    assert stale == [], f"stale generated files under {committed} (run contracts/harness/generate.sh): {stale}"


def _ts_unavailable() -> str | None:
    reason = node_available()
    if reason is None and not (NODE_APP / "node_modules" / "json-schema-to-zod").exists():
        reason = "json-schema-to-zod missing (run npm ci)"
    return reason


def _skip_or_fail(reason: str) -> None:
    if os.environ.get("PXH_REQUIRE_NODE") == "1":
        pytest.fail(f"PXH_REQUIRE_NODE=1 but the TypeScript half cannot run: {reason}")
    pytest.skip(reason)


@pytest.mark.skipif(importlib.util.find_spec("datamodel_code_generator") is None, reason="datamodel-code-generator not installed")
def test_python_models_are_up_to_date(tmp_path: Path) -> None:
    GENERATE.main(["--skip-ts", "--skip-vectors", "--into", str(tmp_path)])
    assert len(GENERATE.GROUPS) == 3
    for group in GENERATE.GROUPS:
        _assert_same(REPO / group.python_out, tmp_path / group.python_out)


# Reverses the walk below the sorting wrapper, the order another file system may list the staged schemas in.
_REVERSED_WALK = (
    "import pathlib, runpy, sys\n"
    "walk = pathlib.Path.rglob\n"
    "pathlib.Path.rglob = lambda self, *a, **k: list(reversed(list(walk(self, *a, **k))))\n"
    "script = sys.argv.pop(1)\n"
    "runpy.run_path(script, run_name='__main__')\n"
)


@pytest.mark.skipif(importlib.util.find_spec("datamodel_code_generator") is None, reason="datamodel-code-generator not installed")
def test_python_models_do_not_depend_on_the_order_the_file_system_lists_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core and the control plane both have ``common.json``; datamodel-code-generator orders same-named files as
    the file system lists them, which once made CI regenerate five control-plane modules differently."""
    run = GENERATE.subprocess.run

    def reversed_walk(cmd: list[str], *args: object, **kwargs: object) -> object:
        script = next((c for c in cmd if str(c).endswith("datamodel_sorted.py")), None)
        if script is not None:
            cmd = [cmd[0], "-I", "-c", _REVERSED_WALK, script, *cmd[cmd.index(script) + 1 :]]
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr(GENERATE.subprocess, "run", reversed_walk)
    GENERATE.main(["--skip-ts", "--skip-vectors", "--into", str(tmp_path)])
    for group in GENERATE.GROUPS:
        _assert_same(REPO / group.python_out, tmp_path / group.python_out)


def test_typescript_targets_are_up_to_date(tmp_path: Path) -> None:
    reason = _ts_unavailable()
    if reason is not None:
        _skip_or_fail(reason)
    GENERATE.main(["--skip-python", "--skip-vectors", "--into", str(tmp_path)])
    outs = [target.out for target in GENERATE.TS_TARGETS]
    assert {o.split("/", 1)[0] for o in outs} == {"backend", "frontend"} and len(outs) == 4
    for out in outs:
        _assert_same(REPO / out, tmp_path / out)


def test_qa11_6_the_token_vector_modules_are_up_to_date(tmp_path: Path) -> None:
    """The token vector is the only list: its Python, frontend and Node modules are exactly what it generates."""
    GENERATE.main(["--skip-python", "--skip-ts", "--into", str(tmp_path)])
    for out in (GENERATE.VECTORS_PY_OUT, GENERATE.VECTORS_TS_OUT, GENERATE.VECTORS_NODE_OUT):
        _assert_same(REPO / out, tmp_path / out)


def test_frontend_targets_import_only_the_zod_3_api() -> None:
    imports = {
        line.strip()
        for target in GENERATE.TS_TARGETS
        if target.out.startswith("frontend/")
        for path in (REPO / target.out).rglob("*.ts")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("import ") and "zod" in line
    }
    assert imports, "the frontend targets import no zod at all? the check is broken"
    assert all(line.endswith(("\"zod/v3\"", "'zod/v3';")) for line in imports), sorted(imports)


def test_require_node_turns_a_skip_into_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PXH_REQUIRE_NODE", "1")
    with pytest.raises(pytest.fail.Exception, match="PXH_REQUIRE_NODE"):
        _skip_or_fail("node is not installed")
    monkeypatch.delenv("PXH_REQUIRE_NODE")
    with pytest.raises(pytest.skip.Exception):
        _skip_or_fail("node is not installed")


def test_drift_check_detects_a_change(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "m.py").write_text("x = 1\n")
    (tmp_path / "b" / "m.py").write_text("x = 2\n")
    assert not filecmp.cmp(tmp_path / "a" / "m.py", tmp_path / "b" / "m.py", shallow=False)
    with pytest.raises(AssertionError, match="stale"):
        _assert_same(tmp_path / "a", tmp_path / "b")
