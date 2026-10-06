"""How a file from a code repository is read.

A source file with a tree-sitter grammar goes to the code parser. Everything
else used to be parsed whole as Markdown, whatever it was: a 50 MB CSV export,
a lock file, a minified bundle. This module names the reader that fits instead,
or the reason the file is skipped.

Pure functions, so the whole table is testable without a parser or a graph.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from enum import Enum

from app.config.constants.arangodb import ExtensionTypes
from app.modules.parsers.code_parser import engine
from app.modules.parsers.code_parser.file_role import is_generated_file_name
from app.modules.parsers.code_parser.lang_config import (
    config_for_extension,
    detect_language,
)
from app.utils.user_errors import (
    BINARY_FILE_SKIPPED,
    GENERATED_FILE_SKIPPED,
    text_file_too_large,
    unsupported_file_type,
)

__all__ = ["CodeFilePlan", "CodeFileRoute", "SkipCause", "plan_code_file"]


class CodeFileRoute(str, Enum):
    CODE = "code"
    DELIMITED = "delimited"
    STRUCTURED = "structured"
    TEXT = "text"
    SKIP = "skip"


class SkipCause(str, Enum):
    GENERATED = "generated"
    NO_PARSER = "no_parser"
    TOO_LARGE = "too_large"
    BINARY = "binary"


@dataclass(frozen=True)
class CodeFilePlan:
    route: CodeFileRoute
    # The language for CODE; the parser registry key for DELIMITED and STRUCTURED.
    parser: str | None = None
    skip_cause: SkipCause | None = None
    # Stored on the record and shown to people, so written for them.
    skip_reason: str | None = None


_DELIMITED = {
    ExtensionTypes.CSV.value: ExtensionTypes.CSV.value,
    ExtensionTypes.TSV.value: ExtensionTypes.TSV.value,
}
_STRUCTURED = {
    ExtensionTypes.JSON.value: ExtensionTypes.JSON.value,
    ExtensionTypes.YAML.value: ExtensionTypes.YAML.value,
    ExtensionTypes.YML.value: ExtensionTypes.YAML.value,
}
# One record per line: the JSON parser rejects them and as prose they are noise.
_DATA_WITHOUT_A_PARSER = frozenset({"ndjson", "jsonl"})

# What git reads to call a file binary.
_BINARY_SNIFF_BYTES = 8000
# UTF-16 and UTF-32 text is full of NUL bytes and still decodes.
_WIDE_TEXT_BOMS = (
    codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE,
)


def _extension(file_name: str | None) -> str:
    base = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    _, dot, ext = base.rpartition(".")
    return ext.lower() if dot else ""


def _declared_extension(extension: str | None) -> str:
    ext = (extension or "").lower().lstrip(".")
    return "" if ext == "unknown" else ext


def _looks_binary(content: bytes) -> bool:
    head = content[:_BINARY_SNIFF_BYTES]
    return not head.startswith(_WIDE_TEXT_BOMS) and b"\x00" in head


def _skip(cause: SkipCause, reason: str) -> CodeFilePlan:
    return CodeFilePlan(CodeFileRoute.SKIP, skip_cause=cause, skip_reason=reason)


def plan_code_file(
    record_name: str,
    file_path: str | None,
    extension: str | None,
    content: bytes,
) -> CodeFilePlan:
    """Pick the reader for one repository file, or the reason to skip it.

    The size limit covers the two readers that take a file whole, code and the
    text fallback. CSV, TSV, JSON and YAML go to the parsers an upload of that
    type gets, with those parsers' own limits and nothing added here.
    """
    path = file_path or record_name
    if is_generated_file_name(record_name) or is_generated_file_name(path):
        return _skip(SkipCause.GENERATED, GENERATED_FILE_SKIPPED)

    declared = _declared_extension(extension)
    language = detect_language(record_name) or detect_language(path)
    if not language and declared:
        cfg = config_for_extension(declared)
        language = cfg.name if cfg else None

    size = len(content)
    limit = engine.MAX_FILE_SIZE_BYTES
    if language:
        if size > limit:
            return _skip(SkipCause.TOO_LARGE, text_file_too_large(size, limit))
        return CodeFilePlan(CodeFileRoute.CODE, parser=language)

    ext = _extension(record_name) or _extension(path) or declared
    if ext in _DELIMITED:
        return CodeFilePlan(CodeFileRoute.DELIMITED, parser=_DELIMITED[ext])
    if ext in _STRUCTURED:
        return CodeFilePlan(CodeFileRoute.STRUCTURED, parser=_STRUCTURED[ext])
    if ext in _DATA_WITHOUT_A_PARSER:
        return _skip(SkipCause.NO_PARSER, unsupported_file_type(ext))
    if size > limit:
        return _skip(SkipCause.TOO_LARGE, text_file_too_large(size, limit))
    if _looks_binary(content):
        return _skip(SkipCause.BINARY, BINARY_FILE_SKIPPED)
    return CodeFilePlan(CodeFileRoute.TEXT)
