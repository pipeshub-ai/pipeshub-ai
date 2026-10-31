"""Indexing and extraction status on typed records read from the graph.

The typed converters (``FileRecord.from_arango_record`` and the rest) left both fields at their defaults, so a record
read by id (``get_record_by_id``, which ``lookup_record`` uses) reported itself QUEUED whatever its stored status: the
lookup told the agent an indexed record "is not indexed yet … its content cannot be read".
"""

from __future__ import annotations

import ast
import inspect

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.models import entities
from app.models.entities import FileRecord, Record, RecordType


def _base(**overrides) -> dict:
    record = Record(
        id="r1", org_id="o1", record_name="pr-1234-retry-budget.txt", record_type=RecordType.FILE,
        external_record_id="e1", version=1, origin=OriginTypes.UPLOAD, connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="c1", virtual_record_id="vr-1",
    )
    return {**record.to_arango_base_record(), **overrides}


def test_a_typed_record_carries_its_stored_status() -> None:
    base = _base(indexingStatus=ProgressStatus.COMPLETED.value, extractionStatus=ProgressStatus.COMPLETED.value,
                 virtualRecordId="vr-1")
    typed = FileRecord.from_arango_record({"_key": "r1", "isFile": True, "extension": "txt"}, base)
    assert typed.indexing_status == ProgressStatus.COMPLETED.value
    assert typed.extraction_status == ProgressStatus.COMPLETED.value
    assert typed.virtual_record_id == "vr-1"


def test_a_record_stored_without_a_status_keeps_the_defaults() -> None:
    base = _base()
    base.pop("indexingStatus", None)
    base.pop("extractionStatus", None)
    typed = FileRecord.from_arango_record({"_key": "r1", "isFile": True}, base)
    assert typed.indexing_status == ProgressStatus.QUEUED.value
    assert typed.extraction_status == ProgressStatus.NOT_STARTED.value


def test_every_typed_converter_reads_the_stored_status() -> None:
    """A converter that forgot reports every record of its type as not indexed."""
    tree = ast.parse(inspect.getsource(entities))
    missing = []
    converters = 0
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "from_arango_record"):
            if not issubclass(getattr(entities, cls.name), Record):
                continue
            converters += 1
            source = ast.unparse(fn)
            if "Record.progress_state_from_arango(" not in source and "indexing_status=" not in source:
                missing.append(cls.name)
    assert converters >= 16
    assert not missing
