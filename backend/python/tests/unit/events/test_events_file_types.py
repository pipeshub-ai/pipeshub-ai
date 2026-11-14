"""The dispatch for the new file types: which handler each reaches, and the prep step both pipelines share."""
from __future__ import annotations

import io
import os
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.events import DedupDecision, EventProcessor
from app.exceptions.indexing_exceptions import PasswordProtectedError
from tests.support import new_type_samples as samples
from tests.support.office_samples import encrypted_docm
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _routed(processor: object) -> list[str]:
    return [name for name, *_ in processor.method_calls if name.startswith("process_")]  # type: ignore[attr-defined]


async def _dispatch(ext: str | None, mime: str, content: bytes, *, record_type: str = "FILE",
                    name: str | None = None) -> tuple[object, list[str]]:
    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": record_type}
    for method in ("process_docx_document", "process_pptx_document", "process_excel_document",
                   "process_msg_document", "process_libreoffice_document", "process_plain_text_document",
                   "process_txt_document", "process_code_document", "process_html_document"):
        setattr(processor, method, MagicMock(side_effect=_mock_processor_gen))
    processor._mark_record = AsyncMock()
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        await _drain(ep.on_event(_make_event_payload(
            extension=ext, mime_type=mime, buffer=content, record_name=name or f"Sample.{ext}",
        )))
    return processor, _routed(processor)


@pytest.mark.parametrize(
    ("ext", "content", "handler"),
    [
        ("docm", samples.make_docm("s"), "process_docx_document"),
        ("dotx", samples.make_dotx("s"), "process_docx_document"),
        ("potx", samples.make_potx("s"), "process_pptx_document"),
        ("xlsm", samples.make_xlsm("s"), "process_excel_document"),
        ("msg", samples.msg_with_text_body(), "process_msg_document"),
        ("odt", samples.make_odt("s"), "process_libreoffice_document"),
        ("rtf", samples.make_rtf("s"), "process_libreoffice_document"),
        ("log", samples.make_log("s"), "process_plain_text_document"),
        ("cfg", samples.make_cfg("s"), "process_plain_text_document"),
    ],
)
@pytest.mark.parametrize("mime", ["application/octet-stream", "text/plain"])
@pytest.mark.parametrize("record_type", ["FILE", "CODE_FILE"])
async def test_the_extension_decides_the_handler_whatever_the_stored_mime(
    ext: str, content: bytes, handler: str, mime: str, record_type: str
) -> None:
    if ext in ("log", "cfg") and mime == "text/plain" and record_type == "FILE":
        handler = "process_txt_document"  # D-16: main read these whole, as text; they still are
    _, routed = await _dispatch(ext, mime, content, record_type=record_type)
    assert routed == [handler]


@pytest.mark.parametrize(
    ("ext", "mime", "handler"),
    [
        # Cells main already read keep main's parser (D-15, D-16).
        ("log", "application/json", "process_structured_document"),
        ("docm", "application/pdf", "_dispatch_pdf_binary"),
        ("xlsm", "application/vnd.ms-excel", "process_xls_document"),
        ("rtf", "application/msword", "process_doc_document"),
        ("htm", "application/json", "process_structured_document"),
        ("htm", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "process_pptx_document"),
        ("htm", "image/png", "process_image"),
        ("htm", "text/markdown", "process_md_document"),
        ("xls", "application/vnd.ms-excel.sheet.macroEnabled.12", "process_xls_document"),
        # Where main read nothing, the new route.
        ("htm", "application/octet-stream", "process_html_document"),
        (None, "application/vnd.ms-excel.sheet.macroEnabled.12", "process_excel_document"),
    ],
)
async def test_a_cell_main_read_keeps_main_s_parser(ext: str | None, mime: str, handler: str) -> None:
    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    seen: list[str] = []

    def stub(name: str) -> Callable[..., AsyncIterator[object]]:
        def call(*_a: object, **_k: object) -> AsyncIterator[object]:
            seen.append(name)
            return _mock_processor_gen()
        return call

    for method in [m for m in dir(type(processor)) if m.startswith("process_")] + [
        "process_structured_document", "process_xls_document", "process_doc_document", "process_md_document",
        "process_image", "process_html_document", "process_pptx_document", "process_excel_document",
        "process_docx_document", "process_plain_text_document", "process_txt_document",
    ]:
        setattr(processor, method, MagicMock(side_effect=stub(method)))
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=stub("_dispatch_pdf_binary")):
        await _drain(ep.on_event(_make_event_payload(
            extension=ext, mime_type=mime, buffer=b"hello world", record_name=f"Sample.{ext}",
        )))
    assert seen == [handler]


