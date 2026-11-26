"""The projection's conditional write (``update_node_fields_if``, ``set_record_properties``) on PostgreSQL: the same tests Neo4j and ArangoDB run
(``tests/integration/graph_permissions/test_record_property_projection.py``), against a real database with the strict record schema.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.services.graph_db.postgres.provider import PostgresGraphProvider
from tests.integration.graph_permissions import (
    test_record_property_projection as shared,
)

from .pg_support import OrgBound, new_org

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


@pytest.fixture(autouse=True)
def _strict_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """These write what indexing writes, so the record schema is enforced (the folder turns it off for its hand-built graphs)."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")


@pytest.fixture
def provider(pg_db) -> OrgBound:
    return OrgBound(PostgresGraphProvider(logging.getLogger("pg-props"), pg_db), new_org())


# ArangoDB's form: one recordProps sub-document, which is what the shared tests read for a provider that is not Neo4j's.
test_each_condition_means_on_the_engine_what_it_means_in_the_reference = shared.test_each_condition_means_on_the_engine_what_it_means_in_the_reference
test_null_or_le_rejects_a_stored_boolean_or_string_like_the_reference = shared.test_null_or_le_rejects_a_stored_boolean_or_string_like_the_reference
test_all_conditions_must_hold_and_none_is_unset = shared.test_all_conditions_must_hold_and_none_is_unset
test_a_missing_node_is_not_applied = shared.test_a_missing_node_is_not_applied
test_no_condition_is_refused = shared.test_no_condition_is_refused
test_strict_schemas_accept_projection = shared.test_strict_schemas_accept_projection
test_removed_property_is_gone = shared.test_removed_property_is_gone
test_a_refused_write_changes_nothing = shared.test_a_refused_write_changes_nothing
test_two_concurrent_writers_with_one_counter_exactly_one_applies = shared.test_two_concurrent_writers_with_one_counter_exactly_one_applies
test_a_ticket_is_projected_once_and_then_costs_no_write = shared.test_a_ticket_is_projected_once_and_then_costs_no_write
test_a_record_that_changed_after_the_read_is_not_overwritten_with_older_values = shared.test_a_record_that_changed_after_the_read_is_not_overwritten_with_older_values


async def test_a_write_is_scoped_to_the_ambient_org(pg_db) -> None:
    """Another org's record under the same key is neither written nor reported as written."""
    p = PostgresGraphProvider(logging.getLogger("pg-props"), pg_db)
    mine, theirs = OrgBound(p, new_org()), OrgBound(p, new_org())
    key = await shared._seed(theirs, shared._ticket())
    assert not await mine.set_record_properties(key, values={"a": "1"}, previous_names=(), controls=shared.CONTROLS, conditions=[shared.Cond("propsVer", "is_null")])
    assert "recordProps" not in await shared._doc(theirs, key)
