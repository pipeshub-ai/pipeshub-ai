"""Tests for ParserRegistry: resolution, fallback chains, defaults."""
from __future__ import annotations

import pytest

from app.models.blocks import BlocksContainer
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.registry import ParserRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_parser(provider: ParserProvider, success: bool = True) -> IParser:
    """Return a mock IParser that succeeds or raises PARSE_FAILED."""

    class _MockParser:
        def supported_formats(self) -> list[str]:
            return ["pdf"]

        async def parse(self, content, record_name, config=None) -> ParseResult:
            if not success:
                raise ParseError(ParseErrorCode.PARSE_FAILED, "Simulated failure")
            return ParseResult(
                block_container=BlocksContainer(blocks=[], block_groups=[]),
                provider_used=provider,
                metadata={"mock": True},
            )

    return _MockParser()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_and_resolve_single_provider() -> None:
    registry = ParserRegistry()
    parser = _make_parser(ParserProvider.DOCLING)
    registry.register("pdf", ParserProvider.DOCLING, parser)
    registry.set_default("pdf", ParserProvider.DOCLING)

    resolved = registry.resolve("application/pdf", "pdf")
    assert resolved is parser


def test_register_multiple_providers_for_same_format() -> None:
    registry = ParserRegistry()
    p1 = _make_parser(ParserProvider.DOCLING)
    p2 = _make_parser(ParserProvider.DEFAULT)
    registry.register("pdf", ParserProvider.DOCLING, p1)
    registry.register("pdf", ParserProvider.DEFAULT, p2)
    registry.set_default("pdf", ParserProvider.DOCLING)

    assert registry.resolve("application/pdf", "pdf") is p1
    assert registry.resolve("application/pdf", "pdf", ParserProvider.DEFAULT) is p2


def test_list_providers() -> None:
    registry = ParserRegistry()
    registry.register("pdf", ParserProvider.DOCLING, _make_parser(ParserProvider.DOCLING))
    registry.register("pdf", ParserProvider.DEFAULT, _make_parser(ParserProvider.DEFAULT))

    providers = registry.list_providers("pdf")
    assert ParserProvider.DOCLING in providers
    assert ParserProvider.DEFAULT in providers


def test_list_all_formats() -> None:
    registry = ParserRegistry()
    registry.register("pdf", ParserProvider.DEFAULT, _make_parser(ParserProvider.DEFAULT))
    registry.register("csv", ParserProvider.DEFAULT, _make_parser(ParserProvider.DEFAULT))

    all_formats = registry.list_all_formats()
    assert "pdf" in all_formats
    assert "csv" in all_formats


# ---------------------------------------------------------------------------
# Resolution errors
# ---------------------------------------------------------------------------


def test_resolve_raises_unsupported_format_for_unknown_mime() -> None:
    registry = ParserRegistry()
    with pytest.raises(ParseError) as exc_info:
        registry.resolve("application/x-unknown-type", "xyz")
    assert exc_info.value.code == ParseErrorCode.UNSUPPORTED_FORMAT


def test_resolve_raises_provider_unavailable_when_provider_missing() -> None:
    registry = ParserRegistry()
    registry.register("pdf", ParserProvider.DOCLING, _make_parser(ParserProvider.DOCLING))
    with pytest.raises(ParseError) as exc_info:
        registry.resolve("application/pdf", "pdf", ParserProvider.DEFAULT)
    assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE


# ---------------------------------------------------------------------------
# Fallback chain — set_fallback_chain / get_fallback_chain storage
# ---------------------------------------------------------------------------


def test_set_and_get_fallback_chain() -> None:
    registry = ParserRegistry()
    registry.register("pdf", ParserProvider.DOCLING, _make_parser(ParserProvider.DOCLING))
    registry.register("pdf", ParserProvider.DEFAULT, _make_parser(ParserProvider.DEFAULT))
    chain = [ParserProvider.DOCLING, ParserProvider.DEFAULT]
    registry.set_fallback_chain("pdf", chain)
    assert registry.get_fallback_chain("pdf") == chain


def test_get_fallback_chain_empty_when_not_set() -> None:
    registry = ParserRegistry()
    assert registry.get_fallback_chain("pdf") == []


def test_resolve_maps_code_mime_and_extension_to_txt() -> None:
    """Code without a tree-sitter grammar still falls back to the text parser."""
    registry = ParserRegistry()
    parser = _make_parser(ParserProvider.DEFAULT)
    registry.register("txt", ParserProvider.DEFAULT, parser)
    registry.set_default("txt", ParserProvider.DEFAULT)

    assert registry.resolve("text/x-sh", "") is parser
    assert registry.resolve("text/x-shellscript", "") is parser
    assert registry.resolve("text/css", "") is parser
    assert registry.resolve("", "css") is parser
    assert registry.resolve("", "scss") is parser
    assert registry.resolve("", "vue") is parser
    assert registry.resolve("", "sql") is parser


