"""Date and extension filters shared by both Drive connectors."""

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.google.drive.utils.drive_filters import (
    parse_drive_datetime,
    passes_date_filters,
    passes_extension_filter,
)

FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"


def _filters(**values: object) -> FilterCollection:
    return FilterCollection.from_dict(values)


def test_parse_drive_datetime_reads_an_rfc3339_string() -> None:
    parsed = parse_drive_datetime("2024-01-02T00:00:00.000Z")
    assert parsed is not None
    assert parsed > 0


def test_folders_pass_date_filters_that_exclude_their_modified_time() -> None:
    filters = _filters(
        modified={
            "operator": "is_after",
            "type": "datetime",
            "value": {"start": 1_717_200_000_000},
        }
    )
    folder = {"mimeType": FOLDER, "modifiedTime": "2020-01-01T00:00:00.000Z"}
    old_file = {"mimeType": "text/plain", "modifiedTime": "2020-01-01T00:00:00.000Z"}

    assert passes_date_filters(folder, filters) is True
    assert passes_date_filters(old_file, filters) is False


def test_extension_filter_uses_mime_for_google_docs_and_the_name_otherwise() -> None:
    allowed = _filters(file_extensions={"operator": "in", "type": "list", "value": [DOC, "pdf"]})
    doc = {"mimeType": DOC, "name": "script.py"}
    pdf = {"mimeType": "application/pdf", "name": "report.PDF"}
    text = {"mimeType": "text/plain", "name": "notes.txt"}

    assert passes_extension_filter(doc, allowed) is True
    assert passes_extension_filter(pdf, allowed) is True
    assert passes_extension_filter(text, allowed) is False


def test_a_missing_extension_fails_an_inclusion_filter() -> None:
    allowed = _filters(file_extensions={"operator": "in", "type": "list", "value": ["pdf"]})
    bare = {"mimeType": "application/octet-stream", "name": "noextension"}

    assert passes_extension_filter(bare, allowed) is False
    assert passes_extension_filter({"mimeType": FOLDER, "name": "dir"}, allowed) is True
