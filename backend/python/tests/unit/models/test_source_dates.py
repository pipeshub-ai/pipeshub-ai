"""A knowledge-base upload's timestamps are what the upload gave it, never shown as document dates."""

from __future__ import annotations

import jsonschema
import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import FileRecord, Record, RecordType
from app.models.source_dates import (
    UPLOAD_CLOCK_WINDOW_MS,
    RecordDates,
    SourceTimestampOrigin,
    upload_origin,
)
from app.schema.arango.documents import record_schema
from app.utils.chat_helpers import create_record_instance_from_dict
from app.utils.not_indexed import record_metadata_lines

UPLOADED = 1792090099000  # 2026-10-15 18:48:19 UTC
FILE_MTIME = 1741000000000  # 2025-03-03 11:06:40 UTC
DAY_MS = 86_400_000


def _kb_doc(**overrides: object) -> dict:
    doc = {
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "notes.txt",
        "recordType": "FILE",
        "externalRecordId": "doc-1",
        "version": 1,
        "origin": "UPLOAD",
        "connectorName": "KB",
        "connectorId": "kb-1",
        "createdAtTimestamp": UPLOADED,
        "updatedAtTimestamp": UPLOADED,
        "sourceCreatedAtTimestamp": UPLOADED - 40,
        "sourceLastModifiedTimestamp": UPLOADED - 40,
    }
    doc.update(overrides)
    return doc


def _kb_record(**overrides: object) -> Record:
    kwargs = {
        "record_name": "notes.txt",
        "record_type": RecordType.FILE,
        "external_record_id": "doc-1",
        "version": 1,
        "origin": OriginTypes.UPLOAD,
        "connector_name": Connectors.KNOWLEDGE_BASE,
        "connector_id": "kb-1",
        "created_at": UPLOADED,
        "source_created_at": UPLOADED - 40,
        "source_updated_at": UPLOADED - 40,
    }
    kwargs.update(overrides)
    if kwargs["created_at"] is None:
        del kwargs["created_at"]
    return Record(**kwargs)


def _date_lines(context: str) -> list[str]:
    labels = ("Created At", "Last Updated At", "Uploaded At", "File Last Modified")
    return [line for line in context.splitlines() if line.startswith(labels)]


class TestUploadOrigin:
    def test_a_timestamp_set_with_the_upload_is_the_upload_time(self) -> None:
        assert upload_origin(UPLOADED - 40, UPLOADED) is SourceTimestampOrigin.UPLOAD
        assert upload_origin(UPLOADED - UPLOAD_CLOCK_WINDOW_MS, UPLOADED) is SourceTimestampOrigin.UPLOAD

    def test_an_older_timestamp_is_the_files_own(self) -> None:
        assert upload_origin(FILE_MTIME, UPLOADED) is SourceTimestampOrigin.FILE

    def test_unknown_reads_as_the_upload_time(self) -> None:
        assert upload_origin(None, UPLOADED) is SourceTimestampOrigin.UPLOAD
        assert upload_origin(FILE_MTIME, None) is SourceTimestampOrigin.UPLOAD


class TestRecordDates:
    def test_a_connectors_record_keeps_its_source_dates(self) -> None:
        dates = RecordDates.from_graph(_kb_doc(connectorName="DRIVE", origin="CONNECTOR"))
        assert dates.origin is None

    def test_a_stored_origin_is_used(self) -> None:
        dates = RecordDates.from_graph(_kb_doc(sourceTimestampOrigin="file"))
        assert dates.origin is SourceTimestampOrigin.FILE

    def test_an_upload_indexed_before_the_origin_was_stored_is_told_apart_by_clock(self) -> None:
        assert RecordDates.from_graph(_kb_doc()).origin is SourceTimestampOrigin.UPLOAD
        legacy_file = _kb_doc(sourceCreatedAtTimestamp=FILE_MTIME, sourceLastModifiedTimestamp=FILE_MTIME)
        assert RecordDates.from_graph(legacy_file).origin is SourceTimestampOrigin.FILE

    def test_an_origin_this_build_does_not_know_is_inferred(self) -> None:
        assert RecordDates.from_graph(_kb_doc(sourceTimestampOrigin="later")).origin is SourceTimestampOrigin.UPLOAD

    def test_listings_label_upload_times_as_uploads(self) -> None:
        assert RecordDates.from_graph(_kb_doc()).listing_date() == ("uploaded", UPLOADED)
        file_doc = _kb_doc(sourceCreatedAtTimestamp=FILE_MTIME, sourceLastModifiedTimestamp=FILE_MTIME)
        assert RecordDates.from_graph(file_doc).listing_date() == ("fileModified", FILE_MTIME)
        drive = _kb_doc(connectorName="DRIVE", origin="CONNECTOR", sourceLastModifiedTimestamp=None)
        assert RecordDates.from_graph(drive).listing_date(UPLOADED) == ("modified", UPLOADED)


