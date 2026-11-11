"""Who-sees-what is written by the vetted kg writers alone: the query service's role can read the typed tables, and a direct write of any of them, or a call of a writer, is refused by the database."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind

from .pg_support import new_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

ACCESS_TABLES = ("scope_members", "principal_closure", "scope_classes", "class_literals", "scopes", "records", "links", "node_keys", "projection_state", "restamp_queue")
WRITES = {
    "INSERT": "INSERT INTO kg.{t} SELECT * FROM kg.{t} WHERE false",
    "UPDATE": "UPDATE kg.{t} SET org_id = org_id WHERE false",
    "DELETE": "DELETE FROM kg.{t} WHERE false",
}


@pytest.fixture
async def query_db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[KernelDB]:
    if not os.environ.get("PG_QUERY_URL"):
        pytest.skip("PG_QUERY_URL is not set")
    monkeypatch.setenv("PG_CORE_URL", os.environ["PG_QUERY_URL"])
    db = KernelDB(DbConfig.from_env("query"))
    await db.start()
    yield db
    await db.close()


async def _refused(db: KernelDB, org: str, sql: str) -> str:
    async def go(tx) -> None:
        await tx.execute(sql)
    with bind(OrgContext(org, None, "job", "job:test", "t")), pytest.raises(Exception) as e:
        await db.run(go, write=True)
    return str(e.value).lower()


@pytest.mark.parametrize("table", ACCESS_TABLES)
@pytest.mark.parametrize("verb", sorted(WRITES))
async def test_the_query_role_cannot_write_a_typed_table_directly(query_db, table, verb) -> None:
    assert "permission denied" in await _refused(query_db, new_org(), WRITES[verb].format(t=table))


async def test_the_query_role_cannot_stamp_a_class_or_call_a_writer(query_db) -> None:
    org = new_org()
    assert "permission denied" in await _refused(query_db, org, "UPDATE kg.records SET class_id = 1 WHERE org_id = '%s'" % org)
    assert "permission denied" in await _refused(query_db, org, "SELECT kg.w_grants_delete(ARRAY[]::bigint[], ARRAY[]::bigint[], ARRAY[]::text[])")


async def test_the_query_role_reads_the_typed_tables(query_db) -> None:
    org = new_org()

    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.scope_members WHERE org_id = $1", org)
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        assert await query_db.run(go) == 0
