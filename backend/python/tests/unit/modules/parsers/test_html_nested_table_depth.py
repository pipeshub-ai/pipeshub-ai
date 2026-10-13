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


def _two_cell_nest(depth: int, *, wrap_innermost: bool) -> str:
    html = "<table><tr><td>LEAF</td><td>SIBLING</td></tr></table>"
    for i in range(depth):
        inner = f"<div>{html}</div>" if wrap_innermost and i == 0 else html
        html = f"<table><tr><td>{inner}</td><td>a{i}</td></tr></table>"
    return html


def _markdown(html: str) -> str:
    node = LexborHTMLParser(f"<html><body>{html}</body></html>").css_first("table")
    return normalized_table_to_markdown(normalize_html_table(node))


def test_table_inside_cell_markup_gets_the_same_cap_as_a_direct_child() -> None:
    from app.modules.parsers.html_parser.html_to_blocks import _MAX_NESTED_TABLE_DEPTH

    direct = _markdown(_two_cell_nest(_MAX_NESTED_TABLE_DEPTH, wrap_innermost=False))
    wrapped = _markdown(_two_cell_nest(_MAX_NESTED_TABLE_DEPTH, wrap_innermost=True))
    assert wrapped == direct


def test_a_flattened_table_keeps_its_cells_apart() -> None:
    from app.modules.parsers.html_parser.html_to_blocks import _MAX_NESTED_TABLE_DEPTH

    markdown = _markdown(_two_cell_nest(_MAX_NESTED_TABLE_DEPTH + 1, wrap_innermost=False))
    assert "LEAF SIBLING" in markdown
    assert "LEAFSIBLING" not in markdown
