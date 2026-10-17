"""The parsing service picks the right parser from a file's MIME type and extension."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.parsing.interface import ParseError, ParseErrorCode, ParserProvider
from app.services.parsing.registry import (
    _EXT_TO_FORMAT,
    _MIME_TO_FORMAT,
    ParserRegistry,
    _normalize_format,
)

PARSING_MAIN = Path(__file__).resolve().parents[5] / "app" / "parsing_main.py"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.parametrize(
    ("mime_type", "extension", "expected"),
    [
        ("application/pdf", "pdf", "pdf"),
        (DOCX_MIME, "docx", "docx"),
        ("application/msword", "doc", "doc"),
        (PPTX_MIME, "pptx", "pptx"),
        ("application/vnd.ms-powerpoint", "ppt", "ppt"),
        (XLSX_MIME, "xlsx", "xlsx"),
        ("application/vnd.ms-excel", "xls", "xls"),
        ("text/csv", "csv", "csv"),
        ("text/tab-separated-values", "tsv", "tsv"),
        ("text/html", "html", "html"),
        ("text/markdown", "md", "md"),
        ("text/mdx", "mdx", "mdx"),
        ("text/plain", "txt", "txt"),
        ("application/json", "json", "json"),
        ("application/x-yaml", "yml", "yaml"),
        ("image/png", "png", "png"),
        ("image/jpeg", "jpeg", "jpg"),
        ("image/svg+xml", "svg", "svg"),
        ("application/epub+zip", "epub", "epub"),
        # Google Workspace files are exported as their Office equivalents.
        ("application/vnd.google-apps.document", "", "docx"),
        ("application/vnd.google-apps.presentation", "", "pptx"),
        ("application/vnd.google-apps.spreadsheet", "", "xlsx"),
        # Mail bodies are HTML.
        ("text/gmail_content", "", "html"),
        # A generic or missing MIME type falls back to the extension.
        ("application/octet-stream", "docx", "docx"),
        ("", "PDF", "pdf"),
        ("", ".xlsx", "xlsx"),
        (None, "htm", "html"),
        # Source files read from a git tree arrive as text/plain.
        ("text/plain", "py", "code"),
        ("text/plain", "go", "code"),
        ("text/x-sh", "sh", "txt"),
    ],
)
def test_format_key_for_customer_files(mime_type, extension, expected) -> None:
    assert _normalize_format(mime_type, extension) == expected


@pytest.mark.parametrize(("mime_type", "extension"), [("application/zip", "zip"), ("", ""), ("video/mp4", "mp4")])
def test_unknown_types_are_rejected_as_unsupported(mime_type, extension) -> None:
    with pytest.raises(ParseError) as caught:
        ParserRegistry().resolve(mime_type, extension)
    assert caught.value.code == ParseErrorCode.UNSUPPORTED_FORMAT


def _default_parser_classes() -> dict[str, str]:
    """Read which class ``parsing_main._build_registry`` registers as the
    DEFAULT parser for each format key, without importing the service (its
    import installs signal handlers and builds the DI container)."""
    tree = ast.parse(PARSING_MAIN.read_text())
    build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_registry")

    var_class: dict[str, str] = {}
    for node in ast.walk(build):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            var_class[node.targets[0].id] = node.value.func.id

    def class_of(expr: ast.expr) -> str:
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            return expr.func.id
        if isinstance(expr, ast.Name):
            return var_class.get(expr.id, expr.id)
        raise AssertionError(f"unexpected parser expression: {ast.dump(expr)}")

    registered: dict[str, str] = {}
    for node in ast.walk(build):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"):
            continue
        fmt_arg, provider_arg, parser_arg = node.args
        if not (isinstance(provider_arg, ast.Attribute) and provider_arg.attr == ParserProvider.DEFAULT.name):
            continue
        if isinstance(fmt_arg, ast.Constant):
            registered[fmt_arg.value] = class_of(parser_arg)
        elif isinstance(fmt_arg, ast.Name):
            loop = next(
                n for n in ast.walk(build)
                if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == fmt_arg.id
            )
            for element in loop.iter.elts:
                registered[element.value] = class_of(parser_arg)
    return registered


EXPECTED_DEFAULT_PARSER = {
    "pdf": "SmartPDFParser",
    "epub": "EPUBParser",
    "docx": "LocalDoclingParser",
    "doc": "DocParser",
    "pptx": "LocalDoclingParser",
    "ppt": "PPTParser",
    "xlsx": "ExcelParser",
    "xls": "XLSParser",
    "csv": "CSVParser",
    "tsv": "CSVParser",
    "html": "SelectolaxHtmlParser",
    "md": "MarkdownItParser",
    "mdx": "MDXParser",
    "txt": "MarkdownItParser",
    "code": "CodeFileParser",
    "json": "JSONParser",
    "yaml": "YAMLParser",
    "blocks": "BlocksParser",
    "sql_table": "SQLTableParser",
    "sql_view": "SQLViewParser",
    **dict.fromkeys(("png", "jpg", "jpeg", "webp", "svg", "heic", "heif"), "ImageParser"),
}


def test_parsing_service_wires_the_expected_default_parser_for_each_format() -> None:
    assert _default_parser_classes() == EXPECTED_DEFAULT_PARSER


def test_every_recognised_file_type_has_a_parser() -> None:
    registered = _default_parser_classes()
    format_keys = set(_MIME_TO_FORMAT.values()) | set(_EXT_TO_FORMAT.values())
    assert sorted(format_keys - registered.keys()) == []