async def test_a_name_s_extension_decides_when_the_record_has_none() -> None:
    _, routed = await _dispatch("unknown", "application/octet-stream", samples.make_cfg("s"), name="app.cfg")
    assert routed == ["process_plain_text_document"]


async def test_a_docm_reaches_the_docx_handler_as_a_plain_package() -> None:
    processor, _ = await _dispatch("docm", "text/plain", samples.make_docm("s"))
    data = processor.process_docx_document.call_args.kwargs["docx_binary"]
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "word/vbaProject.bin" not in names
    assert samples.VBA_MARKER not in data


async def test_a_log_reaches_its_handler_as_capped_utf8_text() -> None:
    processor, _ = await _dispatch("log", "application/octet-stream", "line one\nline two\n".encode("utf-16-le"))
    assert processor.process_plain_text_document.call_args.kwargs["text_binary"] == b"line one\nline two\n"


async def test_a_binary_cfg_is_marked_not_supported_without_reaching_a_parser() -> None:
    processor, routed = await _dispatch("cfg", "application/octet-stream", b"\x00\x01\x02binary\x00" * 20)
    assert routed == []
    args = processor._mark_record.await_args
    assert args.args[1].value == "FILE_TYPE_NOT_SUPPORTED"
    assert args.kwargs["reason"] == "This .cfg file holds binary data, not text, so PipesHub can't read it."


async def test_the_password_check_reads_the_file_before_it_is_rewritten() -> None:
    with patch("app.modules.parsers.ooxml.main_part.as_plain_package") as rewrite, \
         pytest.raises(PasswordProtectedError):
        await _dispatch("docm", "application/octet-stream", encrypted_docm())
    rewrite.assert_not_called()


async def test_an_encrypted_odt_is_password_protected_before_conversion() -> None:
    with pytest.raises(PasswordProtectedError):
        await _dispatch("odt", "application/vnd.oasis.opendocument.text", samples.make_odt("s", encrypted=True))


@pytest.mark.parametrize(
    ("ext", "content", "service_ext", "service_mime"),
    [
        ("docm", samples.make_docm("s"), "docx", _DOCX),
        ("dotx", samples.make_dotx("s"), "docx", _DOCX),
        ("potx", samples.make_potx("s"), "pptx", _PPTX),
        ("msg", samples.msg_with_text_body(), "msg", "text/plain"),
        ("log", samples.make_log("s"), "log", "text/plain"),
        ("xlsm", samples.make_xlsm("s"), "xlsm", "text/plain"),
    ],
)
async def test_the_parsing_service_gets_the_prepared_file(
    ext: str, content: bytes, service_ext: str, service_mime: str
) -> None:
    ep, _, _, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}
    ep.parsing_client = MagicMock(circuit_open=False)
    seen: dict[str, object] = {}

    async def orchestrate(**kw: object) -> AsyncIterator[object]:
        seen.update(kw)
        return
        yield

    with patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"}), \
         patch.object(EventProcessor, "_use_service_pipeline", return_value=True), \
         patch.object(ep, "_orchestrate_via_services", side_effect=orchestrate), \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        await _drain(ep.on_event(_make_event_payload(extension=ext, mime_type="text/plain", buffer=content)))
    assert (seen["extension"], seen["mime_type"]) == (service_ext, service_mime)
    if ext in ("docm", "dotx", "potx"):
        assert samples.VBA_MARKER not in seen["file_content"]  # type: ignore[operator]
