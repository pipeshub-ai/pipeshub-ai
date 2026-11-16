"""The parsers whose output the block-id tests drive, by name.

`REGISTRY` maps a parser name to ``(module, build)``: ``build(content)`` parses *content* (bytes
made by `sample(variant)`) and returns the `BlocksContainer`. Every module under
``app/modules/parsers`` that builds a container must be named here or in `DEFERRED` with the
reason it cannot run without a model, a service or a binary fixture; a guard test fails on a
parser that is in neither, so a new parser has to join the id tests.

Block ids are assigned once, centrally (`BlockIdentity.assign`), so the tests below the parsers
check what every parser's output has in common: ids follow content.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.models.blocks import BlocksContainer

Builder = "Callable[[bytes], Awaitable[BlocksContainer]]"


def _text(variant: str) -> bytes:
    return (f"first line of the {variant} text\n\nsecond paragraph about {variant}\n\n"
            f"third paragraph, with a repeat\n\nthird paragraph, with a repeat\n").encode()


def _markdown(variant: str) -> bytes:
    return (f"# Title {variant}\n\nIntro about {variant}.\n\n- item one\n- item two\n\n"
            f"| a | b |\n|---|---|\n| {variant} | 2 |\n| x | y |\n\nRepeat.\n\nRepeat.\n").encode()


def _html(variant: str) -> bytes:
    return (f"<html><body><h1>Title {variant}</h1><p>Intro about {variant}.</p>"
            f"<table><tr><th>a</th><th>b</th></tr><tr><td>{variant}</td><td>2</td></tr></table>"
            f"<p>Repeat.</p><p>Repeat.</p></body></html>").encode()


def _json(variant: str) -> bytes:
    return json.dumps({"name": variant, "items": [{"k": 1, "v": variant}, {"k": 2, "v": "x"}],
                       "note": "the same note"}).encode()


def _yaml(variant: str) -> bytes:
    return f"name: {variant}\nitems:\n  - k: 1\n    v: {variant}\n  - k: 2\n    v: x\nnote: the same note\n".encode()


def _broken_yaml(variant: str) -> bytes:
    """YAML that does not load, which the parser reads line by line instead."""
    return f"name: {variant}\nitems: [unclosed\n  - k: 1\nnote: the same note\nother: {variant}\n".encode()


def _csv(variant: str) -> bytes:
    return f"id,name,city\n1,{variant},Paris\n2,bob,Rome\n3,carol,Oslo\n".encode()


def _python(variant: str) -> bytes:
    return (f"def f_{variant}(x):\n    return x + 1\n\n\nclass C{variant.title()}:\n"
            f"    def m(self):\n        return 2\n").encode()


def _sql_view(variant: str) -> bytes:
    return json.dumps({"view_name": f"v_{variant}", "database_name": "db", "schema_name": "s",
                       "definition": f"select {variant} from t", "source_tables": ["t"]}).encode()


def _sql_table(variant: str) -> bytes:
    return json.dumps({"table_name": f"t_{variant}", "columns": [{"name": "id", "data_type": "int"},
                                                                  {"name": "v", "data_type": "text"}],
                       "rows": [[1, variant], [2, "x"]]}).encode()


async def _parse_with(parser: object, content: bytes) -> BlocksContainer:
    result = await parser.parse(content, "doc", None)
    return result.block_container


def _builders() -> dict[str, tuple[str, Callable[[str], bytes], Callable[[bytes], Awaitable[BlocksContainer]]]]:
    from unittest.mock import MagicMock

    from app.modules.parsers.code_parser.code_file_parser import CodeFileParser
    from app.modules.parsers.csv.csv_parser import CSVParser
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )
    from app.modules.parsers.json.json_parser import JSONParser
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
    from app.modules.parsers.markdown.mdx_parser import MDXParser
    from app.modules.parsers.sql.sql_table_parser import SQLTableParser
    from app.modules.parsers.sql.sql_view_parser import SQLViewParser
    from app.modules.parsers.text.plain_text_parser import plain_text_to_blocks
    from app.modules.parsers.yaml.yaml_parser import YAMLParser

    async def plain(content: bytes) -> BlocksContainer:
        return plain_text_to_blocks(content.decode())

    async def python_code(content: bytes) -> BlocksContainer:
        result = await CodeFileParser().parse(content, "doc.py", {"language": "python"})
        return result.block_container

    async def csv(content: bytes) -> BlocksContainer:
        return await CSVParser(MagicMock()).parse_to_blocks_lightweight(content)

    markdown = MarkdownItParser()
    return {
        "plain_text": ("text/plain_text_parser.py", _text, plain),
        "markdown": ("markdown/markdown_it_parser.py", _markdown, lambda c: _parse_with(markdown, c)),
        "mdx": ("markdown/mdx_parser.py", _markdown, lambda c: _parse_with(MDXParser(markdown), c)),
        "html": ("html_parser/selectolax_html_parser.py", _html,
                 lambda c: _parse_with(SelectolaxHtmlParser(), c)),
        "json": ("json/json_parser.py", _json, lambda c: _parse_with(JSONParser(), c)),
        "yaml": ("yaml/yaml_parser.py", _yaml, lambda c: _parse_with(YAMLParser(), c)),
        "yaml_structural": ("yaml/structural_yaml_parser.py", _broken_yaml,
                            lambda c: _parse_with(YAMLParser(), c)),
        "csv": ("csv/csv_parser.py", _csv, csv),
        "code": ("code_parser/code_file_parser.py", _python, python_code),
        "sql_view": ("sql/sql_view_parser.py", _sql_view, lambda c: _parse_with(SQLViewParser(), c)),
        "sql_table": ("sql/sql_table_parser.py", _sql_table, lambda c: _parse_with(SQLTableParser(), c)),
    }


def registry() -> dict[str, tuple[str, Callable[[str], bytes], Callable[[bytes], Awaitable[BlocksContainer]]]]:
    return _builders()


# Parser modules that build a container but need a model, a service or a binary fixture to run.
# Their containers still get their ids from the same central step; the container-level tests in
# test_block_identity.py cover that step, and the real files are in the format suites.
DEFERRED: dict[str, str] = {
    "pdf/pdfplumber_opencv_processor.py": "needs a rendered PDF and the layout model",
    "docx/docparser.py": "converts through the docling service",
    "pptx/ppt_parser.py": "converts through the docling service",
    "excel/excel_parser.py": "needs the indexing LLM for row text",
    "excel/xls_parser.py": "needs the indexing LLM for row text",
    "epub/epub_parser.py": "needs a binary epub fixture",
    "email/msg_parser.py": "needs a binary msg fixture",
    "image_parser/image_parser.py": "needs the vision model",
    "ooxml/ooxml_package_parser.py": "needs a binary ooxml fixture",
    "markdown/docling_markdown_parser.py": "converts through the docling service",
    "html_parser/docling_html_parser.py": "converts through the docling service",
    "blocks/blocks_parser.py": "reads blocks another parser already built",
    "markdown/markdown_to_blocks.py": "driven through markdown/markdown_it_parser.py",
    "html_parser/html_to_blocks.py": "driven through html_parser/selectolax_html_parser.py",
}
