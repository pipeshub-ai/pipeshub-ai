"""Domain modules must stay free of transport and persistence imports (ADR-007, PH02-22)."""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[3] / "app"

DOMAIN_DIRS = [
    APP_DIR / "modules" / "authz" / "domain",
    APP_DIR / "modules" / "agents" / "collaboration",
]

FORBIDDEN_PREFIXES = (
    "fastapi",
    "starlette",
    "neo4j",
    "arango",
    "qdrant_client",
    "pymongo",
    "motor",
    "redis",
    "aiokafka",
    "confluent_kafka",
    "app.api",
    "app.services.graph_db",
    "app.services.vector_db",
    "app.services.messaging",
)


def _is_forbidden(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES)


def find_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            violations.extend(
                f"{path}:{node.lineno} imports {m}" for m in modules if _is_forbidden(m)
            )
    return violations


@pytest.mark.parametrize("domain_dir", DOMAIN_DIRS, ids=lambda p: p.name)
def test_current_tree_respects_domain_boundaries(domain_dir: Path) -> None:
    assert find_violations(domain_dir) == []


def test_scan_flags_forbidden_imports(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        "import neo4j\n"
        "from fastapi import Request\n"
        "from app.services.graph_db.interface import IGraphDBProvider\n"
        "import json\n"
    )
    found = find_violations(tmp_path)
    assert len(found) == 3
    assert any("neo4j" in v for v in found)
    assert any("fastapi" in v for v in found)
    assert any("app.services.graph_db" in v for v in found)


def test_scan_allows_pure_modules(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("import json\nfrom dataclasses import dataclass\nfrom app.models.blocks import X\n")
    assert find_violations(tmp_path) == []
