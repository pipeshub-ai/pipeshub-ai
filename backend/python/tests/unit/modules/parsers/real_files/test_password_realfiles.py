"""Real encrypted and look-alike files through both pipelines.

Every file is generated here. The legacy pipeline is the real ``inspect_untrusted_pdf`` and
``EventProcessor.on_event`` dispatch; the service pipeline is the real route with a real
``SmartPDFParser``. Only OCR, Docling and the per-format processors are stubs, so a call count of
zero on them is the proof that nothing reached them.
"""
from __future__ import annotations

import io
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from app.events.events import DedupDecision
from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.pdf.untrusted_pdf import (
    UnreadablePdfError,
    inspect_untrusted_pdf,
)
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from app.services.parsing.registry import ParserRegistry
from tests.support.office_samples import (
    ENCRYPTED_BY_EXTENSION,
    PLAIN_BY_EXTENSION,
    cfbf,
)
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)

MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}


def _pdf(text: str = "Quarterly figures", *, user: str | None = None, owner: str = "own", strength: int = 128) -> bytes:
    buf = io.BytesIO()
    encrypt = None if user is None else pdfencrypt.StandardEncryption(user, ownerPassword=owner, strength=strength)
    pdf = canvas.Canvas(buf, encrypt=encrypt)
    pdf.setFont("Helvetica", 11)
    for row in range(60):
        pdf.drawString(40, 800 - row * 13, f"{text} line {row} lorem ipsum dolor sit amet " * 2)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


USER_PASSWORD = {f"user password, {s}-bit": _pdf(user="s3cret", strength=s) for s in (40, 128)}
OWNER_ONLY = {f"owner password only, {s}-bit": _pdf(user="", strength=s) for s in (40, 128)}
_LOCKED = _pdf(user="s3cret", strength=128)
DAMAGED_LOCKED = {
    "locked, cut in half": _LOCKED[: len(_LOCKED) // 2],
    "locked, xref damaged": _LOCKED.replace(b"startxref", b"startxrex").replace(b"xref", b"xreg") + b"\0garbage" * 50,
}


async def _legacy_pdf(content: bytes) -> str:
    try:
        await inspect_untrusted_pdf(content, label="sample.pdf")
    except PasswordProtectedError:
        return "PW"
    except UnreadablePdfError:
        return "UNREADABLE"
    return "OK"


def _service_pdf(content: bytes) -> tuple[str, int, int]:
    primary, ocr = MagicMock(), MagicMock()
    primary.parse = AsyncMock(return_value=_ok_result())
    ocr.parse = AsyncMock(return_value=_ok_result())
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=SmartPDFParser(primary, ocr))
    client = TestClient(_build_app(registry), raise_server_exceptions=False)
    response = client.post(
        "/api/v1/parse",
        files={"file": ("sample.pdf", content, MIME["pdf"])},
        data={"mime_type": MIME["pdf"], "extension": "pdf", "provider": "default"},
    )
    code = response.json().get("error", {}).get("code") if response.status_code != 200 else None
    if response.status_code == 422 and code == "PASSWORD_REQUIRED":
        verdict = "PW"
    elif response.status_code == 200:
        verdict = "OK"
    else:
        verdict = f"HTTP{response.status_code}:{code}"
    return verdict, ocr.parse.await_count, primary.parse.await_count


