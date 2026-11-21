"""A record's labels have the tier and kind of the reader they select (F23i).

`file_types.tier` / `kind` give the tier and kind of the in-process reader the labels select (`labels_route`); only
labels that select none fall back to extension, then mime, then heavy. The pinned decisions check every cell against
the replayed handler (`delta_rules.HANDLER_TIER_KIND`); these tests name the real shapes and the edges.
"""
from __future__ import annotations

import pytest

from app.config import file_types
from app.services.resource_governor.kinds import (
    ParseKind,
    classify_kind,
    parse_format_kind,
)
from app.services.resource_governor.models import ParseTier
from app.services.resource_governor.tiers import classify

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC, GSHEET, GSLIDES = (
    "application/vnd.google-apps.document", "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
)


@pytest.mark.parametrize(("extension", "mime", "tier", "kind"), [
    # What a SQL connector sends: no extension, a mime with no tier or kind of its own; the SQL reader is light.
    (None, "application/vnd.sql.table", ParseTier.LIGHT, ParseKind.TEXT),
    (None, "application/vnd.sql.view", ParseTier.LIGHT, ParseKind.TEXT),
    # Windows labels a .csv application/vnd.ms-excel; the XLS reader comes first and parses it.
    ("csv", "application/vnd.ms-excel", ParseTier.HEAVY, ParseKind.EXCEL_LOAD),
    ("txt", DOCX, ParseTier.HEAVY, ParseKind.DOCLING_DOCX),
    ("txt", "application/pdf", ParseTier.HEAVY, ParseKind.PDF),
    ("mts", "application/octet-stream", ParseTier.LIGHT, ParseKind.CODE),  # GitLab's mime for .mts
    ("pdf", "text/plain", ParseTier.LIGHT, ParseKind.TEXT),  # the TXT reader reads it
    # A Google export without a kind of its own is read as its format.
    ("txt", GDOC, ParseTier.HEAVY, ParseKind.DOCLING_DOCX),
    ("txt", GSHEET, ParseTier.HEAVY, ParseKind.EXCEL_LOAD),
    ("txt", GSLIDES, ParseTier.HEAVY, ParseKind.DOCLING_DOCX),
    (None, "text/gmail_content", ParseTier.LIGHT, ParseKind.TEXT),
], ids=str)
def test_the_labels_take_their_readers_tier_and_kind(extension, mime, tier, kind) -> None:
    assert (classify(extension, mime), classify_kind(extension, mime)) == (tier, kind)


@pytest.mark.parametrize(("extension", "mime", "tier", "kind"), [
    ("pdf", "application/octet-stream", ParseTier.HEAVY, ParseKind.PDF),  # the extension decides over a generic mime
    ("txt", "application/octet-stream", ParseTier.LIGHT, ParseKind.TEXT),
    ("csv", "text/csv", ParseTier.LIGHT, ParseKind.CSV),
    ("docx", DOCX, ParseTier.HEAVY, ParseKind.DOCLING_DOCX),
    ("unknown", "unknown", ParseTier.HEAVY, ParseKind.UNKNOWN),  # no reader: the safe side
    (None, None, ParseTier.HEAVY, ParseKind.UNKNOWN),
    ("", "", ParseTier.HEAVY, ParseKind.UNKNOWN),
    (None, "application/xml", ParseTier.HEAVY, ParseKind.UNKNOWN),
    (None, "image/heic", ParseTier.HEAVY, ParseKind.UNKNOWN),
    (None, "application/blocks", ParseTier.LIGHT, ParseKind.UNKNOWN),  # blocks have no kind
    ("png", "image/png", ParseTier.HEAVY, ParseKind.UNKNOWN),  # an image has no kind
    (".CSV", "Application/Vnd.MS-Excel; charset=x", ParseTier.HEAVY, ParseKind.EXCEL_LOAD),  # any spelling
], ids=str)
def test_controls(extension, mime, tier, kind) -> None:
    assert (classify(extension, mime), classify_kind(extension, mime)) == (tier, kind)


@pytest.mark.parametrize(("extension", "mime", "error"), [
    (["pdf"], "application/pdf", AttributeError), ({"a": 1}, "text/csv", AttributeError),
    (None, ["text/plain"], AttributeError),
    # The code reader reads a grammar extension whatever the mime, but a malformed mime is not read as absent.
    ("mts", ["text/plain"], AttributeError), ("pyi", b"text/plain", TypeError),
], ids=str)
def test_a_malformed_label_fails_as_before(extension: object, mime: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        file_types.tier(extension, mime)  # type: ignore[arg-type]
    with pytest.raises(error):
        file_types.kind(extension, mime)  # type: ignore[arg-type]


def test_every_reader_the_labels_select_gives_the_tier_and_the_kind() -> None:
    extensions = [*sorted({e for ft in file_types.FILE_TYPES for e in ft.extensions if ft.ext_indexable}), "unknown", ""]
    mimes = [*sorted({ft.canonical_mime for ft in file_types.FILE_TYPES if ft.mime_indexable and ft.canonical_mime}),
             "unknown", "", "application/octet-stream", "text/plain"]
    read = 0
    for extension in extensions:
        for mime in mimes:
            dispatch = file_types.labels_route(extension, mime)
            if file_types.read_tier(dispatch) is not None:
                read += 1
                assert file_types.tier(extension, mime) == file_types.read_tier(dispatch), (extension, mime)
            if file_types.read_kind(dispatch) is not None:
                assert file_types.kind(extension, mime) == file_types.read_kind(dispatch), (extension, mime)
    assert read == 3847


def test_the_path_and_the_name_stand_in_for_a_missing_extension() -> None:
    assert file_types.labels_route(None, "application/octet-stream") is None
    by_name = file_types.labels_route(None, "application/octet-stream", record_name="report.pdf")
    by_path = file_types.labels_route("", None, file_path="docs/report.pdf")
    assert by_name is not None and by_name.route is file_types.Route.PDF
    assert by_path is not None and by_path.route is file_types.Route.PDF
    # An extension of its own wins over the name.
    assert file_types.labels_route("txt", "text/plain", record_name="report.pdf").route is file_types.Route.TXT


@pytest.mark.parametrize(("fmt", "kind"), [
    ("txt", "text"),  # txt's own type, though code read as text shares the format
    ("code", "code"), ("pdf", "pdf"), ("xlsx", "excel_load"), ("docx", "docling_docx"), ("html", "text"),
    ("png", None), ("epub", None), ("blocks", None), (None, None), ("no-such-format", None),
])
def test_the_kind_of_a_parse_format(fmt: str | None, kind: str | None) -> None:
    assert file_types.kind_of_format(fmt) == kind


@pytest.mark.parametrize(("fmt", "extension", "mime", "kind"), [
    ("png", "csv", "image/png", ParseKind.UNKNOWN),  # the service reads an image: the safe figure, not CSV's
    ("xls", "csv", "application/vnd.ms-excel", ParseKind.EXCEL_LOAD),
    ("csv", "csv", "text/csv", ParseKind.CSV),
    (None, "csv", "text/csv", ParseKind.CSV),  # no format resolved: the labels' kind
])
def test_the_service_charges_the_kind_of_the_format_it_parses(fmt, extension, mime, kind) -> None:
    assert parse_format_kind(fmt, extension, mime) is kind
