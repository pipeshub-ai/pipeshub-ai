"""The legacy in-process pipeline stops on a password-protected file before any parser or OCR runs."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.events import DedupDecision
from app.exceptions.indexing_exceptions import PasswordProtectedError
from tests.support.office_samples import ENCRYPTED_BY_EXTENSION, PLAIN_BY_EXTENSION
from tests.support.pdf_samples import owner_password_pdf, password_protected_pdf
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

PROCESSOR_FOR = {"docx": "process_docx_document", "xlsx": "process_excel_document", "pptx": "process_pptx_document"}


def _processor(ep_parts: tuple) -> tuple:
    ep, _, processor, gp = ep_parts
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    for name in (*PROCESSOR_FOR.values(), "process_pdf_with_docling", "process_pdf_document_with_ocr",
                 "process_pdf_document"):
        setattr(processor, name, MagicMock(side_effect=_mock_processor_gen))
    return ep, processor


def _no_dedup(ep: object) -> AbstractContextManager:
    return patch.object(
        ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
        return_value=DedupDecision(virtual_record_id=None, skip_indexing=False),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
async def test_an_encrypted_office_file_raises_the_typed_error_before_its_parser(ext: str) -> None:
    ep, processor = _processor(_make_event_processor())

    with _no_dedup(ep), pytest.raises(PasswordProtectedError):
        await _drain(ep.on_event(_make_event_payload(extension=ext, buffer=ENCRYPTED_BY_EXTENSION[ext]())))

    getattr(processor, PROCESSOR_FOR[ext]).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
async def test_a_plain_office_file_still_reaches_its_parser(ext: str) -> None:
    ep, processor = _processor(_make_event_processor())

    with _no_dedup(ep):
        await _drain(ep.on_event(_make_event_payload(extension=ext, buffer=PLAIN_BY_EXTENSION[ext]())))

    getattr(processor, PROCESSOR_FOR[ext]).assert_called_once()


@pytest.mark.asyncio
async def test_a_password_protected_pdf_raises_the_typed_error_and_skips_every_pdf_parser() -> None:
    ep, processor = _processor(_make_event_processor())

    with _no_dedup(ep), pytest.raises(PasswordProtectedError):
        await _drain(ep.on_event(_make_event_payload(extension="pdf", buffer=password_protected_pdf())))

    processor.process_pdf_with_docling.assert_not_called()
    processor.process_pdf_document_with_ocr.assert_not_called()


@pytest.mark.asyncio
async def test_an_owner_password_pdf_is_indexed() -> None:
    ep, processor = _processor(_make_event_processor())

    with _no_dedup(ep), patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
        await _drain(ep.on_event(_make_event_payload(extension="pdf", buffer=owner_password_pdf())))

    processor.process_pdf_with_docling.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["docm", "xlsm", "pptm"])
async def test_an_encrypted_macro_enabled_file_raises_the_typed_error(ext: str) -> None:
    from tests.support.office_samples import ENCRYPTED_MACRO_BY_EXTENSION

    ep, _ = _processor(_make_event_processor())

    with _no_dedup(ep), pytest.raises(PasswordProtectedError):
        await _drain(ep.on_event(_make_event_payload(extension=ext, buffer=ENCRYPTED_MACRO_BY_EXTENSION[ext]())))