class TestRecordHeader:
    def test_an_upload_with_only_its_upload_time_shows_no_document_dates(self) -> None:
        lines = _date_lines(_kb_record().to_llm_context())
        assert lines == ["Uploaded At: 2026-10-15 18:48:19 UTC (Thu, Oct 15, 2026, 6:48 PM UTC) (upload time, not a document date)"]

    def test_an_uploads_file_timestamp_is_named_as_the_files(self) -> None:
        record = _kb_record(source_created_at=FILE_MTIME, source_updated_at=FILE_MTIME + DAY_MS)
        lines = _date_lines(record.to_llm_context())
        assert lines[0].startswith("File Last Modified: 2025-03-04 11:06:40 UTC")
        assert lines[1].startswith("Uploaded At: 2026-10-15 18:48:19 UTC")
        assert not any(line.startswith(("Created At", "Last Updated At")) for line in lines)

    def test_a_connectors_record_keeps_created_and_updated(self) -> None:
        record = _kb_record(origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE)
        lines = _date_lines(record.to_llm_context())
        assert [line.split(":")[0] for line in lines] == ["Created At", "Last Updated At"]

    def test_a_record_built_without_its_upload_time_is_not_given_the_build_time(self) -> None:
        record = _kb_record(source_created_at=FILE_MTIME, source_updated_at=FILE_MTIME, created_at=None)
        lines = _date_lines(record.to_llm_context())
        assert lines == [line for line in lines if not line.startswith(("Created At", "Last Updated At"))]
        assert lines[0].startswith("Uploaded At: 2025-03-03")

    def test_search_results_carry_the_graphs_upload_time_and_origin(self) -> None:
        record = create_record_instance_from_dict({
            "id": "rec-1", "record_name": "notes.txt", "record_type": "FILE", "origin": "UPLOAD",
            "connector_name": "KB", "external_record_id": "doc-1", "created_at": UPLOADED,
            "source_created_at": FILE_MTIME, "source_updated_at": FILE_MTIME, "source_timestamp_origin": "file",
        })
        lines = _date_lines(record.to_llm_context())
        assert lines[0].startswith("File Last Modified: 2025-03-03")
        assert lines[1].startswith("Uploaded At: 2026-10-15")

    def test_the_raw_record_fallback_names_the_upload_time(self) -> None:
        lines = record_metadata_lines(_kb_doc())
        assert "Uploaded At: 2026-10-15 18:48 UTC (upload time, not a document date)" in lines
        assert not any(line.startswith(("Created At", "Last Updated At")) for line in lines)


class TestStoredOrigin:
    def test_it_round_trips_through_the_graph_document(self) -> None:
        doc = _kb_doc(sourceTimestampOrigin="file")
        record = FileRecord.from_arango_record(arango_base_file_record={"isFile": True}, arango_base_record=doc)
        assert record.source_timestamp_origin == "file"
        assert record.to_arango_base_record()["sourceTimestampOrigin"] == "file"

    def test_it_is_not_written_unset(self) -> None:
        # A pod still on the old strict schema rejects the key even as null.
        assert "sourceTimestampOrigin" not in _kb_record().to_arango_base_record()

    @staticmethod
    def _stored(**overrides: object) -> dict:
        doc = _kb_doc(**overrides)
        del doc["_key"]
        return doc

    @pytest.mark.parametrize("value", ["upload", "file", "content"])
    def test_the_records_schema_accepts_it(self, value: str) -> None:
        jsonschema.validate(self._stored(sourceTimestampOrigin=value), record_schema["rule"])

    def test_the_records_schema_rejects_a_malformed_one(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(self._stored(sourceTimestampOrigin="Upload Time"), record_schema["rule"])
