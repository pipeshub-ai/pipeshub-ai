"""``get_users_by_emails`` (one query for a record's addresses) on each provider, and the PostgreSQL ``page_record_ids_by_type``."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import for_org
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.identity import IdentityMixin
from app.services.graph_db.postgres.records import RecordsMixin

ROWS = [
    {"_key": "u1", "email": "Ann@Acme.com", "orgId": "o1", "userId": "m1", "isActive": True, "fullName": "Ann"},
    {"_key": "u2", "email": "bob@acme.com", "orgId": "o1", "userId": "m2", "isActive": True, "fullName": "Bob"},
]


def _arango(rows: list[dict]) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock(execute_aql=AsyncMock(return_value=rows))
    return p


def _neo4j(rows: list[dict]) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(return_value=[{"u": r} for r in rows]))
    return p


def _pg(rows: list[dict]) -> IdentityMixin:
    p = object.__new__(IdentityMixin)
    p.logger = logging.getLogger("t")
    p._id_rows = AsyncMock(return_value=[{"id": r["_key"], "doc": {**r, "id": r["_key"]}} for r in rows])
    return p


def _args(p: object) -> dict:
    if isinstance(p, ArangoHTTPProvider):
        return p.http_client.execute_aql.await_args.kwargs["bind_vars"]
    if isinstance(p, Neo4jProvider):
        return p.client.execute_query.await_args.kwargs["parameters"]
    return {}


def _calls(p: object) -> int:
    if isinstance(p, ArangoHTTPProvider):
        return p.http_client.execute_aql.await_count
    if isinstance(p, Neo4jProvider):
        return p.client.execute_query.await_count
    return p._id_rows.await_count


@pytest.mark.parametrize("make", [_arango, _neo4j, _pg], ids=["arango", "neo4j", "postgres"])
class TestGetUsersByEmails:
    @pytest.mark.asyncio
    async def test_one_query_answers_every_address_keyed_by_lowercase_email(self, make) -> None:
        p = make(ROWS)
        with for_org("o1", job="t"):
            got = await p.get_users_by_emails(["ANN@acme.com", "bob@acme.com", "nobody@else.com", "ann@acme.com"], org_id="o1")
        assert _calls(p) == 1
        assert {k: u.id for k, u in got.items()} == {"ann@acme.com": "u1", "bob@acme.com": "u2"}

    @pytest.mark.asyncio
    async def test_no_addresses_run_no_query(self, make) -> None:
        p = make(ROWS)
        with for_org("o1", job="t"):
            assert await p.get_users_by_emails([], org_id="o1") == {}
            assert await p.get_users_by_emails(["", ""], org_id="o1") == {}
        assert _calls(p) == 0

    @pytest.mark.asyncio
    async def test_a_failed_read_answers_nothing_unless_asked_to_raise(self, make) -> None:
        p = make(ROWS)
        for attr in ("http_client", "client"):
            if hasattr(p, attr):
                getattr(p, attr).execute_aql = getattr(p, attr).execute_query = AsyncMock(side_effect=RuntimeError("down"))
        if make is _pg:
            p._id_rows = AsyncMock(side_effect=RuntimeError("down"))
        with for_org("o1", job="t"):
            assert await p.get_users_by_emails(["a@x.io"], org_id="o1") == {}
            with pytest.raises(RuntimeError):
                await p.get_users_by_emails(["a@x.io"], org_id="o1", raise_on_error=True)


@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
@pytest.mark.asyncio
async def test_the_query_is_org_scoped_and_lowercased(make) -> None:
    p = make(ROWS)
    await p.get_users_by_emails(["ANN@acme.com"], org_id="o1")
    assert _args(p)["emails"] == ["ann@acme.com"] and _args(p)["org_id"] == "o1"
    await p.get_users_by_emails(["ann@acme.com"])
    assert "org_id" not in _args(p)


@pytest.mark.asyncio
async def test_postgres_reads_only_the_ambient_org() -> None:
    p = _pg(ROWS)
    with for_org("o1", job="t"):
        assert await p.get_users_by_emails(["ann@acme.com"], org_id="o2") == {}
        p._id_rows.assert_not_awaited()
        await p.get_users_by_emails(["ANN@acme.com"])
    sql, args, _tx = p._id_rows.await_args.args
    assert "/* pipeshub:graph.get_users_by_emails */" in sql and "org_id = $1" in sql and "lower(doc->>'email')" in sql
    assert args == ["o1", ["ann@acme.com"]]


# ---- PostgreSQL page_record_ids_by_type ---------------------------------------------------------------------------------------------

def _records(ids: list[str]) -> RecordsMixin:
    p = object.__new__(RecordsMixin)
    tx = MagicMock(fetch=AsyncMock(return_value=[{"id": i} for i in ids]))

    async def _q(fn, _t, **_):  # noqa: ANN202
        return await fn(tx)

    p._q = _q
    p.tx = tx
    return p


class TestPostgresPageRecordIdsByType:
    @pytest.mark.asyncio
    async def test_a_page_is_keyed_ordered_in_the_ambient_org_and_tagged(self) -> None:
        p = _records(["r1", "r2"])
        with for_org("o1", job="t"):
            assert await p.page_record_ids_by_type("o1", ["MAIL", "TICKET"], after_key="r0", limit=2) == ["r1", "r2"]
        sql, *args = p.tx.fetch.await_args.args
        assert sql.startswith("/* pipeshub:graph.page_record_ids_by_type */")
        assert "n.id > $2" in sql and "ORDER BY n.id" in sql and "isDeleted" in sql
        assert args == ["o1", "r0", ["MAIL", "TICKET"], 2]

    @pytest.mark.asyncio
    async def test_the_first_page_starts_before_every_key(self) -> None:
        p = _records([])
        with for_org("o1", job="t"):
            await p.page_record_ids_by_type("o1", ["MAIL"])
        assert p.tx.fetch.await_args.args[2] == "" and p.tx.fetch.await_args.args[-1] == 500

    @pytest.mark.asyncio
    async def test_zero_limit_and_other_org_run_no_query_and_a_negative_limit_is_rejected(self) -> None:
        p = _records(["r1"])
        with for_org("o1", job="t"):
            assert await p.page_record_ids_by_type("o1", ["MAIL"], limit=0) == []
            assert await p.page_record_ids_by_type("o2", ["MAIL"]) == []
            assert await p.page_record_ids_by_type("o1", []) == []
            with pytest.raises(ValueError):
                await p.page_record_ids_by_type("o1", ["MAIL"], limit=-1)
        p.tx.fetch.assert_not_awaited()


# ---- Email normalization & index-backed direct property comparison ---------------------------------------------------

@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
@pytest.mark.asyncio
async def test_the_query_compares_stored_field_directly_without_scalar_lower(make) -> None:
    p = make(ROWS)
    await p.get_users_by_emails(["ANN@acme.com"], org_id="o1")
    if isinstance(p, ArangoHTTPProvider):
        query = p.http_client.execute_aql.await_args.args[0]
        assert "LOWER(user.email)" not in query
        assert "user.email IN @emails" in query
        assert "user.orgId == @org_id" in query
    elif isinstance(p, Neo4jProvider):
        query = p.client.execute_query.await_args.args[0]
        assert "toLower(u.email)" not in query
        assert "u.email IN $emails" in query
        assert "u.orgId = $org_id" in query


@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
@pytest.mark.asyncio
async def test_duplicate_email_user_nodes_prefer_active_user(make) -> None:
    duplicate_rows = [
        {"_key": "stub1", "email": "ann@acme.com", "orgId": "o1", "userId": "third_party_id", "isActive": False, "fullName": "Ann Stub"},
        {"_key": "real1", "email": "ann@acme.com", "orgId": "o1", "userId": "507f1f77bcf86cd799439011", "isActive": True, "fullName": "Ann Real"},
    ]
    p = make(duplicate_rows)
    got = await p.get_users_by_emails(["ann@acme.com"], org_id="o1")
    assert got["ann@acme.com"].id == "real1"
    assert got["ann@acme.com"].is_active is True


@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
@pytest.mark.asyncio
async def test_get_user_by_email_compares_directly_and_normalizes_input(make) -> None:
    p = make(ROWS)
    got = await p.get_user_by_email("  ANN@acme.com  ", org_id="o1")
    assert got is not None
    assert got.id == "u1"
    if isinstance(p, ArangoHTTPProvider):
        query = p.http_client.execute_aql.await_args.args[0]
        assert "LOWER(user.email)" not in query
        assert "user.email == @email" in query
        assert p.http_client.execute_aql.await_args.kwargs["bind_vars"]["email"] == "ann@acme.com"
    elif isinstance(p, Neo4jProvider):
        query = p.client.execute_query.await_args.args[0]
        assert "toLower(u.email)" not in query
        assert "u.email = $email" in query
        assert p.client.execute_query.await_args.kwargs["parameters"]["email"] == "ann@acme.com"


@pytest.mark.asyncio
async def test_arango_ensures_non_unique_org_email_composite_index() -> None:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock(ensure_persistent_index=AsyncMock())
    p.collection_indexes = {}
    await p._ensure_indexes()
    calls = p.http_client.ensure_persistent_index.await_args_list
    match = [c for c in calls if c.args[0] == "users" and c.args[1] == ["orgId", "email"]]
    assert len(match) == 1
    assert match[0].kwargs.get("unique") is False


def test_neo4j_generates_non_unique_org_email_composite_index() -> None:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    indexes = p._generate_performance_indexes()
    assert any("CREATE INDEX user_org_email IF NOT EXISTS FOR (n:User) ON (n.orgId, n.email)" in q for q in indexes)
