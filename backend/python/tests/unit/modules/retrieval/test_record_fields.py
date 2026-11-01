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
from app.services.graph_db.common.utils import (
    PERSON_EDGES_FROM_USER,
    PERSON_EDGES_TO_USER,
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


def test_a_project_lead_is_a_role() -> None:
    assert role_edges("lead") == (EntityRelations.LEAD_BY.value,)


def test_the_roles_cover_exactly_the_person_edges_the_graph_stores() -> None:
    assert set(record_fields.PERSON_EDGES) == set(PERSON_EDGES_TO_USER) | set(PERSON_EDGES_FROM_USER)


def test_each_role_is_one_stored_edge_and_each_edge_one_role() -> None:
    assert all(len(edges) == 1 for edges in record_fields.ROLE_EDGES.values())
    assert len(record_fields.PERSON_EDGES) == len(record_fields.ROLE_EDGES)
    assert role_edges("author") == (EntityRelations.AUTHORED_BY.value,)
    assert role_edges("creator") == (EntityRelations.CREATED_BY.value,)
    assert role_edges("mentioned") == (EntityRelations.MENTIONED_IN.value,)
    assert role_edges("participant") == (EntityRelations.INVOLVED_IN.value,)


def test_the_people_tool_vocabulary_resolves() -> None:
    # find_records_by_person's names before the roles were one list.
    for role in ("assignee", "reporter", "creator", "lead", "mentioned"):
        assert role_edges(role)
    assert role_edges("involved") == role_edges("participant")
    assert role_edges(" Assignee ") == role_edges("assignee")


def test_the_direction_tables_are_the_registry_split_by_stored_direction() -> None:
    from_person = record_fields.EDGES_FROM_PERSON
    assert PERSON_EDGES_TO_USER == tuple(e for e in record_fields.PERSON_EDGES if e not in from_person)
    assert PERSON_EDGES_FROM_USER == tuple(e for e in record_fields.PERSON_EDGES if e in from_person)

