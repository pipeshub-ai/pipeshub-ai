"""``typed_record``: the record document and its type document build the typed record, and nothing else does."""
import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import CodeFileRecord, RecordType
from app.services.graph_db.common.typed_record import typed_record


def _code() -> CodeFileRecord:
    return CodeFileRecord(id="r", org_id="o", record_name="a.py", record_type=RecordType.CODE_FILE, external_record_id="e", version=1, origin=OriginTypes.CONNECTOR,
                          connector_name=Connectors.GITHUB, connector_id="c", mime_type="text/x-python", extension="py", file_path="a.py", file_hash="h", language="python")


def test_a_record_and_its_type_document_build_the_typed_record() -> None:
    rec = _code()
    base = {**rec.to_arango_base_record(), "_key": "r"}
    got = typed_record(base, {**rec.to_arango_record(), "_key": "r"})
    assert isinstance(got, CodeFileRecord) and got.file_path == "a.py"


def test_no_type_document_or_no_type_collection_is_an_error() -> None:
    rec = _code()
    with pytest.raises(ValueError, match="No type collection"):
        typed_record({**rec.to_arango_base_record(), "_key": "r"}, None)
    with pytest.raises(ValueError, match="No type collection"):
        typed_record({**rec.to_arango_base_record(), "recordType": "DRIVE"}, {"x": 1})
