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
    dispatch = file_types.legacy_route(mime, ext, code_ext=ext, reads_as_code_file=False)
    assert decision.file_type is not None and dispatch is not None and dispatch.file_type is not None
    assert decision.file_type.key == dispatch.file_type.key == reader


@pytest.mark.parametrize(("ext", "path", "name", "expected"), [
    ("pdf", "x/a.docx", "a.md", "pdf"),  # the record's own extension
    (["pdf"], None, "a.md", ["pdf"]),  # a malformed one is kept as it is
    (None, "dir/Report.DOCX", "a.md", "docx"),  # the path first, lower case
    ("", None, "dir/notes.MD", "md"),
    ("unknown", None, "a.tar.gz", "gz"),
    ("unknown", "dir/no_dot", "a.py", "py"),  # a path that names nothing gives way to the name
    (None, "v1.2/README", "a.md", "md"),  # only the file's own name counts, not a dotted folder
    (None, None, "v1.2/README", None),
    ("unknown", None, "trailing.", "unknown"),
    (None, None, "no_dot", None),
    (None, None, None, None),
    (None, ["x/a.pdf"], "a.md", "md"),  # a non-string path is ignored
    (None, 7, None, None),
])
def test_code_extension_is_the_records_own_else_its_paths_else_its_names(
    ext: object, path: object, name: object, expected: object,
) -> None:
    assert file_types.code_extension(ext, path, name) == expected


@pytest.mark.parametrize("mime", [
    None, "", "unknown", "application/unknown", "application/octet-stream", "APPLICATION/OCTET-STREAM",
    "application/octet-stream; charset=binary",
])
def test_the_name_reads_a_file_whose_mime_names_nothing(mime: str | None) -> None:
    assert file_types._names_nothing(mime)
    dispatch = file_types.legacy_route(mime, "unknown", code_ext="docx", reads_as_code_file=False)
    assert dispatch is not None and dispatch.route is file_types.Route.DOCX


@pytest.mark.parametrize(("mime", "name_ext"), [
    ("application/zip", "docx"),  # a registry type with no reader
    ("video/mp4", "pdf"),
    ("video/mpeg", "pdf"),  # not in the registry: still a specific mime
    ("application/x-unregistered", "docx"),
    (["application/octet-stream"], "docx"),  # a malformed mime never falls back
])
def test_a_specific_mime_is_never_overruled_by_the_name(mime: object, name_ext: str) -> None:
    assert not file_types._names_nothing(mime)
    assert file_types.legacy_route(mime, "unknown", code_ext=name_ext, reads_as_code_file=False) is None  # type: ignore[arg-type]


def test_the_name_is_only_the_last_word() -> None:
    def route(mime: str | None, ext: str | None, name_ext: str | None) -> object:
        dispatch = file_types.legacy_route(mime, ext, code_ext=name_ext, reads_as_code_file=False)
        return dispatch.route if dispatch else None

    assert route("application/x-yaml", "unknown", "docx") is file_types.Route.YAML  # the mime still wins
    assert route(None, "unknown", "zip") is None  # a name of a type with no reader reads nothing
    assert route(None, "bin", "bin") is None  # the record's own extension is not replaced
    assert route(None, "unknown", None) is None
