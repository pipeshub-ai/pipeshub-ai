"""Deeply nested tables must not blow up block text.

Each nesting level re-serialized everything below it, so text roughly doubled
per level: a 17KB page with tables nested 17 deep produced two 36MB table rows,
which no embedding model accepts, and the record then retried until it stalled
indexing.
"""

from selectolax.lexbor import LexborHTMLParser

from app.modules.parsers.html_parser.html_to_blocks import (
    normalize_html_table,
    normalized_table_to_markdown,
)


def _nested_tables(depth: int, leaf: str = "LEAFTEXT") -> str:
    # The rowspan puts the nested table on two grid rows, so every level
    # serializes it at least twice: tree-shaped layout tables look like this.
    html = leaf
    for i in range(depth):
        html = (
            f"<table><tbody><tr><td rowspan=2>{html}</td><td>a{i}</td></tr>"
            f"<tr><td>b{i}</td></tr></tbody></table>"
        )
    return html


def _serialized_length(depth: int) -> int:
    node = LexborHTMLParser(f"<html><body>{_nested_tables(depth)}</body></html>").css_first("table")
    return len(normalized_table_to_markdown(normalize_html_table(node)))


def test_nested_table_text_grows_linearly_not_exponentially() -> None:
    small, large = _serialized_length(4), _serialized_length(12)
    # Uncapped, eight more levels made the text about 570 times longer.
    assert large < small * 10


def test_tables_below_the_cap_are_still_serialized_as_markdown() -> None:
    from app.modules.parsers.html_parser.html_to_blocks import _MAX_NESTED_TABLE_DEPTH

    node = LexborHTMLParser(f"<html><body>{_nested_tables(_MAX_NESTED_TABLE_DEPTH)}</body></html>").css_first("table")
    markdown = normalized_table_to_markdown(normalize_html_table(node))
    assert "LEAFTEXT" in markdown and "|" in markdown
