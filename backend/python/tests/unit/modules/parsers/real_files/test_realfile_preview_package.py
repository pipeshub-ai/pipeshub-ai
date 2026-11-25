"""Previews of Office files LibreOffice refuses: rebuilt and retried, or refused with a 422."""

from __future__ import annotations

import contextlib
import io
import logging
import shutil
import warnings
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from lxml import etree
from pptx import Presentation

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.connectors.api.router import convert_buffer_to_pdf_stream
from app.utils import ooxml_repair, pdf_stream_conversion
from app.utils.ooxml_repair import rebuild_package
from app.utils.pdf_stream_conversion import libreoffice_to_pdf
from app.utils.user_messages import FILE_PREVIEW_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

needs_impress = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed on this host")

# The web app falls back to the original file on this reason code.
UNPREVIEWABLE = {"reasonCode": "conversion_failed", "message": FILE_PREVIEW_UNAVAILABLE}
NOT_LOADED = b"Error: source file could not be loaded\n"


def _deck() -> bytes:
    deck = Presentation()
    for n in range(2):
        deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = f"Slide {n}"
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def _entries(content: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return [(n, z.read(n)) for n in z.namelist()]


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in entries:
                z.writestr(name, data)
    return out.getvalue()


def _wrong_local_crc(content: bytes) -> bytes:
    data = bytearray(content)
    at = data.find(b"PK\x03\x04", 10)
    data[at + 14 : at + 18] = b"\1\2\3\4"
    return bytes(data)


DEFECTS = {
    "duplicate entry": lambda c: _zip([*_entries(c), _entries(c)[-1]]),
    "part differing only in case": lambda c: _zip([*_entries(c), ("PPT/presentation.xml", b"<x/>")]),
    "missing app.xml": lambda c: _zip([e for e in _entries(c) if e[0] != "docProps/app.xml"]),
    "missing core.xml": lambda c: _zip([e for e in _entries(c) if e[0] != "docProps/core.xml"]),
    "bytes before the zip": lambda c: b"xx" + c,
    "local header CRC differs": _wrong_local_crc,
    "backslash names": lambda c: _zip([(n.replace("/", "\\"), d) for n, d in _entries(c)]),
}


@needs_impress
@pytest.mark.parametrize("defect", DEFECTS)
async def test_a_deck_with_a_package_defect_is_previewed(tmp_path: Path, defect: str) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(DEFECTS[defect](_deck()))

    pdf = Path(await libreoffice_to_pdf(str(source), str(tmp_path)))

    assert pdf.read_bytes().startswith(b"%PDF")
    import pypdfium2

    assert len(pypdfium2.PdfDocument(str(pdf))) == 2


@needs_impress
async def test_a_deck_that_cannot_be_rebuilt_is_a_422(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(_deck()[: len(_deck()) // 2])

    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path))

    assert (raised.value.status_code, raised.value.detail) == (422, UNPREVIEWABLE)
    assert "source file could not be loaded" in caplog.text


@needs_impress
@pytest.mark.parametrize("name", ["x" * 250 + ".pptx", "日" * 83 + ".pptx", "x" * 400 + ".pptx", "x" * 300])
async def test_a_long_record_name_does_not_stop_the_preview(name: str) -> None:
    response = await convert_buffer_to_pdf_stream(_deck(), name, "pptx")
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body.startswith(b"%PDF")


def _fake_soffice(monkeypatch: pytest.MonkeyPatch, stderr: bytes, returncode: int = 0) -> list[str]:
    ran: list[str] = []

    class _Process:
        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", stderr

    _Process.returncode = returncode

    async def fake_exec(*args: str, **_kwargs: object) -> _Process:
        ran.append(args[-1])
        return _Process()

    monkeypatch.setattr(pdf_stream_conversion.asyncio, "create_subprocess_exec", fake_exec)
    return ran


async def test_a_host_that_cannot_open_a_known_good_file_stays_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _fake_soffice(monkeypatch, NOT_LOADED)
    source = tmp_path / "deck.pptx"
    source.write_bytes(_deck())
    with (
        patch.object(pdf_stream_conversion.libreoffice_convert, "can_convert_known_good_file", AsyncMock(return_value=False)),
        pytest.raises(HTTPException) as raised,
    ):
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert raised.value.status_code == 500
    assert len(ran) == 2, "the file, then its rebuilt copy, and no more"


@pytest.mark.parametrize("returncode", [0, 1], ids=["LibreOffice 25", "LibreOffice 26"])
async def test_a_file_that_is_not_loaded_is_rebuilt_then_a_422_whatever_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    ran = _fake_soffice(monkeypatch, NOT_LOADED, returncode)
    source = tmp_path / "deck.pptx"
    source.write_bytes(_deck())
    with (
        patch.object(pdf_stream_conversion.libreoffice_convert, "can_convert_known_good_file", AsyncMock(return_value=True)),
        pytest.raises(HTTPException) as raised,
    ):
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert (raised.value.status_code, raised.value.detail) == (422, UNPREVIEWABLE)
    assert len(ran) == 2


@pytest.mark.parametrize("returncode", [-9, 77])
async def test_a_crash_or_host_failure_is_a_500_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    stderr = NOT_LOADED if returncode < 0 else b"user installation could not be completed"
    ran = _fake_soffice(monkeypatch, stderr, returncode)
    source = tmp_path / "deck.pptx"
    source.write_bytes(_deck())
    with pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert raised.value.status_code == 500
    assert len(ran) == 1


async def test_a_failed_export_is_logged_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ran = _fake_soffice(monkeypatch, b"impl_store failed: 0x909(Error Area:Io Class:Space)")
    source = tmp_path / "deck.pptx"
    source.write_bytes(_deck())
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert raised.value.status_code == 500
    assert "0x909" in caplog.text
    assert len(ran) == 1


@pytest.mark.parametrize(
    "content",
    [
        b"", b"not a zip", _zip([("a.txt", b"x")]), _deck()[:-30],
        _zip([*_entries(_deck()), ("../evil.xml", b"x")]),
        _zip([(n, b"<broken" if n == "_rels/.rels" else d) for n, d in _entries(_deck())]),
    ],
    ids=["empty", "not zip", "other zip", "truncated", "traversal", "broken rels"],
)
def test_what_cannot_be_rebuilt_is_left_alone(tmp_path: Path, content: bytes) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(content)
    assert rebuild_package(source, target) is False
    assert not target.exists()


@pytest.mark.parametrize("limit", ["MAX_ENTRIES", "MAX_UNCOMPRESSED_BYTES"])
def test_a_package_over_the_limits_is_not_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    monkeypatch.setattr(ooxml_repair, limit, 10)
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_deck())
    assert rebuild_package(source, target) is False
    assert not target.exists()


def _with_rels(body: str, doctype: str = "") -> bytes:
    rels = (
        f'{doctype}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{body}</Relationships>"
    ).encode()
    return _zip([(n, rels if n == "_rels/.rels" else d) for n, d in _entries(_deck())])


def test_external_entities_are_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("TOPSECRET")
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_with_rels("&e;", f'<!DOCTYPE r [<!ENTITY e SYSTEM "{secret.as_uri()}">]>'))
    assert rebuild_package(source, target)
    assert b"TOPSECRET" not in zipfile.ZipFile(target).read("_rels/.rels")


