"""``unindex_record`` against a real PostgreSQL graph. Needs a migrated database: PG_CORE_URL, PG_POOLER=none."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.support.unindex_world import run_scenario

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_unindex_record_clears_the_extracted_links_and_keeps_a_twin_whole(
    pg_provider: PostgresGraphProvider, org_ctx: Callable[[str], AbstractContextManager]
) -> None:
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        await run_scenario(pg_provider, org)
