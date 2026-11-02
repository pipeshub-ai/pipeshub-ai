"""Tests that read what a model answers must be marked ``live_model``.

PR stacks index with a stand-in model that answers "OK" to every prompt. A test
that asserts on search ranking, chat answers or citations would fail against it,
so the catalog gives ``live_model`` tests the real-key need (suites.toml
``marker_needs``) and they stay ``not_run`` on a PR instead of failing.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from qa import globs  # noqa: E402

pytestmark = pytest.mark.unit

# Helpers and routes whose output depends on the model.
SEMANTIC_CALLS = re.compile(
    r"access_probe|conversations_client|ConversationsClient|search_client|SearchClient"
    r"|/api/v1/search|/api/v1/conversations|\branking\b|\bcitation",
)
FIXTURE = "ai_models_configured"
MARK = "live_model"


def _it_stack_files() -> list[Path]:
    suites = tomllib.loads((ROOT / "scripts/qa/rules/suites.toml").read_text())["suite"]
    suite = next(s for s in suites if s["id"] == "it-stack")
    return sorted(
        p
        for p in (ROOT / "integration-tests").rglob("*.py")
        if (rel := p.relative_to(ROOT).as_posix())
        and ".venv" not in p.parts
        and globs.match_any(rel, suite["tests"])
        and not globs.match_any(rel, suite.get("exclude", []))
    )


def _reaches_model_fixture(path: Path) -> bool:
    """The file, or a conftest between it and integration-tests/, names the fixture."""
    top = ROOT / "integration-tests"
    chain = [path] + [d / "conftest.py" for d in path.parents if top in d.parents]
    return any(p.exists() and FIXTURE in p.read_text() for p in chain)


def _marks(node: ast.AST) -> bool:
    return any(MARK in ast.unparse(d) for d in getattr(node, "decorator_list", []))


def _unmarked_tests(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    if any(
        isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in n.targets) and MARK in ast.unparse(n.value)
        for n in tree.body
    ):
        return []
    missing = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test") and not _marks(node):
            missing += [f"{node.name}::{m.name}" for m in node.body if isinstance(m, ast.FunctionDef) and m.name.startswith("test_") and not _marks(m)]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") and not _marks(node):
            missing.append(node.name)
    return missing


def _semantic_files() -> list[Path]:
    return [p for p in _it_stack_files() if SEMANTIC_CALLS.search(p.read_text()) and _reaches_model_fixture(p)]


def test_the_scan_finds_the_files_it_is_meant_to_police() -> None:
    assert len(_semantic_files()) >= 10


def test_files_that_read_model_output_are_marked_live_model() -> None:
    unmarked = {
        p.relative_to(ROOT / "integration-tests").as_posix(): missing
        for p in _semantic_files()
        if (missing := _unmarked_tests(p))
    }
    assert not unmarked, (
        f"These tests read what a model answers but are not marked @pytest.mark.{MARK}, "
        "so they would fail against the PR stand-in model:\n"
        + "\n".join(f"  {f}: {', '.join(m[:3])}{' ...' if len(m) > 3 else ''}" for f, m in sorted(unmarked.items()))
    )
