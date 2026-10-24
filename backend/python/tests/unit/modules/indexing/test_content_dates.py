"""A knowledge-base upload takes the dates its content states, and only an upload does."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.entities import Record, RecordType
from app.models.source_dates import SourceTimestampOrigin
from app.modules.indexing.content_dates import (
    apply_content_dates,
    plan_dates_write,
    plan_revert,
)
from app.modules.named_entities.document_dates import DocumentDates
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)

UPLOADED = 1792090099000  # 2026-10-15 18:48:19 UTC
FILE_MTIME = 1760000000000  # 2025-10-09
RECORDS = CollectionNames.RECORDS.value
UPLOAD_ONLY = frozenset({SourceTimestampOrigin.UPLOAD})
UPLOAD_AND_FILE = frozenset({SourceTimestampOrigin.UPLOAD, SourceTimestampOrigin.FILE})


def _day(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


EMAIL_DAY = _day(2025, 2, 11)
DATES = DocumentDates(created_ms=EMAIL_DAY, modified_ms=EMAIL_DAY)


def _doc(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    doc = {
        "_key": "rec-1", "id": "rec-1", "orgId": "org-1", "connectorName": "KB", "origin": "UPLOAD",
        "createdAtTimestamp": UPLOADED, "updatedAtTimestamp": UPLOADED,
        "sourceCreatedAtTimestamp": UPLOADED - 40, "sourceLastModifiedTimestamp": UPLOADED - 40,
        "sourceTimestampOrigin": "upload",
    }
    doc.update(overrides)
    return {k: v for k, v in doc.items() if v is not None}


def _content_doc(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    return _doc(**{
        "sourceCreatedAtTimestamp": EMAIL_DAY, "sourceLastModifiedTimestamp": EMAIL_DAY,
        "sourceTimestampOrigin": "content", "uploadSourceCreatedAtTimestamp": UPLOADED - 40,
        "uploadSourceLastModifiedTimestamp": UPLOADED - 40, "uploadSourceTimestampOrigin": "upload",
        **overrides,
    })


class TestPlan:
    def test_an_upload_dated_by_its_upload_takes_its_contents_dates(self) -> None:
        write = plan_dates_write(_doc(), DATES, UPLOAD_ONLY)
        assert write is not None and write.action == "date"
        assert write.updates == {
            "sourceCreatedAtTimestamp": EMAIL_DAY, "sourceLastModifiedTimestamp": EMAIL_DAY,
            "sourceTimestampOrigin": "content",
            "uploadSourceCreatedAtTimestamp": UPLOADED - 40, "uploadSourceLastModifiedTimestamp": UPLOADED - 40,
            "uploadSourceTimestampOrigin": "upload",
        }
        assert write.expected == {
            "sourceCreatedAtTimestamp": UPLOADED - 40, "sourceLastModifiedTimestamp": UPLOADED - 40,
            "sourceTimestampOrigin": "upload",
        }

    def test_an_upload_stored_before_origins_were_kept_is_dated_too(self) -> None:
        write = plan_dates_write(_doc(sourceTimestampOrigin=None), DATES, UPLOAD_ONLY)
        assert write is not None and write.action == "date"
        assert write.expected["sourceTimestampOrigin"] is None
        assert write.updates["uploadSourceTimestampOrigin"] == "upload"

    def test_a_connectors_record_is_never_changed(self) -> None:
        drive = _doc(connectorName="DRIVE", origin="CONNECTOR", sourceTimestampOrigin=None)
        assert plan_dates_write(drive, DATES, UPLOAD_AND_FILE) is None
        assert plan_dates_write(drive, None, UPLOAD_AND_FILE) is None

    def test_the_uploaders_file_time_is_replaced_only_when_asked(self) -> None:
        file_doc = _doc(sourceCreatedAtTimestamp=FILE_MTIME, sourceLastModifiedTimestamp=FILE_MTIME, sourceTimestampOrigin="file")
        assert plan_dates_write(file_doc, DATES, UPLOAD_ONLY) is None
        write = plan_dates_write(file_doc, DATES, UPLOAD_AND_FILE)
        assert write is not None and write.updates["uploadSourceTimestampOrigin"] == "file"

    def test_a_content_date_after_the_files_own_time_is_not_believed(self) -> None:
        file_doc = _doc(sourceCreatedAtTimestamp=FILE_MTIME, sourceLastModifiedTimestamp=FILE_MTIME, sourceTimestampOrigin="file")
        later = DocumentDates(created_ms=_day(2026, 1, 5), modified_ms=_day(2026, 1, 5))
        assert plan_dates_write(file_doc, later, UPLOAD_AND_FILE) is None

    def test_the_same_dates_again_write_nothing(self) -> None:
        assert plan_dates_write(_content_doc(), DATES, UPLOAD_ONLY) is None

    def test_changed_content_dates_keep_the_upload_values_already_kept(self) -> None:
        later = DocumentDates(created_ms=EMAIL_DAY, modified_ms=_day(2025, 3, 1))
        write = plan_dates_write(_content_doc(), later, UPLOAD_ONLY)
        assert write is not None and write.action == "redate"
        assert write.updates == {"sourceCreatedAtTimestamp": EMAIL_DAY, "sourceLastModifiedTimestamp": _day(2025, 3, 1)}

    def test_content_that_no_longer_states_a_date_gives_the_upload_values_back(self) -> None:
        write = plan_dates_write(_content_doc(), None, UPLOAD_ONLY)
        assert write is not None and write.action == "revert"
        assert write.updates == {
            "sourceCreatedAtTimestamp": UPLOADED - 40, "sourceLastModifiedTimestamp": UPLOADED - 40,
            "sourceTimestampOrigin": "upload",
        }
        assert write.expected["sourceTimestampOrigin"] == "content"

    def test_a_file_time_replaced_under_all_comes_back_when_only_upload_times_are_replaced(self) -> None:
        over_file = _content_doc(uploadSourceTimestampOrigin="file")
        assert plan_dates_write(over_file, DATES, UPLOAD_AND_FILE) is None
        write = plan_dates_write(over_file, DATES, UPLOAD_ONLY)
        assert write is not None and write.action == "revert"
        assert write.updates["sourceTimestampOrigin"] == "file"

    def test_an_upload_stored_before_origins_were_kept_has_its_origin_stamped(self) -> None:
        write = plan_dates_write(_doc(sourceTimestampOrigin=None), None, UPLOAD_ONLY)
        assert write is not None and write.action == "stamp"
        assert write.updates == {"sourceTimestampOrigin": "upload"}
        assert plan_dates_write(_doc(), None, UPLOAD_ONLY) is None

    def test_revert_restores_only_content_dated_records(self) -> None:
        write = plan_revert(_content_doc())
        assert write is not None and write.updates["sourceTimestampOrigin"] == "upload"
        assert plan_revert(_doc()) is None


class _Graph:
    """The two provider calls the write makes, with the CAS both providers implement."""

    def __init__(self, *docs: dict[str, Any]) -> None:
        self.docs = {doc["_key"]: copy.deepcopy(doc) for doc in docs}
        self.writes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.reads = 0

    async def get_document(self, key: str, collection: str, transaction: str | None = None) -> dict[str, Any] | None:
        assert collection == RECORDS
        self.reads += 1
        doc = self.docs.get(key)
        return copy.deepcopy(doc) if doc else None

    async def update_node_fields_if_match(
        self, key: str, collection: str, updates: dict[str, Any], expected: dict[str, Any], transaction: str | None = None,
    ) -> bool:
        assert collection == RECORDS
        self.writes.append((key, updates, expected))
        doc = self.docs.get(key)
        if doc is None or any(doc.get(field) != value for field, value in expected.items()):
            return False
        doc.update(updates)
        return True


EMAIL_BLOCKS = [
    "Re: Trident redlines",
    "From: Elaine <elaine@trident.com>\nTo: Michael <michael@redwood.com>\nDate: Tue, Feb 11, 2025 at 8:04 AM\nSubject: redlines",
    "Michael, attached are our redlines.",
]


def _record(doc: dict[str, Any], texts: list[str]) -> Record:
    return Record(
        id=doc["_key"], org_id="org-1", record_name="redlines.txt", record_type=RecordType.FILE,
        external_record_id="doc-1", version=1, origin=OriginTypes(doc["origin"]),
        connector_name=Connectors(doc["connectorName"]), connector_id="kb-1",
        created_at=doc["createdAtTimestamp"], source_created_at=doc["sourceCreatedAtTimestamp"],
        source_updated_at=doc["sourceLastModifiedTimestamp"], source_timestamp_origin=doc.get("sourceTimestampOrigin"),
        block_containers=BlocksContainer(blocks=[Block(index=i, type=BlockType.TEXT, data=t) for i, t in enumerate(texts)]),
    )


async def _extraction(record: Record) -> NamedEntityExtraction:
    kinds = frozenset({EntityKind.DATE, EntityKind.DATE_TIME, EntityKind.DATE_RANGE, EntityKind.DURATION})
    return await NamedEntityExtractor().extract(NamedEntityRequest(blocks=record.block_containers.blocks, enabled=kinds))


class TestApply:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONTENT_DOCUMENT_DATES", "true")

    @pytest.mark.asyncio
    async def test_it_is_off_unless_switched_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONTENT_DOCUMENT_DATES")
        graph, record = _Graph(_doc()), _record(_doc(), EMAIL_BLOCKS)
        assert await apply_content_dates(graph, record, await _extraction(record)) == "off"
        assert (graph.reads, graph.writes) == (0, [])

    @pytest.mark.asyncio
    async def test_the_emails_date_replaces_the_upload_time_once(self) -> None:
        graph, record = _Graph(_doc()), _record(_doc(), EMAIL_BLOCKS)
        extraction = await _extraction(record)

        assert await apply_content_dates(graph, record, extraction) == "date"
        stored = graph.docs["rec-1"]
        assert (stored["sourceCreatedAtTimestamp"], stored["sourceLastModifiedTimestamp"]) == (EMAIL_DAY, EMAIL_DAY)
        assert stored["sourceTimestampOrigin"] == "content"
        assert stored["createdAtTimestamp"] == UPLOADED
        assert (record.source_created_at, record.source_timestamp_origin) == (EMAIL_DAY, "content")

        assert await apply_content_dates(graph, record, extraction) == "unchanged"
        assert len(graph.writes) == 1

    @pytest.mark.asyncio
    async def test_a_connectors_record_is_not_read_or_written(self) -> None:
        drive = _doc(connectorName="DRIVE", origin="CONNECTOR", sourceTimestampOrigin=None)
        graph, record = _Graph(drive), _record(drive, EMAIL_BLOCKS)
        assert await apply_content_dates(graph, record, await _extraction(record)) == "off"
        assert (graph.reads, graph.writes) == (0, [])

    @pytest.mark.asyncio
    async def test_an_extraction_that_did_not_run_changes_nothing(self) -> None:
        graph, record = _Graph(_content_doc()), _record(_content_doc(), EMAIL_BLOCKS)
        failed = NamedEntityExtraction(status="FAILED", termination_reason="llm_error")
        assert await apply_content_dates(graph, record, failed) == "unread"
        assert graph.writes == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unloaded", [BlocksContainer(blocks=[], block_groups=[]), None], ids=["default", "none"])
    async def test_a_record_read_without_its_blocks_keeps_its_content_dates(self, unloaded: BlocksContainer | None) -> None:
        # As a persist retry or a stage's save built it: the graph document and the
        # stored extraction, but not the blocks.
        extraction = await _extraction(_record(_content_doc(), EMAIL_BLOCKS))
        graph, record = _Graph(_content_doc()), _record(_content_doc(), [])
        record.block_containers = unloaded  # type: ignore[assignment]

        assert await apply_content_dates(graph, record, extraction) == "unread"
        assert (graph.reads, graph.writes) == (0, [])
        assert graph.docs["rec-1"]["sourceTimestampOrigin"] == "content"

    @pytest.mark.asyncio
    async def test_a_new_version_written_meanwhile_is_not_overwritten(self) -> None:
        graph, record = _Graph(_doc()), _record(_doc(), EMAIL_BLOCKS)
        extraction = await _extraction(record)
        graph.docs["rec-1"]["sourceLastModifiedTimestamp"] = UPLOADED + 5_000

        async def read_before_the_new_version(key: str, collection: str, transaction: str | None = None) -> dict:
            return _doc()

        graph.get_document = read_before_the_new_version
        assert await apply_content_dates(graph, record, extraction) == "superseded"
        assert graph.docs["rec-1"]["sourceTimestampOrigin"] == "upload"

    @pytest.mark.asyncio
    async def test_a_new_versions_date_after_the_first_upload_is_believed(self) -> None:
        march = _day(2027, 3, 1)
        doc = _doc(updatedAtTimestamp=march + 3_600_000, sourceLastModifiedTimestamp=march + 3_599_000)
        graph = _Graph(doc)
        record = _record(doc, ["Policy update", "Date: 2027-03-01", "The new policy follows."])
        assert await apply_content_dates(graph, record, await _extraction(record)) == "date"
        assert graph.docs["rec-1"]["sourceCreatedAtTimestamp"] == march

    @pytest.mark.asyncio
    async def test_an_undated_document_keeps_its_upload_time(self) -> None:
        graph, record = _Graph(_doc()), _record(_doc(), ["Runbook", "Restart the service, then check the logs."])
        assert await apply_content_dates(graph, record, await _extraction(record)) == "unchanged"
        assert graph.writes == []


class TestSchema:
    """Neo4j checks an update against the records schema before writing it, and
    ArangoDB's strict collection schema checks the whole document after it."""

    @staticmethod
    def _stored(doc: dict[str, Any]) -> dict[str, Any]:
        base = {"recordName": "redlines.txt", "externalRecordId": "doc-1", "recordType": "FILE", "connectorId": "kb-1"}
        return {**base, **{k: v for k, v in doc.items() if k not in ("_key", "id")}}

    @pytest.mark.parametrize("make", [
        lambda: plan_dates_write(_doc(), DATES, UPLOAD_ONLY),
        lambda: plan_dates_write(_doc(sourceTimestampOrigin=None), None, UPLOAD_ONLY),
        lambda: plan_revert(_content_doc()),
    ])
    def test_every_write_passes_the_records_schema(self, make) -> None:
        import jsonschema

        from app.schema.arango.documents import record_schema
        from app.schema.node_validator import NodeSchemaValidator

        write = make()
        NodeSchemaValidator().validate_node_update(RECORDS, write.updates)
        before = _content_doc() if write.action == "revert" else _doc(sourceTimestampOrigin=write.expected["sourceTimestampOrigin"])
        jsonschema.validate(self._stored({**before, **write.updates}), record_schema["rule"])
