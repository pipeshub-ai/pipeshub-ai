"""C-33 (QA7-40, XL-12): every cross-language vector has a reader in Python and in Node.

A vector exists so two implementations agree; one only Python reads holds nothing on the Node side. A
vector one language alone implements is listed in ``SINGLE_LANGUAGE`` with the reason, and the entry
fails once the other language reads it too.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

VECTOR_DIRS = (REPO_ROOT / "contracts" / "harness" / "vectors", REPO_ROOT / "contracts" / "control-plane" / "vectors")
NODE_ROOT = REPO_ROOT / "backend" / "nodejs" / "apps"

SINGLE_LANGUAGE: dict[str, tuple[str, str]] = {
    "mcp-tool-names.json": ("python", "the MCP hub and the engines (Python) name MCP tools; Node never does"),
    "tool-identity.json": ("python", "instance-scoped tool names are built by the worker's tool registry only"),
    "authz.json": ("node", "the Authorizer runs in Node only (03-interfaces §5.1, CP-SKEL D3); Python's admin checks go as their routes become facades (CPD-9 §4)"),
    "live-fold.json": ("python", "the live meet runs in the worker's resolver only; Node pins layers and never folds live ones (CPD6-4)"),
    "tool-display-names.json": ("node", "the Slack bot and the web client name a step's tool at the surface; Python only sends the tool's own display name (TOOLNAMES)"),
    "team-scope.json":("node","the team layer source and the effective-set resolver run in Node; Python reads the pin (CPD-5)"),
    "working-jobs.json": ("node", "the Jobs page's Working rule is Node's count (CJ14-FU-1) and the web client's sections; Python never lists a person's jobs"),
    "org-only-addresses.json": ("python", "the worker decides what an org-only address taints; Node only carries the list"),
    "ui-stream-events.json": ("node", "Node alone projects the durable stream the web app reads; the worker writes journal events, never AG-UI frames (UX-01)"),
}


def _vectors() -> list[Path]:
    return sorted(p for d in VECTOR_DIRS for p in d.iterdir() if p.suffix == ".json" or p.is_dir())


@cache
def _sources(language: str) -> str:
    if language == "python":
        # The fault suite under integration-tests/harness is harness Python too, and reads vectors.
        roots = [PYTHON_ROOT / r for r in ("app", "harness_core", "harness_engines", "tests")]
        roots.append(REPO_ROOT / "integration-tests" / "harness")
        # This file names node-only vectors in SINGLE_LANGUAGE; that is not a read.
        files = [p for r in roots for p in r.rglob("*.py") if "generated" not in p.parts and p != Path(__file__)]
    else:
        files = [p for r in ("src", "tests") for p in (NODE_ROOT / r).rglob("*.ts")]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def _read_by(vector: Path, language: str) -> bool:
    text = _sources(language)
    stem = vector.name.removesuffix(".json")
    named = [f"vectors/{stem}", f"'vectors', '{stem}", f'"vectors" / "{stem}"', f'VECTORS / "{stem}"']
    if vector.suffix:
        named.append(vector.name)
    return any(n in text for n in named)


@pytest.mark.parametrize("vector", _vectors(), ids=lambda p: p.name)
def test_c33_a_vector_is_read_in_python_and_in_node(vector: Path) -> None:
    readers = {lang for lang in ("python", "node") if _read_by(vector, lang)}
    single = SINGLE_LANGUAGE.get(vector.name)
    if single is None:
        assert readers == {"python", "node"}, f"{vector.name} is read only by {sorted(readers) or 'nobody'}"
    else:
        assert readers == {single[0]}, f"{vector.name}: listed as {single[0]}-only, read by {sorted(readers)}"


def test_c33_every_single_language_entry_names_a_vector() -> None:
    assert set(SINGLE_LANGUAGE) <= {p.name for p in _vectors()}
