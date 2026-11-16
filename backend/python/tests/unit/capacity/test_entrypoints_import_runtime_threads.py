"""Every service entry point imports ``app.utils.runtime_threads`` before any other app module."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[3] / "app"
ENTRYPOINTS = sorted(p for p in APP.glob("*_main.py"))
# The sandbox's Docker API proxy loads no ML library and runs in its own image.
EXEMPT = {"docker_proxy_main.py": "no ML library; separate image"}


def _first_app_import(path: Path) -> str | None:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    return alias.name
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
            and (node.module == "app" or node.module.startswith("app."))
        ):
            return node.module
    return None


def test_the_entry_points_are_found() -> None:
    assert {"indexing_main.py", "extraction_main.py", "embedding_main.py"} <= {p.name for p in ENTRYPOINTS}


@pytest.mark.parametrize("path", [p for p in ENTRYPOINTS if p.name not in EXEMPT], ids=lambda p: p.name)
def test_runtime_threads_is_the_first_app_import(path: Path) -> None:
    assert _first_app_import(path) == "app.utils.runtime_threads"
