"""Graph schema validation of the PostgreSQL graph provider, against a real database: a refused write changes nothing, an
incomplete upsert is accepted only for a node that already exists (the existence query), and edges are validated.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.schema.node_validator import SchemaValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly after the folder's conftest, which switches validation off for the provider contract tests.
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")


@pytest.fixture
async def org(pg_provider, pg_db, org_ctx, strict) -> AsyncIterator[str]:
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


async def test_an_invalid_document_is_refused_and_nothing_is_written(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        with pytest.raises(SchemaValidationError, match="email"):
            await pg_provider.batch_upsert_nodes([{"_key": "u1", "email": 5}], "users")
        with pytest.raises(SchemaValidationError, match="bogus"):
            await pg_provider.batch_upsert_nodes([{"_key": "u1", "email": "a@b.example", "bogus": 1}], "users")
        assert await pg_provider.get_document("u1", "users") is None


async def test_a_batch_with_one_bad_document_writes_none_of_it(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        with pytest.raises(SchemaValidationError):
            await pg_provider.batch_upsert_nodes(
                [{"_key": "u1", "email": "a@b.example"}, {"_key": "u2", "email": 5}], "users"
            )
        assert await pg_provider.get_existing_keys("users", ["u1", "u2"]) == set()


async def test_an_incomplete_upsert_of_an_absent_node_is_refused(pg_provider, org, org_ctx) -> None:
    # `email` is required, so a node that does not exist yet cannot be created without it.
    with org_ctx(org):
        with pytest.raises(SchemaValidationError, match="email"):
            await pg_provider.batch_upsert_nodes([{"_key": "u1", "fullName": "Ada"}], "users")
        assert await pg_provider.get_document("u1", "users") is None


async def test_an_incomplete_upsert_of_an_existing_node_merges(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        assert await pg_provider.batch_upsert_nodes([{"_key": "u1", "email": "a@b.example"}], "users")
        # No email in the payload, but the node exists: this is an update of one field.
        assert await pg_provider.batch_upsert_nodes([{"_key": "u1", "fullName": "Ada"}], "users")
        got = await pg_provider.get_document("u1", "users")
        assert got["email"] == "a@b.example" and got["fullName"] == "Ada"


async def test_a_batch_mixing_existing_and_absent_incomplete_nodes_is_refused_whole(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        assert await pg_provider.batch_upsert_nodes([{"_key": "u1", "email": "a@b.example"}], "users")
        with pytest.raises(SchemaValidationError, match="u2"):
            await pg_provider.batch_upsert_nodes(
                [{"_key": "u1", "fullName": "Ada"}, {"_key": "u2", "fullName": "Grace"}], "users"
            )
        # The refusal came before any statement: the existing node was not touched either.
        assert "fullName" not in await pg_provider.get_document("u1", "users")


async def test_an_invalid_edge_is_refused(pg_provider, org, org_ctx) -> None:
    # orgAppRelation requires createdAtTimestamp and allows no other property.
    edge = {"from_id": "o1", "from_collection": "orgs", "to_id": "a1", "to_collection": "apps"}
    with org_ctx(org):
        with pytest.raises(SchemaValidationError, match="createdAtTimestamp"):
            await pg_provider.batch_create_edges([edge], "orgAppRelation")
        with pytest.raises(SchemaValidationError, match="bogus"):
            await pg_provider.batch_create_edges([{**edge, "createdAtTimestamp": 1, "bogus": True}], "orgAppRelation")
