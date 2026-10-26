"""A knowledge-base upload's timestamps are what the upload gave it, never shown as document dates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from app.utils.chat_helpers import create_record_instance_from_dict, get_record
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

    def test_a_re_upload_stored_before_the_origin_was_kept_is_not_called_a_file_time(self) -> None:
        # The first upload sent the file's time; a later API upload sent none,
        # and a rename has since moved updatedAtTimestamp.
        reuploaded = _kb_doc(
            sourceCreatedAtTimestamp=FILE_MTIME,
            sourceLastModifiedTimestamp=UPLOADED + 30 * DAY_MS,
            updatedAtTimestamp=UPLOADED + 31 * DAY_MS,
        )
        assert RecordDates.from_graph(reuploaded).listing_date() == ("uploaded", UPLOADED + 30 * DAY_MS)

    def test_a_later_versions_time_older_than_the_first_upload_is_the_files(self) -> None:
        older = _kb_doc(sourceCreatedAtTimestamp=FILE_MTIME, sourceLastModifiedTimestamp=FILE_MTIME + DAY_MS)
        assert RecordDates.from_graph(older).listing_date() == ("fileModified", FILE_MTIME + DAY_MS)

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

    def test_a_re_upload_stored_before_the_origin_was_kept_shows_no_file_time(self) -> None:
        record = _kb_record(source_created_at=FILE_MTIME, source_updated_at=UPLOADED + 30 * DAY_MS)
        lines = _date_lines(record.to_llm_context())
        assert len(lines) == 1 and lines[0].startswith("Uploaded At: 2026-11-14")

    def test_a_new_version_sent_without_a_file_time_shows_its_own_upload_time(self) -> None:
        reuploaded = UPLOADED + 90 * DAY_MS
        record = _kb_record(source_updated_at=reuploaded, source_timestamp_origin="upload")
        lines = _date_lines(record.to_llm_context())
        assert len(lines) == 1 and lines[0].startswith("Uploaded At: 2027-01-13")
        doc = _kb_doc(sourceLastModifiedTimestamp=reuploaded, sourceTimestampOrigin="upload")
        assert RecordDates.from_graph(doc).listing_date() == ("uploaded", reuploaded)

    def test_a_connectors_record_keeps_created_and_updated(self) -> None:
        record = _kb_record(origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE)
        lines = _date_lines(record.to_llm_context())
        assert [line.split(":")[0] for line in lines] == ["Created At", "Last Updated At"]

    def test_a_record_built_without_its_upload_time_is_not_given_the_build_time(self) -> None:
        # Nothing says whether its source time is the upload's or the file's.
        record = _kb_record(source_created_at=FILE_MTIME, source_updated_at=FILE_MTIME, created_at=None)
        assert _date_lines(record.to_llm_context()) == []
        assert RecordDates.from_graph(_kb_doc(createdAtTimestamp=None)).listing_date() is None
        stored = _kb_record(created_at=None, source_timestamp_origin="upload")
        assert _date_lines(stored.to_llm_context())[0].startswith("Uploaded At: 2026-10-15")

    def test_search_results_carry_the_graphs_upload_time_and_origin(self) -> None:
        record = create_record_instance_from_dict({
            "id": "rec-1", "record_name": "notes.txt", "record_type": "FILE", "origin": "UPLOAD",
            "connector_name": "KB", "external_record_id": "doc-1", "created_at": UPLOADED,
            "source_created_at": FILE_MTIME, "source_updated_at": FILE_MTIME, "source_timestamp_origin": "file",
        })
        lines = _date_lines(record.to_llm_context())
        assert lines[0].startswith("File Last Modified: 2025-03-03")
        assert lines[1].startswith("Uploaded At: 2026-10-15")

    @pytest.mark.asyncio
    async def test_a_stored_copy_of_another_record_lends_it_no_dates(self) -> None:
        # Content is stored once for every record sharing it; this copy is another
        # record's, uploaded with its file time, and this record's graph document
        # was stored before origins were kept.
        copy = {
            "id": "rec-other", "virtual_record_id": "vr-1", "org_id": "org-1", "record_name": "other.txt",
            "record_type": "FILE", "version": 1, "origin": "UPLOAD", "connector_name": "KB",
            "connector_id": "kb-1", "mime_type": "text/plain", "source_created_at": FILE_MTIME,
            "source_updated_at": FILE_MTIME, "source_timestamp_origin": "file",
            "created_at": UPLOADED - 9 * DAY_MS, "semantic_metadata": {},
            "block_containers": {"blocks": [], "block_groups": []},
        }
        store = SimpleNamespace(get_record_from_storage=AsyncMock(return_value=copy))
        results: dict = {}

        graph_doc = _kb_doc(virtualRecordId="vr-1", mimeType="text/plain")

        await get_record("vr-1", results, store, "org-1", {"vr-1": graph_doc})

        assert _date_lines(results["vr-1"]["context_metadata"]) == [
            "Uploaded At: 2026-10-15 18:48:19 UTC (Thu, Oct 15, 2026, 6:48 PM UTC) (upload time, not a document date)",
        ]

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

    def test_indexing_loads_it_with_the_record(self) -> None:
        from app.events.processor import convert_record_dict_to_record

        doc = _kb_doc(sourceTimestampOrigin="file", mimeType="text/plain")
        assert convert_record_dict_to_record(doc).source_timestamp_origin == "file"

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


class TestContentDates:
    EMAIL_DAY = 1739232000000  # 2025-02-11 00:00 UTC
    REPLY_DAY = 1739404800000  # 2025-02-13 00:00 UTC

    def test_a_date_the_document_states_is_shown_as_its_date(self) -> None:
        record = _kb_record(
            source_created_at=self.EMAIL_DAY, source_updated_at=self.REPLY_DAY, source_timestamp_origin="content",
        )
        lines = [line for line in record.to_llm_context().splitlines() if line.startswith(("Document Date", "Last Updated", "Uploaded At", "Created At"))]
        assert lines == [
            "Document Date: 2025-02-11 (stated in the document)",
            "Last Updated: 2025-02-13 (stated in the document)",
            "Uploaded At: 2026-10-15 18:48:19 UTC (Thu, Oct 15, 2026, 6:48 PM UTC) (upload time, not a document date)",
        ]

    def test_one_stated_day_is_shown_once(self) -> None:
        record = _kb_record(source_created_at=self.EMAIL_DAY, source_updated_at=self.EMAIL_DAY, source_timestamp_origin="content")
        context = record.to_llm_context()
        assert "Document Date: 2025-02-11 (stated in the document)" in context
        assert "Last Updated:" not in context

    def test_the_day_is_the_stated_one_in_any_time_zone(self) -> None:
        from app.utils.time_conversion import reset_llm_time_zone, set_llm_time_zone

        token = set_llm_time_zone("America/Los_Angeles")
        try:
            context = _kb_record(source_created_at=self.EMAIL_DAY, source_updated_at=self.EMAIL_DAY, source_timestamp_origin="content").to_llm_context()
        finally:
            reset_llm_time_zone(token)
        assert "Document Date: 2025-02-11" in context

    def test_listings_show_the_stated_date(self) -> None:
        doc = _kb_doc(sourceCreatedAtTimestamp=self.EMAIL_DAY, sourceLastModifiedTimestamp=self.EMAIL_DAY, sourceTimestampOrigin="content")
        assert RecordDates.from_graph(doc).listing_date() == ("dated", self.EMAIL_DAY)

    def test_the_records_schema_accepts_the_kept_upload_values(self) -> None:
        doc = _kb_doc(
            sourceTimestampOrigin="content", uploadSourceCreatedAtTimestamp=UPLOADED,
            uploadSourceLastModifiedTimestamp=UPLOADED, uploadSourceTimestampOrigin="upload",
        )
        del doc["_key"]
        jsonschema.validate(doc, record_schema["rule"])
