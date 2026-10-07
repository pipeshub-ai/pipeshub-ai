"""How a file on the code path is read.

A source file with a tree-sitter grammar goes to the code parser. Everything
else used to be parsed whole as Markdown, whatever it was: a 50 MB CSV export,
a lock file, a minified bundle. This module names the reader that fits instead,
or, for a file synced from a code repository, the reason it is skipped.

Pure functions, so the whole table is testable without a parser or a graph.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from enum import Enum

from app.config.constants.arangodb import ExtensionTypes
from app.modules.parsers.code_parser import engine
from app.modules.parsers.code_parser.file_role import is_generated_file_name
from app.modules.parsers.code_parser.lang_config import config_for_extension
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
    # For CODE, the extension the grammar was chosen by. The parsing service
    # picks its parser from an extension, and the record's own may disagree
    # with its name or be missing.
    extension: str | None = None
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
    *,
    repository_file: bool,
) -> CodeFilePlan:
    """Pick the reader for one file on the code path, or the reason to skip it.

    *repository_file* is True for a file a connector synced out of a code
    repository (a ``CODE_FILE`` record). Only those are filtered: generated
    files, data dumps with no parser and binary content are skipped, and the
    text fallback is held to the code size limit. A source file someone
    uploaded keeps what it had before, the code parser's own size limit and an
    unlimited text fallback.

    CSV, TSV, JSON and YAML go to the parsers an upload of that type gets, with
    those parsers' own limits and nothing added here.
    """
    path = file_path or record_name
    if repository_file and (is_generated_file_name(record_name) or is_generated_file_name(path)):
        return _skip(SkipCause.GENERATED, GENERATED_FILE_SKIPPED)

    declared = _declared_extension(extension)
    # The file's own name decides before the extension the record declares.
    candidates = (_extension(record_name), _extension(path), declared)
    grammar = next(
        ((ext, cfg) for ext in candidates if ext and (cfg := config_for_extension(ext))), None
    )

    size = len(content)
    limit = engine.MAX_FILE_SIZE_BYTES
    too_large = text_file_too_large(size, limit, repository_file=repository_file)
    if grammar:
        if size > limit:
            return _skip(SkipCause.TOO_LARGE, too_large)
        grammar_extension, cfg = grammar
        return CodeFilePlan(CodeFileRoute.CODE, parser=cfg.name, extension=grammar_extension)

    ext = next((ext for ext in candidates if ext), "")
    if ext in _DELIMITED:
        return CodeFilePlan(CodeFileRoute.DELIMITED, parser=_DELIMITED[ext])
    if ext in _STRUCTURED:
        return CodeFilePlan(CodeFileRoute.STRUCTURED, parser=_STRUCTURED[ext])
    if not repository_file:
        return CodeFilePlan(CodeFileRoute.TEXT)
    if ext in _DATA_WITHOUT_A_PARSER:
        return _skip(SkipCause.NO_PARSER, unsupported_file_type(ext))
    if size > limit:
        return _skip(SkipCause.TOO_LARGE, too_large)
    if _looks_binary(content):
        return _skip(SkipCause.BINARY, BINARY_FILE_SKIPPED)
    return CodeFilePlan(CodeFileRoute.TEXT)
