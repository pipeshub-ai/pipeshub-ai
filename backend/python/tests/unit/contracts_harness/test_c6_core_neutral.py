"""C6: core contracts never name the x.pipeshub extension (REPORT 5.5: the core is product-neutral)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .support import CONTRACTS, NODE_APP, PYTHON_ROOT

if TYPE_CHECKING:
    from pathlib import Path

# `$id` URIs live on the schemas.pipeshub.com host; only the extension namespace is forbidden.
FORBIDDEN = re.compile(r"x[._]pipeshub|/ext/")


def _offenders(node: object, where: str) -> list[str]:
    if isinstance(node, dict):
        return [hit for k, v in node.items() if k != "$id" for hit in _offenders(k, where) + _offenders(v, where)]
    if isinstance(node, list):
        return [hit for v in node for hit in _offenders(v, where)]
    if isinstance(node, str) and FORBIDDEN.search(node):
        return [f"{where}: {node[:80]}"]
    return []


def core_offenders(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*.json")):
        hits += _offenders(json.loads(path.read_text(encoding="utf-8")), path.relative_to(root).as_posix())
    return hits


def test_core_schemas_and_fixtures_do_not_reference_the_pipeshub_extension() -> None:
    assert core_offenders(CONTRACTS / "core") == []


def test_generated_core_code_does_not_reference_the_pipeshub_extension() -> None:
    generated = [
        *(PYTHON_ROOT / "harness_core" / "contracts" / "generated").rglob("*.py"),
        *(NODE_APP / "src" / "modules" / "agent-harness" / "contracts" / "generated" / "core").rglob("*.ts"),
    ]
    assert generated
    assert [str(p) for p in generated if FORBIDDEN.search(p.read_text(encoding="utf-8"))] == []


def test_lint_detects_a_violation(tmp_path: Path) -> None:
    (tmp_path / "bad.schema.json").write_text(json.dumps({"$ref": "../ext/pipeshub/run-spec.schema.json"}))
    (tmp_path / "bad.invalid.json").write_text(json.dumps([{"instance": {"extensions": {"x.pipeshub": {}}}}]))
    assert len(core_offenders(tmp_path)) == 2
