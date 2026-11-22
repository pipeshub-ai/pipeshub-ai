"""One counter for a PDF's pages.

Every place that produces a page count stored on a record, or a parse state's ``coverage.pages``, gets it from
``open_pdf`` (pdfplumber's ``len(pdf.pages)``) or from the parsing service's one counter. A writer of ``coverage.pages``
that counts another way would make the record's ``pageCount`` and the parse state disagree, so the scan fails (it does not
skip) when such a writer exists without ``open_pdf``.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"

# the files that produce a stored page count, and what they count with
COUNTERS = {
    "modules/parsers/pdf/pdf_inspection.py": "len(pdf.pages)",
    "services/parsing/providers/smart_pdf_parser.py": "len(pdf.pages)",
}


def _coverage_page_writers(source: str) -> list[int]:
    """Lines that build a ``coverage`` mapping holding a ``pages`` key."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                names_pages = isinstance(value, ast.Dict) and any(isinstance(inner, ast.Constant) and inner.value == "pages" for inner in value.keys)
                if isinstance(key, ast.Constant) and key.value == "coverage" and names_pages:
                    lines.append(node.lineno)
    return lines


def _violations(files: dict[str, str]) -> list[str]:
    return [name for name, source in files.items() if _coverage_page_writers(source) and "open_pdf" not in source]


def test_every_writer_of_coverage_pages_calls_open_pdf() -> None:
    files = {str(path.relative_to(APP)): path.read_text(encoding="utf-8-sig") for path in APP.rglob("*.py")}

    assert _violations(files) == []


def test_a_writer_of_coverage_pages_that_counts_another_way_is_seen() -> None:
    """The negative control."""
    sneaky = 'def state(pdf):\n    return {"coverage": {"pages": pdf.get_num_pages()}}\n'
    honest = 'from x import open_pdf\ndef state(b):\n    return {"coverage": {"pages": open_pdf(b).page_count}}\n'

    assert _violations({"sneaky.py": sneaky}) == ["sneaky.py"]
    assert _violations({"honest.py": honest}) == []


def test_the_counters_still_count_with_pdfplumber() -> None:
    for relative, expression in COUNTERS.items():
        assert expression in (APP / relative).read_text(encoding="utf-8-sig"), relative


def test_the_only_stored_page_count_sources_are_the_listed_ones() -> None:
    """``pageCount`` is written from ``inspected.page_count`` (in-process) and ``metadata["page_count"]`` (service) only."""
    events = (APP / "events/events.py").read_text(encoding="utf-8-sig")

    assert "inspected.page_count" in events and 'get("page_count")' in events
    assert "pypdfium2" not in events
