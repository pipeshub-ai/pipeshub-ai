"""PostgreSQL graph provider fixtures. Need a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractContextManager

@pytest.fixture(autouse=True)
def _provider_contract_tests_skip_schema_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the provider's mechanics (deletes, hierarchy, trash, access, projection) on hand-built
    nodes and edges, not payloads the application writes, so graph schema validation is off for this folder.
    ``test_schema_enforcement.py`` turns it back on and checks it on a real database."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")


@pytest.fixture(autouse=True)
def _no_background_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader repeats 1 % of its answers by the walk on a task of its own, which calls ``decide`` again: a test that counts the decisions of its call would sometimes see two. Tests of the sample set it themselves."""
    monkeypatch.setenv("PG_KG_SHADOW_SAMPLE", "0")


TABLE_LOCK_KEY = 0x6B675F7461626C65       # the advisory lock that runs a ``pg_table_lock`` test alone
ALONE_TIMEOUT_S = 3600 + 600               # a marked test first waits for those running in other workers: the folder's longest timeout, then its own run


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("pg_table_lock"):
            item.add_marker(pytest.mark.timeout(ALONE_TIMEOUT_S), append=False)


@pytest.fixture(autouse=True)
async def _table_locks_run_alone(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """xdist workers share one database. A test marked ``pg_table_lock`` (LOCK TABLE, an index dropped or built) holds this lock exclusively and every other test
    holds it shared, so the marked one runs while no other test of the folder does: a read queued behind its table lock waits out its ``lock_timeout`` and the walk
    answers, which fails a sibling that counts kg's answers."""
    if not os.environ.get("PG_CORE_URL"):
        yield
        return
    con = await asyncpg.connect(os.environ["PG_CORE_URL"])
    try:
        alone = request.node.get_closest_marker("pg_table_lock") is not None
        await con.execute("SELECT pg_advisory_lock($1)" if alone else "SELECT pg_advisory_lock_shared($1)", TABLE_LOCK_KEY)
        yield
    finally:
        await con.close()                       # the session's advisory locks end with it


@pytest.fixture
def pg_required() -> None:
    """Skips a test that builds its own ``KernelDB`` where no database is configured; ``pg_db`` has it too."""
    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("PG_CORE_URL is not set")


@pytest.fixture
async def pg_db(pg_required: None) -> AsyncIterator[KernelDB]:
    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    yield db
    await db.close()


@pytest.fixture
def pg_provider(pg_db) -> PostgresGraphProvider:
    return PostgresGraphProvider(logging.getLogger("pg-graph"), pg_db)


@pytest.fixture
def org_ctx() -> Callable[[str], AbstractContextManager]:
    """``with org_ctx("org-1"):`` binds the ambient org for the block."""
    def make(org: str) -> AbstractContextManager:
        return bind(OrgContext(org, None, "job", "job:test", "t"))
    return make