@pytest.mark.asyncio
@pytest.mark.parametrize("content", USER_PASSWORD.values(), ids=USER_PASSWORD.keys())
async def test_a_pdf_with_a_user_password_is_protected_on_both_pipelines_and_reaches_no_parser(content: bytes) -> None:
    assert await _legacy_pdf(content) == "PW"
    assert _service_pdf(content) == ("PW", 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", OWNER_ONLY.values(), ids=OWNER_ONLY.keys())
async def test_a_pdf_with_only_an_owner_password_indexes_on_both_pipelines(content: bytes) -> None:
    assert await _legacy_pdf(content) == "OK"
    verdict, ocr_calls, primary_calls = _service_pdf(content)
    assert (verdict, primary_calls) == ("OK", 1)
    assert ocr_calls == 0


@pytest.mark.asyncio
async def test_a_pdf_whose_text_talks_about_passwords_is_not_protected() -> None:
    content = _pdf("This document is password protected. Enter the password. Incorrect password error.")

    assert await _legacy_pdf(content) == "OK"
    assert _service_pdf(content)[0] == "OK"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", DAMAGED_LOCKED.values(), ids=DAMAGED_LOCKED.keys())
async def test_a_damaged_locked_pdf_never_crashes_either_pipeline(content: bytes) -> None:
    assert await _legacy_pdf(content) in ("PW", "UNREADABLE")
    verdict = _service_pdf(content)[0]
    assert not verdict.startswith("HTTP5"), verdict


@pytest.mark.asyncio
async def test_a_plain_truncated_pdf_is_unreadable_not_protected() -> None:
    cut = _pdf()[:300]

    assert await _legacy_pdf(cut) == "UNREADABLE"
    assert _service_pdf(cut)[0] != "PW"


async def _legacy_office(ext: str, content: bytes) -> str:
    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    for name in ("process_docx_document", "process_excel_document", "process_pptx_document"):
        setattr(processor, name, MagicMock(side_effect=_mock_processor_gen))
    decision = DedupDecision(virtual_record_id=None, skip_indexing=False)
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=decision):
        try:
            await _drain(ep.on_event(_make_event_payload(extension=ext, buffer=content)))
        except PasswordProtectedError:
            return "PW"
    return "OK"


def _service_office(ext: str, content: bytes) -> str:
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=_ok_result())
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=parser)
    client = TestClient(_build_app(registry), raise_server_exceptions=False)
    response = client.post(
        "/api/v1/parse",
        files={"file": (f"sample.{ext}", content, MIME[ext])},
        data={"mime_type": MIME[ext], "extension": ext, "provider": "default"},
    )
    code = response.json().get("error", {}).get("code") if response.status_code != 200 else None
    if response.status_code == 422 and code == "PASSWORD_REQUIRED":
        return "PW"
    return "OK" if response.status_code == 200 else f"HTTP{response.status_code}:{code}"


def _lookalikes() -> dict[str, bytes]:
    encrypted = ENCRYPTED_BY_EXTENSION["docx"]()
    scrambled = bytearray(encrypted)
    for i in range(512, len(scrambled), 7):
        scrambled[i] ^= 0xA5
    hostile = bytearray(encrypted[:4096])
    struct.pack_into("<I", hostile, 48, 0xFFFFFFF0)
    bad_shift = bytearray(hostile)
    struct.pack_into("<I", bad_shift, 48, 0)
    struct.pack_into("<H", bad_shift, 30, 0xFFFF)
    magic = bytes.fromhex("D0CF11E0A1B11AE1")
    return {
        "legacy workbook container": cfbf(["Workbook", "SummaryInformation"]),
        "encrypted cut at the header": encrypted[:512],
        "encrypted cut after the header": encrypted[:1024],
        "encrypted body scrambled": bytes(scrambled),
        "directory sector far outside the file": bytes(hostile),
        "sector shift that is no size": bytes(bad_shift),
        "magic then random bytes": magic + bytes((i * 131 + 7) % 256 for i in range(4096)),
    }


LOOKALIKES = _lookalikes()


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
async def test_encrypted_office_files_are_protected_on_both_pipelines(ext: str) -> None:
    content = ENCRYPTED_BY_EXTENSION[ext]()

    assert await _legacy_office(ext, content) == "PW"
    assert _service_office(ext, content) == "PW"


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
async def test_plain_office_files_are_not(ext: str) -> None:
    content = PLAIN_BY_EXTENSION[ext]()

    assert await _legacy_office(ext, content) == "OK"
    assert _service_office(ext, content) == "OK"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [n for n in LOOKALIKES if n != "encrypted cut after the header"])
async def test_damaged_or_foreign_containers_are_left_to_the_parser(name: str) -> None:
    content = LOOKALIKES[name]

    assert await _legacy_office("docx", content) == "OK"
    assert _service_office("docx", content) == "OK"


@pytest.mark.asyncio
async def test_an_encrypted_file_cut_after_the_header_never_crashes() -> None:
    content = LOOKALIKES["encrypted cut after the header"]

    assert await _legacy_office("docx", content) in ("OK", "PW")
    assert not _service_office("docx", content).startswith("HTTP5")
