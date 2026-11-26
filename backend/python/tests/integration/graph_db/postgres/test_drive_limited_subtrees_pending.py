"""The Drive connector's pending limited folders (``test_drive_limited_subtrees_pending_e2e``), run unchanged on one PostgreSQL org.

The functions are imported, so they are collected here with a ``connector`` fixture whose sync point is on PostgreSQL.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import TYPE_CHECKING

import pytest

from app.core.db import DbConfig, KernelDB
from tests.integration.graph_db.postgres.pg_support import (
    OrgBound,
    StandInProvider,
    new_org,
)
from tests.integration.test_drive_limited_subtrees_pending_e2e import (  # noqa: F401
    pending_queue,
    test_a_cursor_written_back_is_read_back,
    test_folders_queued_one_after_another_all_stay,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.google.drive.team.connector import (
        GoogleDriveTeamConnector,
    )

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture
async def connector(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[GoogleDriveTeamConnector]:
    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("PG_CORE_URL is not set")
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    org = new_org()
    try:
        yield pending_queue(OrgBound(StandInProvider(logging.getLogger("drive-limited-pending-pg"), db), org), org)
    finally:
        await db.close()
