"""PostgreSQL graph provider fixtures. Need a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none."""
from __future__ import annotations

import logging
import os

import pytest

from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = pytest.mark.integration


@pytest.fixture
async def pg_db():
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
def org_ctx():
    """``with org_ctx("org-1"):`` binds the ambient org for the block."""
    def make(org: str):
        return bind(OrgContext(org, None, "job", "job:test", "t"))
    return make
