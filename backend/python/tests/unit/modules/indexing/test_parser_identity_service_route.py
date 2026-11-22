"""The identity names the parser that runs: the in-process dispatch or, on the service pipeline, the service's own.

``file_types.legacy_route`` and ``file_types.parse_format`` name different parsers for some labels (``application/json`` with a
``csv`` extension is a CSV in process and JSON in the service). A key derived by the first for a copy the second built would
let a same-bytes update reuse the copy for a record the service reads differently.
"""
from __future__ import annotations

import itertools

import pytest

from app.config import file_types
from app.modules.parsers.file_prep import prepare_file, prepared_labels
from app.modules.pipeline import parser_identity as pi

MIMES = [m for m in sorted(file_types.MIME_FORMATS) if m] + ["text/plain", "application/octet-stream", None]
EXTENSIONS = [e for e in sorted(file_types._BY_EXTENSION) if e] + [None]


def _name(ext: str | None) -> str:
    return "f" + (f".{ext}" if ext else "")


def test_a_service_key_is_the_service_format_for_every_label_pair() -> None:
    checked = 0
    for mime, ext in itertools.product(MIMES, EXTENSIONS):
        key = pi.parser_key(mime, ext, _name(ext), "FILE", service=True)
        if key is None:
            continue
        # The service is sent the file as prepared, with the extension its client fills in; the format is exactly that of
        # the dispatch the legacy route names for these labels.
        dispatch = file_types.legacy_route(
            mime, ext, code_ext=file_types.code_extension(ext, None, _name(ext)), reads_as_code_file=False,
        )
        sent_ext, sent_mime = prepared_labels(dispatch.file_type if dispatch else None, ext, mime)
        expected = file_types.parse_format(sent_mime, file_types.service_extension(sent_mime, sent_ext, _name(ext)))
        assert key.split("@")[0] == expected, (mime, ext)
        checked += 1
    assert checked > 1000


def test_a_json_with_a_csv_extension_is_json_on_the_service_and_csv_in_process() -> None:
    assert pi.parser_key("application/json", "csv", "a.csv", "FILE", service=True) == "json@1"
    assert pi.parser_key("application/json", "csv", "a.csv", "FILE", service=False) == "csv@1"
    assert pi.parser_identity("application/json", "csv", "a.csv", "FILE", pipeline=pi.PIPELINE_SERVICE) == "svc:json@1"


def test_a_csv_is_not_taken_for_the_json_copy_the_service_built() -> None:
    built = pi.parser_key("application/json", "csv", "a.csv", "FILE", service=True)
    assert built != pi.parser_key("text/csv", "csv", "b.csv", "FILE", service=True)


@pytest.mark.parametrize("flag,expected", [("true", True), ("false", False), ("", False)])
def test_the_store_side_follows_the_process_setting(monkeypatch: pytest.MonkeyPatch, flag: str, expected: bool) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", flag)
    assert pi.service_pipeline_configured() is expected
    assert (pi.parser_key("application/json", "csv", "a.csv", "FILE") == "json@1") is expected


@pytest.mark.parametrize(
    ("mime", "ext", "sent"),
    [
        ("application/vnd.ms-word.document.macroEnabled.12", "docm", "docx"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.template", "dotx", "docx"),
        ("application/vnd.openxmlformats-officedocument.presentationml.template", "potx", "pptx"),
        ("application/vnd.ms-powerpoint.presentation.macroEnabled.12", "pptm", "pptx"),
    ],
)
def test_a_prepared_package_is_keyed_by_the_format_the_service_is_sent(mime: str, ext: str, sent: str) -> None:
    """The service receives the plain package a macro-enabled or template file holds, so its key is the plain format's."""
    assert pi.parser_key(mime, ext, f"f.{ext}", "FILE", service=True) == f"{sent}@1"


def test_a_revision_bump_of_the_plain_format_reaches_the_prepared_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(pi.PARSER_ROUTE_REV, "docx", 2)
    assert pi.parser_key("application/vnd.ms-word.document.macroEnabled.12", "docm", "f.docm", "FILE", service=True) == "docx@2"


@pytest.mark.asyncio
async def test_the_labels_of_a_prepared_file_are_the_ones_the_key_assumes() -> None:
    log = file_types.by_extension("log")
    prepared = await prepare_file(b"a line\n", log, extension="unknown", mime_type="text/plain")
    assert prepared is not None
    assert (prepared.extension, prepared.mime_type) == prepared_labels(log, "unknown", "text/plain")


def test_an_extension_the_client_fills_in_is_the_one_the_key_uses() -> None:
    """A macro-enabled pptx mime with no extension is sent as ``ppt``, and the service reads it as such."""
    mime = "application/vnd.ms-powerpoint.presentation.macroEnabled.12"
    assert file_types.service_extension(mime, None, "deck") == file_types.extension_for_mime(mime)
    assert pi.parser_key(mime, None, "deck", "FILE", service=True) == f"{file_types.parse_format(mime, file_types.service_extension(mime, None, 'deck'))}@1"


def test_the_file_path_names_the_extension_before_the_record_name_in_process() -> None:
    """The dispatch reads the extension from the path first: text/plain named ``notes`` at ``src/x.py`` is read as source."""
    from app.events.events import EventProcessor

    dispatch, _code_ext, as_code = EventProcessor._read_plan("FILE", "text/plain", None, "src/x.py", "notes", None)
    with_path = pi.parser_key("text/plain", None, "notes", "FILE", service=False, file_path="src/x.py")
    without = pi.parser_key("text/plain", None, "notes", "FILE", service=False)
    assert (dispatch is not None and dispatch.route.value in {"code"}) or as_code
    assert with_path is None and without is not None
    # the key built with the path is the one the dispatch used (here source, which is not tracked)
    assert pi.parser_identity("text/plain", None, "notes", "FILE", pipeline=pi.PIPELINE_IN_PROCESS, file_path="src/x.py") == (
        f"inproc:{pi.PATH_UNTRACKED_KEY}"
    )


def test_the_service_predicate_of_the_event_processor_includes_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.events.test_events import _make_event_processor

    ep, _, _, _ = _make_event_processor()
    ep.parsing_client, ep.extraction_client, ep.sink_orchestrator = object(), object(), object()
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    assert ep._use_service_pipeline() is pi.service_pipeline_configured() is True
    monkeypatch.setenv("USE_PARSING_SERVICE", "false")
    assert ep._use_service_pipeline() is pi.service_pipeline_configured() is False
