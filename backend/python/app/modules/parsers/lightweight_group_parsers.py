"""Group parsers for pushed content, whatever ``PARSER_BACKEND`` says.

This module imports no Docling class, so pushed content cannot reach one.
"""

from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser


def lightweight_group_parsers(**kwargs: object) -> tuple[MarkdownItParser, SelectolaxHtmlParser]:
    return MarkdownItParser(**kwargs), SelectolaxHtmlParser(**kwargs)
