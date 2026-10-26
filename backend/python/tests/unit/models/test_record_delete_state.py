"""Soft-delete fields on ``Record``: what is written, and what is read back.

Nothing writes a trashed record yet; these pin the contract the delete path
will rely on, and the rolling-upgrade guard: a live record must not send the new
keys, because a pod still on the old strict Arango schema rejects them.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.config.constants.arangodb import Connectors, DeleteSource, OriginTypes
from app.models import entities
from app.models.entities import FileRecord, Record, RecordType
from app.schema.arango.documents import record_schema

NEW_KEYS = (
    "deletedAtTimestamp", "deleteSource", "deleteBatchId", "purgeAttempts", "purgeLastError",
    "trashedExternalRecordId",
)


def _record(**overrides) -> Record:
    fields = {
        "id": "r1",
        "org_id": "o1",
        "record_name": "a.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": "e1",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": "c1",
    }
    fields.update(overrides)
    return Record(**fields)


def test_a_live_record_writes_no_new_keys() -> None:
    doc = _record().to_arango_base_record()
    assert doc["isDeleted"] is False
    assert doc["deletedByUserId"] is None
    assert not set(NEW_KEYS) & doc.keys()


def test_a_trashed_record_writes_its_delete_state() -> None:
    doc = _record(
        is_deleted=True,
        deleted_at=1_700_000_000_000,
        deleted_by_user_id="u1",
        delete_source=DeleteSource.USER,
        delete_batch_id="batch-1",
        purge_attempts=2,
        purge_last_error="blob store timed out",
        trashed_external_record_id="src/a.pdf",
    ).to_arango_base_record()
    assert doc["isDeleted"] is True
    assert doc["deletedByUserId"] == "u1"
    assert {k: doc[k] for k in NEW_KEYS} == {
        "deletedAtTimestamp": 1_700_000_000_000,
        "deleteSource": "USER",
        "deleteBatchId": "batch-1",
        "purgeAttempts": 2,
        "purgeLastError": "blob store timed out",
        "trashedExternalRecordId": "src/a.pdf",
    }


def test_delete_state_round_trips() -> None:
    original = _record(
        is_deleted=True,
        deleted_at=5,
        deleted_by_user_id="u1",
        delete_source=DeleteSource.CONNECTOR,
        delete_batch_id="b",
        trashed_external_record_id="src/a.pdf",
    )
    back = Record.from_arango_base_record(original.to_arango_base_record())
    assert (back.is_deleted, back.deleted_at, back.deleted_by_user_id, back.delete_source, back.delete_batch_id) == (
        True, 5, "u1", DeleteSource.CONNECTOR, "b",
    )
    assert back.trashed_external_record_id == "src/a.pdf"


def test_a_stored_record_without_the_fields_is_live() -> None:
    back = Record.from_arango_base_record(
        {k: v for k, v in _record().to_arango_base_record().items() if k not in ("isDeleted", "deletedByUserId")}
    )
    assert back.is_deleted is False
    assert back.delete_source is None


def test_an_unknown_delete_source_does_not_break_the_read() -> None:
    doc = {**_record().to_arango_base_record(), "isDeleted": True, "deleteSource": "SOMETHING_NEW"}
    back = Record.from_arango_base_record(doc)
    assert back.is_deleted is True
    assert back.delete_source is None


def test_a_typed_record_carries_the_delete_state() -> None:
    """``get_record_by_id`` and ``get_file_record_by_id`` return typed records;
    the authorizer's live check reads ``is_deleted`` off them."""
    base = {**_record().to_arango_base_record(), "isDeleted": True, "deletedAtTimestamp": 9}
    file_doc = {"_key": "r1", "orgId": "o1", "name": "a.pdf", "isFile": True, "extension": "pdf"}
    typed = FileRecord.from_arango_record(file_doc, base)
    assert typed.is_deleted is True
    assert typed.deleted_at == 9


def test_every_typed_converter_reads_the_delete_state() -> None:
    """A converter that forgot would report a trashed record as live."""
    tree = ast.parse(inspect.getsource(entities))
    missing = []
    converters = 0
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "from_arango_record"):
            if not issubclass(getattr(entities, cls.name), Record):
                continue
            converters += 1
            if "Record.delete_state_from_arango(" not in ast.unparse(fn):
                missing.append(cls.name)
    assert converters >= 16
    assert not missing


class TestArangoSchema:
    """The records collection is strict: a key missing here is a rejected write."""

    @pytest.mark.parametrize("key", ("isDeleted", "deletedByUserId", *NEW_KEYS))
    def test_declared(self, key) -> None:
        assert key in record_schema["rule"]["properties"]

    def test_still_strict(self) -> None:
        assert record_schema["rule"]["additionalProperties"] is False
        assert record_schema["level"] == "strict"

    def test_delete_source_values(self) -> None:
        allowed = record_schema["rule"]["properties"]["deleteSource"]["enum"]
        assert set(allowed) == {"USER", "CONNECTOR", "SYSTEM", None}

    def test_nothing_new_is_required(self) -> None:
        assert not set(NEW_KEYS) & set(record_schema["rule"]["required"])


def test_the_typed_document_carries_no_delete_state() -> None:
    """Deletion lives on the base `records` document; the typed collections are strict too."""
    typed = FileRecord(**{**_record().model_dump(), "is_file": True}).model_copy(
        update={"is_deleted": True, "deleted_at": 1, "delete_source": DeleteSource.USER}
    ).to_arango_record()
    assert not {"isDeleted", "deletedByUserId", *NEW_KEYS} & typed.keys()
