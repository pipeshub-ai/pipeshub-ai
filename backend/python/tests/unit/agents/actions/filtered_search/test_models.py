"""Unit tests for `filtered_search.models`: the trimmed
`FilterCapabilityDescriptor` (vocabulary-mapping only, no filter-field
declarations) and the plain result models.

There is no `FilterSpec` anymore — filters are native query strings the
adapters validate directly (see `test_adapter_*.py`)."""

from app.agents.actions.filtered_search.models import (
    CustomFieldDef,
    FilterCapabilityDescriptor,
    FilteredRecord,
    FilteredSearchUniverse,
    GroupReference,
)
from app.models.entities import RecordGroupType


def test_capability_descriptor_declares_vocabulary_mapping_only() -> None:
    caps = FilterCapabilityDescriptor(
        connector_type="JIRA",
        record_group_noun="project",
        container_group_types=[RecordGroupType.PROJECT],
        group_reference=GroupReference.SHORT_NAME,
        supports_custom_fields=True,
    )
    assert caps.connector_type == "JIRA"
    assert caps.container_group_types == [RecordGroupType.PROJECT]
    assert caps.excluded_group_types == []
    assert caps.people_coverage_note is None


def test_capability_descriptor_excluded_group_types_default_empty() -> None:
    caps = FilterCapabilityDescriptor(
        connector_type="SLACK",
        record_group_noun="channel",
        container_group_types=[RecordGroupType.SLACK_CHANNEL],
        excluded_group_types=[RecordGroupType.SLACK_THREAD],
        group_reference=GroupReference.EXTERNAL_ID,
    )
    assert caps.excluded_group_types == [RecordGroupType.SLACK_THREAD]


def test_filtered_search_universe_defaults() -> None:
    universe = FilteredSearchUniverse(connector_type="JIRA", native_query="project = X")
    assert universe.records == []
    assert universe.total_available is None
    assert universe.truncated is False


def test_filtered_record_optional_fields_default_none() -> None:
    record = FilteredRecord(external_id="1", name="Issue 1")
    assert record.web_url is None
    assert record.external_group_id is None


def test_custom_field_def_clause_name_optional() -> None:
    field = CustomFieldDef(field_id="customfield_1", name="Story Points", field_type="number")
    assert field.clause_name is None
    assert field.allowed_values is None
