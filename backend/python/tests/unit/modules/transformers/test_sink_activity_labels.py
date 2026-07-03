"""Labels for the ``document_indexed`` activity counter (SinkOrchestrator).

KB record docs don't carry ``recordGroupId`` — the KB UUID is stored as
``externalGroupId`` — so ``_activity_labels`` must fall back to
``external_record_group_id`` or the "Documents indexed by KB" dashboard
panel stays empty (every series lands with kb="none").
"""

from app.config.constants.arangodb import Connectors
from app.models.entities import Record, RecordGroupType, RecordType
from app.modules.transformers.sink_orchestrator import SinkOrchestrator


def _record(**overrides) -> Record:
    base = dict(
        id="rec-1",
        record_name="doc.pdf",
        record_type=RecordType.FILE,
        record_group_type=None,
        external_record_id="ext-rec-1",
        version=1,
        origin="UPLOAD",
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="conn-1",
        org_id="org-1",
        mime_type="application/pdf",
    )
    base.update(overrides)
    return Record(**base)


def test_kb_record_uses_external_group_id_when_record_group_id_missing() -> None:
    record = _record(external_record_group_id="kb-uuid-1")
    connector, org, kb = SinkOrchestrator._activity_labels(record)
    assert connector == "KB"
    assert org == "org-1"
    assert kb == "kb-uuid-1"


def test_kb_record_prefers_record_group_id() -> None:
    record = _record(
        record_group_id="kb-uuid-primary",
        external_record_group_id="kb-uuid-fallback",
    )
    assert SinkOrchestrator._activity_labels(record)[2] == "kb-uuid-primary"


def test_kb_record_without_any_group_id_is_none() -> None:
    assert SinkOrchestrator._activity_labels(_record())[2] == "none"


def test_kb_by_record_group_type() -> None:
    record = _record(
        connector_name=Connectors.GOOGLE_DRIVE,
        record_group_type=RecordGroupType.KB,
        external_record_group_id="kb-uuid-2",
    )
    assert SinkOrchestrator._activity_labels(record)[2] == "kb-uuid-2"


def test_non_kb_record_never_gets_kb_label() -> None:
    record = _record(
        connector_name=Connectors.GOOGLE_DRIVE,
        record_group_type=RecordGroupType.DRIVE,
        external_record_group_id="drive-folder-1",
    )
    connector, _org, kb = SinkOrchestrator._activity_labels(record)
    assert connector == Connectors.GOOGLE_DRIVE.value
    assert kb == "none"
