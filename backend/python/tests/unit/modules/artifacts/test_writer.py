"""``ArtifactWriter``: the files of a revision, their bytes, and the order they are stored in."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors
from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
    ImageMetadata,
    SemanticMetadata,
)
from app.models.entities import Priority, Record, RecordType, Status, TicketRecord
from app.models.record_metadata import RecordMetadata
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import MANIFEST_PATH, PROPERTIES_PATH
from app.modules.artifacts.store import MAX_FILES_PER_CALL, NodeArtifactStore
from app.modules.artifacts.writer import (
    PART_MAX_BYTES,
    ArtifactWriter,
    build_revision,
    canonical_json,
    shard_lines,
)
from app.modules.record_metadata.builtin import ticket_metadata
from tests.unit.modules.artifacts.fake_storage import FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

REV = "0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 50
JPEG = b"\xff\xd8\xff" + b"jpeg" * 40


def _uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _record(blocks: list[Block] | None = None, groups: list[BlockGroup] | None = None) -> Record:
    return Record(
        id="rec-1",
        record_name="doc.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin="UPLOAD",
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="conn-1",
        org_id="org-1",
        mime_type="application/pdf",
        virtual_record_id="vr-1",
        block_containers=BlocksContainer(blocks=blocks or [], block_groups=groups or []),
    )


def _text_blocks(n: int, size: int) -> list[Block]:
    return [
        Block(id=f"b{i}", index=i, type=BlockType.TEXT, data=f"{i:06d}" + "x" * size, parent_index=0,
              citation_metadata=CitationMetadata(page_number=i // 10 + 1))
        for i in range(n)
    ]


def _image_block(index: int, uri: str) -> Block:
    return Block(id=f"img{index}", index=index, type=BlockType.IMAGE, data={"uri": uri},
                 image_metadata=ImageMetadata(description=f"figure {index}"))


@pytest.fixture(autouse=True)
def _routes_available() -> None:
    store_module.ARTIFACT_ROUTES.reset()
    store_module.PROPERTIES_FILE.reset()


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


def _writer(fake: FakeStorage, **kwargs: int) -> ArtifactWriter:
    logger = logging.getLogger("artifact-writer-test")
    return ArtifactWriter(NodeArtifactStore(fake.blob_storage(), logger), logger, **kwargs)


def _lines(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.splitlines()]


class TestSharding:
    def test_parts_are_bounded_and_keep_every_line_whole(self) -> None:
        lines = [canonical_json({"i": i, "t": "y" * (i * 37 % 900)}) + b"\n" for i in range(400)]
        parts = shard_lines(lines, max_bytes=4096)

        assert b"".join(parts) == b"".join(lines)
        assert all(len(p) <= 4096 for p in parts)
        assert all(p.endswith(b"\n") for p in parts)
        assert len(parts) > 1

    def test_a_line_over_the_bound_is_a_part_of_its_own(self) -> None:
        big = b"z" * 5000 + b"\n"
        parts = shard_lines([b"a\n", big, b"b\n"], max_bytes=4096)
        assert parts == [b"a\n", big, b"b\n"]

    def test_default_bound_is_8_mib(self) -> None:
        assert PART_MAX_BYTES == 8 * 1024 * 1024
        record = _record(_text_blocks(9, 1024 * 1024))
        built = build_revision(record, record.block_containers)
        parts = built.artifacts["blocks"].parts or []

        assert [p.path for p in parts] == [f"blocks/part-{n:04d}.jsonl" for n in range(len(parts))]
        assert len(parts) == 2
        assert all(p.size <= PART_MAX_BYTES for p in parts)

    def test_same_input_same_parts_and_bytes(self) -> None:
        record = _record(
            [*_text_blocks(300, 700), _image_block(300, _uri("image/png", PNG))],
            [BlockGroup(id="g0", index=0, type=GroupType.TEXT_SECTION,
                        children=BlockGroupChildren.from_indices(list(range(300))))],
        )

        def build() -> dict[str, bytes]:
            built = build_revision(
                record, record.block_containers,
                semantic_metadata=SemanticMetadata(summary="s", topics=["b", "a"]),
                reconciliation_metadata={"hash_to_block_ids": {"h": ["b0"]}, "block_id_to_index": {"b0": 0}},
                part_max_bytes=64 * 1024,
            )
            return {p: f.data for p, f in built.files.items()}

        first, second = build(), build()
        assert first == second
        assert len([p for p in first if p.startswith("blocks/")]) > 1


class TestFiles:
    def test_images_are_decoded_once_and_blocks_hold_a_ref(self) -> None:
        png, jpeg, gif = _uri("image/png", PNG), _uri("image/jpeg", JPEG), _uri("image/gif", b"GIF89a....")
        record = _record([_image_block(0, png), _image_block(1, png), _image_block(2, jpeg), _image_block(3, gif)])

        built = build_revision(record, record.block_containers)

        png_path = f"images/{hashlib.sha256(PNG).hexdigest()}.png"
        jpg_path = f"images/{hashlib.sha256(JPEG).hexdigest()}.jpg"
        assert built.files[png_path].data == PNG
        assert built.files[png_path].content_type == "image/png"
        assert built.files[jpg_path].data == JPEG
        assert built.artifacts["images"].count == 2
        blocks = _lines(built.files["blocks/part-0000.jsonl"].data)
        assert [b["data"] for b in blocks[:3]] == [{"asset": png_path}, {"asset": png_path}, {"asset": jpg_path}]
        # A format the storage service does not take stays inline.
        assert blocks[3]["data"] == {"uri": gif}
        # The record handed in, which the stored record is written from, keeps its data URIs.
        assert record.block_containers.blocks[0].data == {"uri": png}

    def test_text_projection_record_groups_metadata_and_reconciliation(self) -> None:
        blocks = [
            *_text_blocks(2, 3),
            Block(id="row", index=2, type=BlockType.TABLE_ROW, parent_index=1,
                  data={"row_natural_language_text": "Alice is 30", "cells": ["Alice", 30]}),
            Block(id="empty", index=3, type=BlockType.TEXT, data=""),
            _image_block(4, _uri("image/png", PNG)),
        ]
        groups = [BlockGroup(id="g0", index=0, type=GroupType.TEXT_SECTION),
                  BlockGroup(id="g1", index=1, type=GroupType.TABLE)]
        record = _record(blocks, groups)
        record.semantic_metadata = SemanticMetadata(summary="kept out of record.json")
        reconciliation = {"hash_to_block_ids": {"h0": ["b0"]}, "block_id_to_index": {"b0": 0, "b1": 1}}

        built = build_revision(record, record.block_containers, semantic_metadata=record.semantic_metadata,
                               reconciliation_metadata=reconciliation)

        assert _lines(built.files["text/part-0000.jsonl"].data) == [
            {"i": 0, "g": 0, "page": 1, "text": "000000xxx"},
            {"i": 1, "g": 0, "page": 1, "text": "000001xxx"},
            {"i": 2, "g": 1, "page": None, "text": "Alice is 30"},
            {"i": 4, "g": None, "page": None, "text": "figure 4"},
        ]
        snapshot = json.loads(built.files["record.json"].data)
        assert snapshot["id"] == "rec-1" and snapshot["virtual_record_id"] == "vr-1"
        assert "block_containers" not in snapshot and "semantic_metadata" not in snapshot
        assert [g["id"] for g in json.loads(built.files["groups.json"].data)] == ["g0", "g1"]
        assert json.loads(built.files["metadata.json"].data)["summary"] == "kept out of record.json"
        assert json.loads(built.files["reconciliation.json"].data) == reconciliation
        assert built.artifacts["reconciliation"].count == 2
        assert built.artifacts["blocks"].count == 5 and built.artifacts["text"].count == 4

    def test_json_is_sorted_compact_utf8(self) -> None:
        assert canonical_json({"b": 1, "a": "Borgartún"}) == '{"a":"Borgartún","b":1}'.encode()
        assert canonical_json({"s": "\ud800"}) == b'{"s":"\\ud800"}'


class TestWriteRevision:
    async def test_every_file_then_the_manifest(self, storage: FakeStorage) -> None:
        record = _record([*_text_blocks(50, 2000), _image_block(50, _uri("image/png", PNG))])

        written = await _writer(storage, part_max_bytes=32 * 1024).write_revision(
            record, "org-1", "vr-1", REV, record.block_containers,
            reconciliation_metadata={"block_id_to_index": {"b0": 0}},
        )

        assert written is not None
        assert storage.put_order[-1] == MANIFEST_PATH
        assert storage.put_order.count(MANIFEST_PATH) == 1
        assert written.manifest_ref.document_id == FakeStorage.document_id("vr-1", REV)
        assert written.manifest_ref.root == FakeStorage.root("vr-1", REV)
        root = FakeStorage.root("vr-1", REV)
        manifest = json.loads(storage.puts[f"{root}/{MANIFEST_PATH}"].data)
        assert manifest["virtualRecordId"] == "vr-1" and manifest["rev"] == REV and manifest["orgId"] == "org-1"
        listed = {
            f["path"]: f
            for entry in manifest["artifacts"].values()
            for f in (entry.get("parts") or entry.get("files") or [])
        }
        listed.update({
            e["path"]: {"path": e["path"], "size": e["size"], "sha256": e["sha256"]}
            for e in manifest["artifacts"].values() if e.get("path")
        })
        assert set(listed) == set(storage.put_order) - {MANIFEST_PATH}
        for path, entry in listed.items():
            data = storage.puts[f"{root}/{path}"].data
            assert entry["size"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()

    async def test_a_failed_put_stores_no_manifest(self, storage: FakeStorage) -> None:
        storage.fail_paths = {"groups.json"}
        record = _record(_text_blocks(5, 10))

        with pytest.raises(Exception):  # noqa: B017 - any storage failure
            await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert MANIFEST_PATH not in storage.put_order

    async def test_more_files_than_one_presign_takes(self, storage: FakeStorage) -> None:
        images = [_image_block(i, _uri("image/png", PNG + i.to_bytes(4, "big"))) for i in range(1200)]
        record = _record(images)

        written = await _writer(storage, upload_concurrency=32).write_revision(
            record, "org-1", "vr-1", REV, record.block_containers,
        )

        assert written is not None
        assert [len(c["files"]) for c in storage.presign_calls] == [MAX_FILES_PER_CALL, 1205 - MAX_FILES_PER_CALL]
        assert storage.put_order[-1] == MANIFEST_PATH
        assert len(storage.put_order) == 1205

    async def test_storage_without_the_routes_writes_nothing(self, storage: FakeStorage) -> None:
        storage.presign_status = 404
        record = _record(_text_blocks(2, 10))
        writer = _writer(storage)

        assert await writer.write_revision(record, "org-1", "vr-1", REV, record.block_containers) is None
        assert await writer.write_revision(record, "org-1", "vr-1", REV, record.block_containers) is None

        assert len(storage.presign_calls) == 1
        assert storage.put_order == []

    async def test_a_revision_the_storage_service_would_refuse_is_not_sent(self, storage: FakeStorage) -> None:
        record = _record(_text_blocks(1, 1))
        with pytest.raises(ValueError, match="revision"):
            await _writer(storage).write_revision(record, "org-1", "vr-1", "NOT-HEX", record.block_containers)
        assert storage.presign_calls == []


def _ticket(**fields: object) -> TicketRecord:
    return TicketRecord(
        id="rec-1", record_name="PA-1 Login fails", record_type=RecordType.TICKET, external_record_id="PA-1",
        version=1, origin="CONNECTOR", connector_name=Connectors.JIRA, connector_id="conn-1", org_id="org-1",
        virtual_record_id="vr-1", block_containers=BlocksContainer(blocks=_text_blocks(2, 10), block_groups=[]),
        **fields,
    )


class TestProperties:
    async def test_a_tickets_properties_round_trip_and_are_listed(self, storage: FakeStorage) -> None:
        record = _ticket(status=Status.OPEN, priority=Priority.HIGH, assignee="Ada", labels=["auth"],
                         due_date=1_767_225_600_000)

        written = await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert written is not None
        data = storage.puts[f"{FakeStorage.root('vr-1', REV)}/{PROPERTIES_PATH}"].data
        assert data == canonical_json(json.loads(data))
        assert RecordMetadata.model_validate_json(data) == ticket_metadata(record)
        assert set(json.loads(data)) == {"schemaId", "values"}
        entry = written.manifest.artifacts["properties"]
        assert entry.path == PROPERTIES_PATH and entry.count == 5
        assert entry.size == len(data) and entry.sha256 == hashlib.sha256(data).hexdigest()
        assert written.manifest.writerVersion == 2

    def test_a_record_without_values_has_no_file(self) -> None:
        for record in (_record(_text_blocks(1, 1)), _ticket()):
            built = build_revision(record, record.block_containers)
            assert PROPERTIES_PATH not in built.files and "properties" not in built.artifacts

    def test_values_given_are_the_ones_written(self) -> None:
        record = _record(_text_blocks(1, 1))
        values = ticket_metadata(_ticket(status=Status.OPEN))

        built = build_revision(record, record.block_containers, properties=values)

        assert RecordMetadata.model_validate_json(built.files[PROPERTIES_PATH].data) == values

    def test_not_written_when_record_json_is_another_records(self) -> None:
        record = _ticket(status=Status.OPEN)
        assert PROPERTIES_PATH in build_revision(record, record.block_containers).files

        own = build_revision(record, record.block_containers, stored_record_fields={"id": "rec-1"})
        owners = build_revision(record, record.block_containers, stored_record_fields={"id": "rec-0"})

        assert PROPERTIES_PATH in own.files
        assert PROPERTIES_PATH not in owners.files


class TestStorageWithoutPropertiesJson:
    """A storage service older than properties.json refuses any presign naming it, while the
    indexing service already writes it (either may be upgraded first)."""

    async def test_the_set_is_stored_without_it_and_the_manifest_does_not_list_it(
        self, storage: FakeStorage, caplog: pytest.LogCaptureFixture,
    ) -> None:
        storage.refused_paths = {PROPERTIES_PATH}
        writer = _writer(storage)
        first, second = _ticket(status=Status.OPEN), _ticket(status=Status.DONE)

        with caplog.at_level(logging.WARNING):
            written = await writer.write_revision(first, "org-1", "vr-1", REV, first.block_containers)
            again = await writer.write_revision(second, "org-1", "vr-2", REV, second.block_containers)

        assert written is not None and again is not None
        for vrid, revision in (("vr-1", written), ("vr-2", again)):
            root = FakeStorage.root(vrid, REV)
            assert f"{root}/{PROPERTIES_PATH}" not in storage.puts
            assert f"{root}/record.json" in storage.puts and storage.put_order.count(MANIFEST_PATH) == 2
            assert "properties" not in revision.manifest.artifacts
            assert "properties" not in json.loads(storage.puts[f"{root}/{MANIFEST_PATH}"].data)["artifacts"]
        # One refused call for the process, then the file is left out up front; one warning.
        assert [PROPERTIES_PATH in {f["path"] for f in c["files"]} for c in storage.presign_calls] == [
            True, False, False,
        ]
        assert caplog.text.count("refuses properties.json") == 1

    async def test_a_storage_service_that_takes_it_gets_it(self, storage: FakeStorage) -> None:
        record = _ticket(status=Status.OPEN)

        written = await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert written is not None and "properties" in written.manifest.artifacts
        assert f"{FakeStorage.root('vr-1', REV)}/{PROPERTIES_PATH}" in storage.puts
        assert len(storage.presign_calls) == 1

    async def test_a_refusal_of_something_else_still_fails_the_set(self, storage: FakeStorage) -> None:
        storage.refused_paths = {"record.json"}
        record = _ticket(status=Status.OPEN)

        with pytest.raises(StorageRequestRejectedError):
            await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert storage.puts == {}
        # One unknown path could be properties.json, so it is retried once without it; that
        # fails too, so the file is not turned off.
        assert store_module.PROPERTIES_FILE.available
        assert len(storage.presign_calls) == 2

    async def test_two_unknown_paths_do_not_blame_properties_json(self, storage: FakeStorage) -> None:
        storage.refused_paths = {PROPERTIES_PATH, "record.json"}
        record = _ticket(status=Status.OPEN)

        with pytest.raises(StorageRequestRejectedError):
            await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert store_module.PROPERTIES_FILE.available
        assert len(storage.presign_calls) == 1

    async def test_a_400_for_another_reason_never_turns_the_file_off(self, storage: FakeStorage) -> None:
        # The set is accepted without properties.json, so a retry would pass; that alone must
        # not read as "storage does not know the file" and drop it for the whole process.
        storage.refuse_other_with_properties = "Request body too large for this route"
        record = _ticket(status=Status.OPEN)

        with pytest.raises(StorageRequestRejectedError):
            await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)
        assert store_module.PROPERTIES_FILE.available
        assert len(storage.presign_calls) == 1

        storage.refuse_other_with_properties = ""
        written = await _writer(storage).write_revision(record, "org-1", "vr-2", REV, record.block_containers)

        assert written is not None and "properties" in written.manifest.artifacts

    async def test_a_record_without_values_is_never_retried(self, storage: FakeStorage) -> None:
        storage.refused_paths = {"record.json"}
        record = _record(_text_blocks(1, 1))

        with pytest.raises(StorageRequestRejectedError):
            await _writer(storage).write_revision(record, "org-1", "vr-1", REV, record.block_containers)

        assert len(storage.presign_calls) == 1
