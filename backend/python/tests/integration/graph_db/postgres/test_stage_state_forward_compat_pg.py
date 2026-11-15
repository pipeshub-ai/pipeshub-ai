"""The stage-state schema is forward-compatible on PostgreSQL (ArangoDB and Neo4j: ../test_stage_state_forward_compat.py).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema import node_validator
from app.schema.node_schema_registry import adapt_schema
from tests.integration.graph_db import stage_forward_compat_support as support

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

STAGES = CollectionNames.STAGE_STATES.value


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly after the folder's conftest, which switches validation off for the provider contract tests.
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")


@pytest.fixture
async def org(pg_provider, pg_db, org_ctx, strict) -> AsyncIterator[str]:
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


async def test_unknown_stage_field_and_value_accepted(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await support.unknown_field_and_value_accepted(pg_provider, org)


async def test_unknown_tier_and_priority_read_tolerantly(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await support.unknown_tier_and_priority_read_tolerantly(pg_provider, org)


async def test_a_foreign_row_is_left_byte_identical(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await support.a_foreign_row_is_left_byte_identical(pg_provider, org)


async def test_records_strict_types_checked_and_rollback_floor(
    pg_provider, org, org_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    with org_ctx(org):
        await support.record_nodes_stay_strict(pg_provider, org)
        await support.stage_types_still_checked(pg_provider, org)

        document = support.stage_document(org)
        assert await pg_provider.stage_state_create({**document, "sweepAtMs": 1})
        strict_schema = adapt_schema(support.strict_schema_of_the_previous_release())
        original = node_validator.get_node_schema
        monkeypatch.setattr(
            node_validator, "get_node_schema", lambda name: strict_schema if name == STAGES else original(name)
        )
        with pytest.raises(support.REFUSED):
            await pg_provider.stage_state_create({**support.stage_document(org), "sweepAtMs": 1})
        with pytest.raises(support.REFUSED):
            await pg_provider.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "SPLIT", {"attempt": 1})
        # The application validator checks the patch only (ArangoDB checks the whole stored row): a patch of
        # known fields and a known status on the extended row goes through.
        assert await pg_provider.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "COMPLETED", {"attempt": 1})
