"""Deeply nested tables must not blow up block text.

Each nesting level re-serialized everything below it, so text roughly doubled
per level: a 17KB Wikipedia page (taxonomy cladogram, 17 levels) produced two
36MB table rows, which no embedding model accepts — the record then retried
until it stalled indexing.
"""

from selectolax.lexbor import LexborHTMLParser

from app.modules.parsers.html_parser.html_to_blocks import (
    _MAX_NESTED_TABLE_DEPTH,
    normalize_html_table,
    normalized_table_to_markdown,
)


def _nested_tables(depth: int, leaf: str = "LEAFTEXT") -> str:
    html = leaf
    for i in range(depth):
        html = f"<table><tbody><tr><th>H{i}</th><td>{html}</td></tr></tbody></table>"
    return html


def _serialized_length(depth: int) -> int:
    node = LexborHTMLParser(f"<html><body>{_nested_tables(depth)}</body></html>").css_first("table")
    return len(normalized_table_to_markdown(normalize_html_table(node)))


def test_nested_table_text_grows_linearly_not_exponentially() -> None:
    small, large = _serialized_length(4), _serialized_length(16)
    # Exponential growth would be ~2**12 times bigger.
    assert large < small * 10


def test_tables_below_the_cap_are_still_serialized_as_markdown() -> None:
    node = LexborHTMLParser(f"<html><body>{_nested_tables(_MAX_NESTED_TABLE_DEPTH)}</body></html>").css_first("table")
    markdown = normalized_table_to_markdown(normalize_html_table(node))
    assert "LEAFTEXT" in markdown and "|" in markdown
