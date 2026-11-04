"""Exact Markdown for representative connector bodies.

A change in the converter (or a library bump) shows up as a diff in the .md files.
To accept an intended change, rewrite them with::

    UPDATE_GOLDEN=1 pytest tests/unit/utils/test_html_markdown_golden.py
"""

import os
from pathlib import Path

import pytest

from app.utils.html_markdown import html_to_markdown

_DIR = Path(__file__).parent / "golden" / "html_markdown"
_CASES = sorted(p.stem for p in _DIR.glob("*.html"))


def test_the_corpus_is_present() -> None:
    assert len(_CASES) >= 8


@pytest.mark.parametrize("name", _CASES)
def test_markdown_matches_golden(name: str) -> None:
    actual = html_to_markdown((_DIR / f"{name}.html").read_text(encoding="utf-8"))
    expected_path = _DIR / f"{name}.md"
    if os.environ.get("UPDATE_GOLDEN"):
        expected_path.write_text(actual, encoding="utf-8", newline="")
    assert actual == expected_path.read_text(encoding="utf-8")
