"""The indexing service document keeps its numbered sections: a change to one part must not drop another."""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[4] / "docs" / "indexing-service.md"


def test_section_two_keeps_every_numbered_subsection_in_order() -> None:
    headings = re.findall(r"^### (2\.\d+) ", DOC.read_text(), flags=re.M)
    assert headings, "no section 2 subsections found"
    assert headings == [f"2.{i}" for i in range(1, len(headings) + 1)], headings
    assert "2.6" in headings  # record processing diagnostics
