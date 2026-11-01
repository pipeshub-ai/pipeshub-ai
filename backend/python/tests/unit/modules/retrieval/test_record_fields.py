"""The field registry names exactly the properties the record models write."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import EntityRelations
from app.models.entities import DealRecord, RecordType, TicketRecord
from app.modules.retrieval import record_fields
from app.modules.retrieval.record_fields import (
    FIELDS,
    FieldKind,
    Store,
    field,
    role_edges,
)


def _stored_properties(store: Store) -> set[str]:
    if store is Store.TICKET:
        return set(TicketRecord(
            org_id="o", record_name="t", record_type=RecordType.TICKET, external_record_id="x",
            version=1, origin="CONNECTOR", connector_name="JIRA", connector_id="c",
        ).to_arango_record())
    if store is Store.DEAL:
        return set(DealRecord(
            org_id="o", record_name="d", record_type=RecordType.DEAL, external_record_id="x",
            version=1, origin="CONNECTOR", connector_name="SALESFORCE", connector_id="c",
        ).to_arango_record())
    return set()


@pytest.mark.parametrize("store", [Store.TICKET, Store.DEAL])
def test_every_stored_field_is_a_property_its_record_model_writes(store: Store) -> None:
    # A renamed property would silently match nothing; this keeps the two in step.
    written = _stored_properties(store)
    declared = {f.property for f in FIELDS.values() if f.store is store}
    assert declared <= written, declared - written


def test_edge_and_derived_fields_name_no_property() -> None:
    assert all(f.property is None for f in FIELDS.values() if f.store in (Store.EDGE, Store.DERIVED))
    assert all(f.property for f in FIELDS.values() if f.store not in (Store.EDGE, Store.DERIVED))


def test_value_fields_list_their_values() -> None:
    assert "RESOLVED" in field("status").values and "HIGHEST" in field("priority").values
    assert field("deal_outcome").values == ("WON", "LOST", "OPEN")
    assert all(f.values for f in FIELDS.values() if f.kind is FieldKind.VALUE and f.store is not Store.RECORD)


def test_every_role_maps_to_real_edge_types_and_no_role_means_all() -> None:
    known = {e.value for e in EntityRelations}
    assert set(record_fields.PERSON_EDGES) <= known
    assert role_edges("assignee") == ("ASSIGNED_TO",)
    assert set(role_edges(None)) == set(record_fields.PERSON_EDGES)
    assert set(record_fields.ORGANIZATION_EDGES) <= known


def test_an_unknown_field_or_role_names_the_choices() -> None:
    with pytest.raises(ValueError, match="fields: amount"):
        field("colour")
    with pytest.raises(ValueError, match="roles: assignee"):
        role_edges("sponsor")
