"""Plain text (logs, configuration files) as text blocks, with no markup read into it.

A ``#`` comment in a .cfg or a ``*`` in a log line is text, not a heading or a list, so these
files are not read as Markdown. Lines are grouped into windows cut at line breaks, so a block
holds whole lines and the citation points at the lines that matched.
"""
from __future__ import annotations

from typing import Any

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.services.parsing.interface import ParseResult

WINDOW_CHARS = 4_000


def _split_line(line: str) -> list[str]:
    """A line longer than a window, cut at the last space before each window's end when there is one."""
    parts: list[str] = []
    while len(line) > WINDOW_CHARS:
        cut = line.rfind(" ", 0, WINDOW_CHARS)
        cut = cut + 1 if cut > 0 else WINDOW_CHARS
        parts.append(line[:cut])
        line = line[cut:]
    if line:
        parts.append(line)
    return parts


def _windows(text: str) -> list[str]:
    windows: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        for piece in _split_line(line) if len(line) > WINDOW_CHARS else [line]:
            if current and size + len(piece) > WINDOW_CHARS:
                windows.append("".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece)
    if current:
        windows.append("".join(current))
    return [w for w in (w.strip("\r\n") for w in windows) if w.strip()]


def plain_text_to_blocks(text: str) -> BlocksContainer:
    """*text* as TEXT blocks of whole lines, each at most ``WINDOW_CHARS`` characters."""
    blocks = [
        Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=window)
        for i, window in enumerate(_windows(text))
    ]
    return BlocksContainer(blocks=blocks)


class PlainTextParser:
    """The parsing service's parser for .log and .cfg: capped, decoded and split into lines."""

    async def parse(self, content: bytes, record_name: str, config: dict[str, Any] | None = None) -> ParseResult:
        from app.modules.parsers import parse_pool
        from app.modules.parsers.text.capped_text import BinaryFile, prepare_capped_text
        from app.services.parsing.interface import UnsupportedFormatError
        from app.services.resource_governor.kinds import ParseKind
        from app.utils.user_errors import binary_text_file

        extension = record_name.rsplit(".", 1)[-1].lower() if "." in record_name else "txt"
        prepared = prepare_capped_text(content)
        if isinstance(prepared, BinaryFile):
            raise UnsupportedFormatError(extension, binary_text_file(extension))
        text = prepared.text
        if parse_pool.should_offload(len(text)):
            container = await parse_pool.submit(
                plain_text_to_blocks, text, label=record_name, size=len(text), kind=ParseKind.TEXT
            )
        else:
            container = plain_text_to_blocks(text)
        return ParseResult(block_container=container, metadata={"record_name": record_name})

    def supported_formats(self) -> list[str]:
        return ["log", "cfg"]
