"""The new readers on edge input: surrogate pairs and font code pages in mail RTF, damaged
DEFLATE data in zip members, UTF-16 text outside ASCII, and long unbroken lines."""
from __future__ import annotations

import io
import time
import zipfile
import zlib
from unittest.mock import patch

import compressed_rtf
import pytest

from app.modules.parsers.email.rtf_deencapsulate import deencapsulate_html
from app.services.parsing.interface import UnreadableFileError
from tests.support import new_type_samples as samples
from tests.unit.modules.parsers.test_msg_to_html import _message, _Props

_SMILE = "\U0001f600"


def _page_of_rtf(rtf: bytes) -> str:
    from app.modules.parsers.email.msg_to_html import msg_to_html

    message = _message(properties=_Props(binary={0x1009: compressed_rtf.compress(rtf)}))
    with patch("oxmsg.Message.load", return_value=message):
        return msg_to_html(b"\xd0\xcf\x11\xe0")


def test_a_surrogate_pair_in_an_rtf_text_body_is_one_character() -> None:
    page = _page_of_rtf(rb"{\rtf1\ansi\uc1 hi \u-10179?\u-8704? there\par}")
    assert _SMILE in page
    page.encode("utf-8")


def test_a_lone_surrogate_in_an_rtf_text_body_is_replaced_not_fatal() -> None:
    page = _page_of_rtf(rb"{\rtf1\ansi\uc1 hi \u-10179? there\par}")
    assert "�" in page
    page.encode("utf-8")


async def test_the_parsing_service_reads_such_a_mail() -> None:
    from app.modules.parsers.email.msg_parser import MsgParser
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )

    message = _message(properties=_Props(binary={0x1009: compressed_rtf.compress(
        rb"{\rtf1\ansi\uc1 hi \u-10179?\u-8704? there\par}")}))
    with patch("oxmsg.Message.load", return_value=message), \
         patch("app.modules.parsers.parse_pool.run_blocking", side_effect=lambda fn, *a, **_k: fn(*a)):
        await MsgParser(SelectolaxHtmlParser(allow_remote_fetch=False)).parse(b"\xd0\xcf\x11\xe0", "m.msg")


def test_a_surrogate_pair_in_encapsulated_html_is_one_character() -> None:
    html = deencapsulate_html(rb"{\rtf1\ansi\fromhtml1 {\*\htmltag64 <p>}\uc1 a\u-10179?\u-8704?b{\*\htmltag72 </p>}}")
    assert f"a{_SMILE}b" in html
    assert "�" not in html


def test_text_is_decoded_with_its_font_s_code_page() -> None:
    rtf = (
        rb"{\rtf1\ansi\ansicpg1252\fromhtml1 {\fonttbl{\f0\fswiss Arial;}{\f1\fcharset204 Arial;}}"
        rb"{\*\htmltag64 <p>}\f1 \'cf\'f0\'e8\'e2\'e5\'f2\f0  caf\'e9{\*\htmltag72 </p>}}"
    )
    html = deencapsulate_html(rtf)
    assert "Привет" in html
    assert "café" in html


def test_markup_in_htmltag_groups_keeps_the_header_code_page() -> None:
    rtf = (
        rb"{\rtf1\ansi\ansicpg1252\fromhtml1 {\fonttbl{\f1\fcharset204 Arial;}}"
        rb'\f1 {\*\htmltag64 <p title="caf\'e9">}x{\*\htmltag72 </p>}}'
    )
    assert 'title="café"' in deencapsulate_html(rtf)


