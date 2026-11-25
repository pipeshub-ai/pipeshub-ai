"""Previews of Office files LibreOffice refuses: rebuilt and retried, or refused with a 422."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import io
import logging
import shutil
import struct
import threading
import time
import tracemalloc
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from lxml import etree
from pptx import Presentation

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.connectors.api.router import convert_buffer_to_pdf_stream
from app.utils import libreoffice_convert, ooxml_repair, pdf_stream_conversion
from app.utils.ooxml_repair import rebuild_package
from app.utils.pdf_stream_conversion import libreoffice_to_pdf
from app.utils.user_messages import FILE_PREVIEW_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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
        '<Relationship Id="above root" Type="t" Target="../ppt/presentation.xml"/>'
        '<Relationship Id="gone" Type="t" Target="docProps/gone.xml"/>'
    ))
    assert rebuild_package(source, target)
    root = etree.fromstring(zipfile.ZipFile(target).read("_rels/.rels"))
    assert [r.get("Id") for r in root] == ["external", "present", "above root"]


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


def _lying_rels(inflated: int) -> bytes:
    """A deck whose _rels/.rels inflates to *inflated* bytes but declares 900."""
    data = bytearray(_zip([(n, b"\0" * inflated if n == "_rels/.rels" else d) for n, d in _entries(_deck())]))
    at = data.find(b"PK\x01\x02")
    while at >= 0:
        size = struct.unpack_from("<H", data, at + 28)[0]
        if data[at + 46 : at + 46 + size] == b"_rels/.rels":
            struct.pack_into("<I", data, at + 24, 900)
        at = data.find(b"PK\x01\x02", at + 46)
    return bytes(data)


def test_relationships_that_lie_about_their_size_are_not_inflated(tmp_path: Path) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_lying_rels(200 * 1024 * 1024))
    tracemalloc.start()
    try:
        assert rebuild_package(source, target) is False
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 20 * 1024 * 1024


def test_an_oversized_directory_is_refused_before_it_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ooxml_repair, "_MAX_DIRECTORY_BYTES", 100)
    monkeypatch.setattr(ooxml_repair, "_parts", lambda _a: pytest.fail("the directory was loaded"))
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    source.write_bytes(_deck())
    assert rebuild_package(source, target) is False


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_compression_office_does_not_use_is_refused(tmp_path: Path, method: int) -> None:
    source, target = tmp_path / "in.pptx", tmp_path / "out.pptx"
    with zipfile.ZipFile(source, "w", method) as z:
        for name, data in _entries(_deck()):
            z.writestr(name, data)
    assert rebuild_package(source, target) is False


@pytest.mark.parametrize("how", ["cancelled", "cancelled twice", "timed out"])
async def test_the_conversion_slot_is_held_until_the_rebuild_thread_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    started, ended = threading.Event(), threading.Event()
    slots = libreoffice_convert.conversion_slots
    in_use_at_end: list[int] = []

    def slow(_source: Path, _target: Path, stopped: Callable[[], bool]) -> bool:
        started.set()
        while not stopped():
            threading.Event().wait(0.01)
        threading.Event().wait(0.1)
        in_use_at_end.append(slots.in_use)
        ended.set()
        return False

    monkeypatch.setattr(pdf_stream_conversion, "rebuild_package", slow)
    timeout = 0.2 if how == "timed out" else 30.0
    task = asyncio.create_task(pdf_stream_conversion._rebuild_package(str(tmp_path / "a"), str(tmp_path / "b"), timeout=timeout))
    await asyncio.to_thread(started.wait, 5)
    if how != "timed out":
        task.cancel()
        if how == "cancelled twice":
            await asyncio.sleep(0.03)
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(HTTPException) as raised:
            await task
        assert raised.value.status_code == 504
    assert ended.is_set()
    assert in_use_at_end == [1]
    assert slots.in_use == 0


async def test_files_that_fail_together_share_one_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[str] = []

    async def fake_run(_binary: bytes, input_ext: str, output_ext: str, stem: str = "") -> SimpleNamespace:
        runs.append(f"{input_ext}>{output_ext}")
        await asyncio.sleep(0.05)
        return SimpleNamespace(returncode=0, output=b"x")

    monkeypatch.setattr(libreoffice_convert, "_run_libreoffice", fake_run)
    monkeypatch.setattr(libreoffice_convert, "_format_probe_results", {})
    monkeypatch.setattr(libreoffice_convert, "_probes_in_flight", {})
    answers = await asyncio.gather(*(libreoffice_convert.can_convert_known_good_file("pptx", "pdf") for _ in range(5)))
    assert answers == [True] * 5
    assert len(runs) == 2, "one sample made and read once"


async def test_an_old_success_is_checked_again_before_a_final_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, output=None)

    monkeypatch.setattr(libreoffice_convert, "_run_libreoffice", failing_run)
    long_ago = time.monotonic() - 3600
    monkeypatch.setattr(libreoffice_convert, "_format_probe_results", {("pptx", "pdf"): (True, long_ago)})
    assert await libreoffice_convert.can_convert_known_good_file("pptx", "pdf") is False


async def test_the_shared_probe_runs_in_no_callers_context(monkeypatch: pytest.MonkeyPatch) -> None:
    caller: contextvars.ContextVar[str | None] = contextvars.ContextVar("caller", default=None)
    seen: list[str | None] = []

    async def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        seen.append(caller.get())
        return SimpleNamespace(returncode=0, output=b"x")

    monkeypatch.setattr(libreoffice_convert, "_run_libreoffice", fake_run)
    monkeypatch.setattr(libreoffice_convert, "_format_probe_results", {})
    caller.set("org-1")
    assert await libreoffice_convert.can_convert_known_good_file("pptx", "pdf") is True
    assert seen == [None, None]


async def test_a_file_name_cannot_forge_a_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _fake_soffice(monkeypatch, NOT_LOADED)
    source = tmp_path / "in.x\nCRITICAL forged"
    source.write_bytes(b"x")
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException):
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert all("\n" not in r.getMessage() for r in caplog.records)


async def test_a_very_long_extension_is_a_refusal_not_an_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_soffice(monkeypatch, NOT_LOADED)
    source = tmp_path / ("a." + "x" * 240)
    source.write_bytes(_deck())
    with pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path))
    assert raised.value.status_code == 500