def test_resolve_maps_grammar_backed_code_to_code_parser() -> None:
    """Python/TS/JS have a tree-sitter grammar and route to the code parser."""
    registry = ParserRegistry()
    parser = _make_parser(ParserProvider.DEFAULT)
    registry.register("code", ParserProvider.DEFAULT, parser)
    registry.set_default("code", ParserProvider.DEFAULT)

    assert registry.resolve("text/x-python", "py") is parser
    assert registry.resolve("", "ts") is parser
    assert registry.resolve("", "tsx") is parser
    assert registry.resolve("application/javascript", "") is parser
    assert registry.resolve("text/javascript", "") is parser
    assert registry.resolve("text/x-python-script", "") is parser
    assert registry.resolve("text/x-script.python", "") is parser


def test_supported_code_file_extensions_normalize() -> None:
    """Every CODE_FILE gate extension must resolve to a known format key."""
    from app.config.constants.arangodb import SUPPORTED_CODE_FILE_EXTENSIONS
    from app.modules.parsers.code_parser.lang_config import SUPPORTED_CODE_EXTENSIONS
    from app.services.parsing.registry import _normalize_format

    # html/htm → html; md → md; grammar-backed code → code; the rest → txt
    expected = {
        "html": "html",
        "htm": "html",
        "md": "md",
    }
    for ext in SUPPORTED_CODE_FILE_EXTENSIONS:
        format_key = _normalize_format("", ext)
        assert format_key is not None, f"missing registry mapping for .{ext}"
        want = expected.get(ext, "code" if ext in SUPPORTED_CODE_EXTENSIONS else "txt")
        assert format_key == want, f".{ext} mapped to {format_key!r}, expected {want!r}"


def test_every_grammar_backed_extension_maps_to_code() -> None:
    """A language added to lang_config but missing from _EXT_TO_FORMAT would
    silently route through the prose parser instead of tree-sitter."""
    from app.modules.parsers.code_parser.lang_config import SUPPORTED_CODE_EXTENSIONS
    from app.services.parsing.registry import _EXT_TO_FORMAT

    missing = [
        ext for ext in sorted(SUPPORTED_CODE_EXTENSIONS)
        if _EXT_TO_FORMAT.get(ext) != "code"
    ]
    assert not missing, (
        f"Extensions with a tree-sitter grammar but not mapped to 'code' in "
        f"_EXT_TO_FORMAT: {missing}"
    )


def test_resolve_maps_image_jpg_and_heic_mime() -> None:
    registry = ParserRegistry()
    parser = _make_parser(ParserProvider.DEFAULT)
    registry.register("jpg", ParserProvider.DEFAULT, parser)
    registry.register("heic", ParserProvider.DEFAULT, parser)
    registry.set_default("jpg", ParserProvider.DEFAULT)
    registry.set_default("heic", ParserProvider.DEFAULT)

    assert registry.resolve("image/jpg", "") is parser
    assert registry.resolve("image/heic", "") is parser


def test_resolve_maps_epub_mime_and_extension() -> None:
    registry = ParserRegistry()
    parser = _make_parser(ParserProvider.DEFAULT)
    registry.register("epub", ParserProvider.DEFAULT, parser)
    registry.set_default("epub", ParserProvider.DEFAULT)

    assert registry.resolve("application/epub+zip", "") is parser
    assert registry.resolve("", "epub") is parser


def test_resolve_maps_json_and_yaml_mime_and_extension() -> None:
    registry = ParserRegistry()
    json_parser = _make_parser(ParserProvider.DEFAULT)
    yaml_parser = _make_parser(ParserProvider.DEFAULT)
    registry.register("json", ParserProvider.DEFAULT, json_parser)
    registry.register("yaml", ParserProvider.DEFAULT, yaml_parser)
    registry.set_default("json", ParserProvider.DEFAULT)
    registry.set_default("yaml", ParserProvider.DEFAULT)

    assert registry.resolve("application/json", "") is json_parser
    assert registry.resolve("", "json") is json_parser
    assert registry.resolve("application/yaml", "") is yaml_parser
    assert registry.resolve("text/yaml", "") is yaml_parser
    assert registry.resolve("application/x-yaml", "") is yaml_parser
    assert registry.resolve("", "yaml") is yaml_parser
    assert registry.resolve("", "yml") is yaml_parser
