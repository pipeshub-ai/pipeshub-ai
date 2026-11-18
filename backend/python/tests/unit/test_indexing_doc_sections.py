"""The indexing service document keeps its numbered sections: a change to one part must not drop, move or duplicate another."""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[4] / "docs" / "indexing-service.md"


def _headings(prefix: str) -> list[str]:
    return re.findall(rf"^{prefix} (\d+(?:\.\d+)?)[. ]", DOC.read_text(), flags=re.M)


def test_top_level_sections_appear_once_and_in_order() -> None:
    assert _headings("##") == [str(i) for i in range(1, 8)]  # sections 1 to 7, each once


def test_section_two_keeps_every_numbered_subsection_in_order() -> None:
    assert [h for h in _headings("###") if h.startswith("2.")] == [f"2.{i}" for i in range(1, 7)]


def test_each_subsection_sits_under_its_own_section() -> None:
    """Walk the headings in file order: a ``### N.x`` must follow ``## N``."""
    current = None
    for level, number in re.findall(r"^(##|###) (\d+(?:\.\d+)?)[. ]", DOC.read_text(), flags=re.M):
        if level == "##":
            current = number
        else:
            assert number.split(".")[0] == current, f"### {number} is under ## {current}"
