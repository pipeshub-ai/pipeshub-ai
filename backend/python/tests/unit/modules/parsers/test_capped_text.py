"""Log and configuration files: charsets, the head-and-tail cap, binary files, and plain-text blocks."""
from __future__ import annotations

import codecs

import pytest

from app.models.blocks import BlockType, DataFormat
from app.modules.parsers.text.capped_text import (
    BinaryFile,
    CappedText,
    is_binary,
    prepare_capped_text,
)
from app.modules.parsers.text.plain_text_parser import (
    WINDOW_CHARS,
    PlainTextParser,
    plain_text_to_blocks,
)

_JAPANESE = "設定ファイル: サーバーのホスト名とポート番号を記述します。\n" * 40


@pytest.mark.parametrize(
    ("data", "text"),
    [
        ("port = 8080\nhost = café\n".encode(), "port = 8080\nhost = café\n"),
        ("port = 8080\nhost = café\n".encode("utf-16-le"), "port = 8080\nhost = café\n"),
        ("port = 8080\nhost = café\n".encode("utf-16-be"), "port = 8080\nhost = café\n"),
        (codecs.BOM_UTF16_LE + "host = café\n".encode("utf-16-le"), "host = café\n"),
        (codecs.BOM_UTF8 + "host = café\n".encode(), "host = café\n"),
        ("naïve “quotes” — 50€\n".encode("cp1252"), "naïve “quotes” — 50€\n"),
        (_JAPANESE.encode("shift_jis"), _JAPANESE),
    ],
    ids=["utf-8", "utf-16le no bom", "utf-16be no bom", "utf-16 bom", "utf-8 bom", "cp1252", "shift-jis"],
)
def test_each_charset_reads_as_the_text_written(data: bytes, text: str) -> None:
    result = prepare_capped_text(data)
    assert isinstance(result, CappedText)
    assert result.text == text
    assert result.skipped is None


def test_a_file_with_nul_bytes_that_is_not_utf16_is_binary() -> None:
    assert isinstance(prepare_capped_text(b"\x7fELF\x02\x01\x01\x00\x00\x00binary\x00\x00data"), BinaryFile)
    assert is_binary(b"PK\x03\x04\x14\x00\x00\x00")
    assert not is_binary("a = 1\n".encode("utf-16-le"))
    assert not is_binary(b"plain = text\n")


def _log(lines: int) -> bytes:
    return "".join(f"2026-11-13 line {i:06d} " + "x" * 50 + "\n" for i in range(lines)).encode()


def test_an_oversized_log_keeps_its_head_and_tail_cut_at_line_breaks() -> None:
    data = _log(20_000)
    result = prepare_capped_text(data, max_bytes=200_000)
    assert isinstance(result, CappedText)
    head_end, tail_start = result.skipped  # type: ignore[misc]
    assert data[head_end - 1:head_end] == b"\n" and data[tail_start - 1:tail_start] == b"\n"
    assert result.text.startswith("2026-11-13 line 000000 ")
    assert result.text.rstrip("\n").endswith(f"line {19_999:06d} " + "x" * 50)
    assert f"bytes {head_end:,} to {tail_start:,} of {len(data):,}" in result.text
    assert len(result.text.encode()) <= 200_000 + 200
    for line in result.text.splitlines():
        assert line.startswith(("2026-11-13 line ", "[... "))


def test_an_oversized_utf16_log_is_cut_on_whole_characters() -> None:
    data = "".join(f"line {i:05d} é\n" for i in range(20_000)).encode("utf-16-le")
    result = prepare_capped_text(data, max_bytes=100_000)
    assert isinstance(result, CappedText)
    assert "�" not in result.text
    assert result.text.startswith("line 00000 é")
    assert result.text.rstrip("\n").endswith("line 19999 é")


def test_a_log_without_line_breaks_is_still_capped() -> None:
    result = prepare_capped_text(b"x" * 1_000_000, max_bytes=100_000)
    assert isinstance(result, CappedText)
    assert len(result.text) < 101_000


def test_hash_comments_stay_text_not_headings() -> None:
    container = plain_text_to_blocks("# Service configuration\n[server]\nport = 8080\n")
    assert [b.type for b in container.blocks] == [BlockType.TEXT]
    assert container.blocks[0].format == DataFormat.TXT
    assert container.blocks[0].data.startswith("# Service configuration")
    assert container.block_groups == []


def test_blocks_hold_whole_lines_and_stay_within_a_window() -> None:
    text = "".join(f"{i:05d} " + "y" * 90 + "\n" for i in range(500))
    container = plain_text_to_blocks(text)
    assert len(container.blocks) > 1
    assert all(len(b.data) <= WINDOW_CHARS for b in container.blocks)
    assert "\n".join(b.data for b in container.blocks) == text.rstrip("\n")
    assert [b.index for b in container.blocks] == list(range(len(container.blocks)))


def test_a_line_longer_than_a_window_is_split() -> None:
    container = plain_text_to_blocks("word " * 3000)
    assert all(len(b.data) <= WINDOW_CHARS for b in container.blocks)
    assert "".join(b.data for b in container.blocks).split() == ["word"] * 3000


def test_blank_text_makes_no_blocks() -> None:
    assert plain_text_to_blocks("\n\n  \n").blocks == []


async def test_the_parsing_service_parser_caps_and_reads_plain_text() -> None:
    result = await PlainTextParser().parse(b"# comment\nkey = value\n", "app.cfg")
    assert result.block_container is not None
    assert result.block_container.blocks[0].data == "# comment\nkey = value"


async def test_the_parsing_service_parser_refuses_a_binary_file() -> None:
    from app.services.parsing.interface import UnsupportedFormatError

    with pytest.raises(UnsupportedFormatError):
        await PlainTextParser().parse(b"\x00\x01\x02\x00binary\x00", "core.log")


@pytest.mark.parametrize("path", ["/bin/ls", "/bin/sh", "/usr/lib/dyld", "/bin/bash"])
def test_a_real_executable_is_binary(path: str) -> None:
    from pathlib import Path

    if not Path(path).exists():
        pytest.skip(f"{path} is not on this host")
    assert isinstance(prepare_capped_text(Path(path).read_bytes()), BinaryFile)


@pytest.mark.parametrize(
    "data",
    [
        b"\x00" * 4096 + b"\x7fheader" + b"\x00" * 4096,  # NUL padding at both byte parities
        (b"A\x00" * 100 + b"\x00\x00" * 900),  # text then padding: both parities mostly NUL
        bytes(range(256)) * 32,  # every byte value
        b"\xff\xfe" + b"\x00\xd8" * 500,  # a UTF-16 mark followed by lone surrogates
    ],
    ids=["nul-padded", "padded-tail", "all-bytes", "bom-then-garbage"],
)
def test_nul_padded_or_undecodable_bytes_are_binary(data: bytes) -> None:
    assert isinstance(prepare_capped_text(data), BinaryFile)
