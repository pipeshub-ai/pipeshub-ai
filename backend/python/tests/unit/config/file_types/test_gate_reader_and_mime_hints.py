"""The gate restricts the reader the dispatch will use, and mime hints match in any case."""
from __future__ import annotations

import pytest

from app.config import file_types

_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
_DOCM = "application/vnd.ms-word.document.macroEnabled.12"
_PPTM = "application/vnd.ms-powerpoint.presentation.macroEnabled.12"


@pytest.mark.parametrize(("mime", "ext"), [(_XLSM, "xlsm"), (_DOCM, "docm")])
def test_a_macro_mime_names_its_extension_in_any_spelling(mime: str, ext: str) -> None:
    for form in (mime, mime.lower(), mime.upper(), f"{mime}; charset=utf-8", f"{mime.lower()};q=1"):
        assert file_types.extension_for_mime(form) == ext, form


def test_a_hint_to_another_type_stays_exact() -> None:
    # The pptm mime maps to ppt only as main spelled it; other spellings name nothing new.
    assert file_types.extension_for_mime(_PPTM) == "ppt"
    assert file_types.extension_for_mime(_PPTM.lower()) is None


def _gate(ext: str | None, mime: str | None, **kw: object) -> file_types.GateDecision:
    return file_types.gate_decision(
        mime=mime, extension=ext, record_name=f"f.{ext}", is_code_file=False, code_file_extension=None, **kw,
    )


def test_a_pdf_labelled_with_the_log_mime_is_not_held_to_the_log_ceiling() -> None:
    decision = _gate("pdf", "text/x-log", size_bytes=file_types.TEXT_LOG_HARD_LIMIT_BYTES + 1)
    assert decision.supported
    assert decision.file_type is not None and decision.file_type.key == "pdf"


def test_turning_off_pdf_refuses_a_pdf_whatever_its_mime() -> None:
    assert _gate("pdf", "text/x-log", disabled=frozenset({"pdf"})).refusal == "disabled"
    assert _gate("log", "application/pdf", disabled=frozenset({"pdf"})).refusal == "disabled"
    assert _gate("log", "application/pdf", disabled=frozenset({"log"})).supported


@pytest.mark.parametrize(("ext", "mime", "reader"), [
    ("log", "application/octet-stream", "log"), ("log", "text/plain", "txt"), ("log", "application/json", "json"),
    ("pdf", "text/x-log", "pdf"), (None, "text/x-log", "log"), ("docm", "text/plain", "docm"),
])
def test_the_gate_names_the_reader_the_dispatch_uses(ext: str | None, mime: str, reader: str) -> None:
    decision = _gate(ext, mime)
    dispatch = file_types.legacy_route(mime, ext, code_extension=ext, reads_as_code_file=False)
    assert decision.file_type is not None and dispatch is not None and dispatch.file_type is not None
    assert decision.file_type.key == dispatch.file_type.key == reader