def test_only_relationships_to_missing_parts_are_dropped(tmp_path: Path) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_with_rels(
        '<Relationship Id="external" Type="t" Target="https://docs.invalid/" TargetMode="External"/>'
        '<Relationship Id="present" Type="t" Target="/ppt/presentation.xml"/>'
        '<Relationship Id="gone" Type="t" Target="docProps/gone.xml"/>'
    ))
    assert rebuild_package(source, target)
    root = etree.fromstring(zipfile.ZipFile(target).read("_rels/.rels"))
    assert [r.get("Id") for r in root] == ["external", "present"]


def test_the_rebuilt_package_keeps_every_part(tmp_path: Path) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_deck())
    assert rebuild_package(source, target)
    before, after = dict(_entries(_deck())), dict(_entries(target.read_bytes()))
    assert before.keys() == after.keys()
    assert all(before[n] == after[n] for n in before if n != "_rels/.rels")


def test_a_refusal_logs_nothing_from_the_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    content = _zip([("a.txt", b"x"), ("SECRETNAME/../x", b"y")])
    deck = _zip([(n, b"<SECRETNAME" if n == "_rels/.rels" else d) for n, d in _entries(_deck())])
    for data in (content, deck):
        source.write_bytes(data)
        with caplog.at_level(logging.INFO):
            assert rebuild_package(source, target) is False
    assert "not rebuilt" in caplog.text
    assert "SECRETNAME" not in caplog.text


async def test_rebuilding_counts_against_the_conversion_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[str] = []

    class _Slots:
        @contextlib.asynccontextmanager
        async def slot(self) -> AsyncIterator[None]:
            held.append("held")
            yield
            held.pop()

    _fake_soffice(monkeypatch, NOT_LOADED)
    monkeypatch.setattr(pdf_stream_conversion.libreoffice_convert, "conversion_slots", _Slots())
    during: list[int] = []
    monkeypatch.setattr(pdf_stream_conversion, "rebuild_package", lambda *_a: bool(during.append(len(held))))
    source = tmp_path / "deck.bin"
    source.write_bytes(b"x")
    with pytest.raises(HTTPException):
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert during == [1]
