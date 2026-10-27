"""PostgreSQL runtime tests (key-value store, work queue). They need a migrated database; without one they skip.

``PG_RUNTIME_TEST_URL`` is the login DSN of a service role (``pipeshub_connectors_login``) on a database migrated to the heads
(``python -m db.migrate``), reached directly (no pooler), e.g. ``postgresql://pipeshub_connectors_login@127.0.0.1:5432/pipeshub``.
"""

import os
import uuid

import pytest

from app.services.pg_runtime import RuntimeDb
from tests.support.require_service import missing

URL = os.environ.get("PG_RUNTIME_TEST_URL", "")


@pytest.fixture
async def runtime(monkeypatch) -> RuntimeDb:
    if not URL:
        missing("PG_RUNTIME_TEST_URL is not set")
    monkeypatch.setenv("PG_CORE_URL", URL)
    monkeypatch.setenv("PG_POOLER", "none")
    monkeypatch.setenv("PG_RUNTIME_SERVICE", "connectors")
    monkeypatch.delenv("PG_LISTEN_URL", raising=False)
    return RuntimeDb()


@pytest.fixture
def uid() -> str:
    return uuid.uuid4().hex[:10]