def _corrupt_member(package: bytes, name: str) -> bytes:
    """*package* with *name*'s DEFLATE data replaced by bytes of the same length that do not inflate."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        info = archive.getinfo(name)
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        size = info.compress_size
    data = bytearray(package)
    data[offset:offset + size] = b"\xff" * size
    return bytes(data)


def test_a_package_member_that_does_not_inflate_is_damaged() -> None:
    from app.modules.parsers.ooxml.main_part import as_plain_package

    for member in ("[Content_Types].xml", "word/document.xml"):
        damaged = _corrupt_member(samples.make_docm("x"), member)
        with pytest.raises(UnreadableFileError):
            as_plain_package(damaged, "word", label="docm")


def test_an_odt_manifest_that_does_not_inflate_is_left_to_the_converter() -> None:
    from app.modules.parsers.encryption import raise_if_odf_encrypted

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("META-INF/manifest.xml", "<manifest/>" * 50)
    damaged = _corrupt_member(out.getvalue(), "META-INF/manifest.xml")
    with zipfile.ZipFile(io.BytesIO(damaged)) as archive, pytest.raises(zlib.error):
        archive.read("META-INF/manifest.xml")
    raise_if_odf_encrypted(damaged)


async def test_a_capped_log_keeps_its_own_extension_for_the_parsing_service() -> None:
    from app.config.file_types import by_extension, parse_format
    from app.modules.parsers.file_prep import prepare_file

    log = by_extension("log")
    assert log is not None
    prepared = await prepare_file(b"a line\n", log, extension="unknown", mime_type="text/x-log")
    assert prepared is not None and prepared.extension == "log"
    assert parse_format(prepared.mime_type, prepared.extension) == "log"
    kept = await prepare_file(b"a line\n", log, extension="LOG", mime_type=None)
    assert kept is not None and kept.extension == "LOG"


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_text_with_letters_outside_ascii_is_text(codec: str) -> None:
    from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text

    text = "name=张三\nport=8080\n" * 100
    result = prepare_capped_text(text.encode(codec))
    assert isinstance(result, CappedText)
    assert result.text == text


def test_a_long_unbroken_line_is_split_in_linear_time() -> None:
    from app.modules.parsers.text.plain_text_parser import plain_text_to_blocks

    started = time.perf_counter()
    size = 20 * 1024 * 1024
    from app.modules.parsers.text.plain_text_parser import _split_line

    parts = _split_line("x" * size)
    assert time.perf_counter() - started < 1.0
    assert sum(len(p) for p in parts) == size
    assert sum(len(b.data) for b in plain_text_to_blocks("x" * 100_000).blocks) == 100_000


@pytest.mark.parametrize("text", ["имя=Иван\n" * 100, "日本語のログ行\n" * 200, "x=\U0001f600\n" * 100])
@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_text_in_any_script_is_text(text: str, codec: str) -> None:
    from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text

    result = prepare_capped_text(text.encode(codec))
    assert isinstance(result, CappedText)
    assert result.text == text


def test_no_binary_on_this_host_reads_as_text() -> None:
    from pathlib import Path

    from app.modules.parsers.text.capped_text import BinaryFile, prepare_capped_text

    paths = [p for d in ("/bin", "/usr/bin", "/usr/lib") for p in sorted(Path(d).glob("*"))[:150] if p.is_file()]
    misread = []
    for path in paths:
        # Only the head: reading a whole runner binary (/bin/chromium) took over 60 s in CI.
        try:
            with path.open("rb") as file:
                head = file.read(64 * 1024)
        except OSError:
            continue
        if b"\x00" in head[:8192] and not isinstance(prepare_capped_text(head), BinaryFile):
            misread.append(str(path))
    assert misread == []


@pytest.mark.parametrize(
    "text",
    [("x" * 600 + "\n") * 12, "k=" + "v" * 3000 + "\n", ("y" * 200 + "\n") * 12,
     ("имя Иван " * 80 + "\n") * 6],
    ids=["600-char lines", "one 3000-char line", "200-char lines", "long Cyrillic lines"],
)
@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_text_with_long_lines_is_text(text: str, codec: str) -> None:
    from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text

    result = prepare_capped_text((text * 2).encode(codec))
    assert isinstance(result, CappedText)
    assert result.text == text * 2


@pytest.mark.parametrize(
    "text",
    ["name=" + "张三" * 1500 + "\n", "ข้อความภาษาไทยยาวมาก" * 400, "مرحبابالعالمالعربي" * 400,
     "😀🎉👍🏽🇯🇵" * 1200, "名前=田中太郎、ひらがなとカタカナ" * 300, "设定文件服务器主机名端口号" * 600,
     "サーバーのホスト名とポート番号" * 500, "name=" + "汉字" * 1500 + "\n"],
    ids=["CJK", "Thai", "Arabic", "emoji", "Japanese", "Han alone", "kana with a long-vowel mark",
         "ideographs whose code units are two ASCII bytes"],
)
@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_text_on_one_long_line_without_spaces_is_text(text: str, codec: str) -> None:
    from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text

    result = prepare_capped_text(text.encode(codec))
    assert isinstance(result, CappedText)
    assert result.text.startswith(text[:50])


@pytest.mark.parametrize("text", [
    "設定ファイル: サーバーのホスト名とポート番号を記述します。\n", "服务器配置文件，主机名和端口号。\n" * 5,
    "Fichier de configuration du serveur: hôte et port résumé.\n" * 5, "plain ascii configuration value = 42\n",
])
@pytest.mark.parametrize("encoding", ["shift_jis", "gbk", "mac_roman", "cp1252", "utf-8"])
def test_text_in_a_legacy_encoding_is_not_read_as_utf16(text: str, encoding: str) -> None:
    from app.modules.parsers.text.capped_text import _codec

    try:
        data = text.encode(encoding)
    except UnicodeEncodeError:
        pytest.skip("not encodable")
    codec = _codec(data)[0]
    assert codec is None or not codec.startswith("utf-16")
