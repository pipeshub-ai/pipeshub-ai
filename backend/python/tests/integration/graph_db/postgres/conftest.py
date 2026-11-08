"""PostgreSQL graph provider fixtures. Need a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractContextManager

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _provider_contract_tests_skip_schema_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the provider's mechanics (deletes, hierarchy, trash, access, projection) on hand-built
    nodes and edges, not payloads the application writes, so graph schema validation is off for this folder.
    ``test_schema_enforcement.py`` turns it back on and checks it on a real database."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")


@pytest.fixture
async def pg_db() -> AsyncIterator[KernelDB]:
    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("PG_CORE_URL is not set")
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
