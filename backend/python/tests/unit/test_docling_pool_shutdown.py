"""The idle-shutting Docling executor stops before the governor closes (T76)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.modules.parsers.pdf import docling_processor as dp

ROOT = Path(__file__).parents[2] / "app"


def _line_of(function: ast.AST, suffix: str) -> int:
    lines = [
        n.lineno for n in ast.walk(function)
        if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(suffix)
    ]
    assert lines, f"no call to {suffix}"
    return min(lines)


@pytest.mark.parametrize(
    ("module", "closer"),
    [("docling_main.py", "governor.close"), ("parsing_main.py", "governor.close"), ("indexing_main.py", "governor.close")],
)
def test_docling_pool_shutdown_before_governor_close(module: str, closer: str) -> None:
    tree = ast.parse((ROOT / module).read_text())
    lifespan = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"
    )
    assert _line_of(lifespan, "shutdown_docling_pool") < _line_of(lifespan, closer), module


def test_shutdown_call_is_inert_in_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the budget off no wrapper exists, so there is nothing to stop and the legacy pool is left alone."""
    monkeypatch.setenv("PARSE_LANES", "off")
    monkeypatch.setattr(dp, "_auto", [None])
    dp._get_process_pool.cache_clear()
    assert dp.shutdown_docling_pool() is False
    assert dp._get_process_pool.cache_info().currsize == 0  # not created, not shut down
