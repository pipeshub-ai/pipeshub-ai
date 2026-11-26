"""``unindex_record`` against real Neo4j and ArangoDB (PostgreSQL: ``postgres/test_unindex_record_pg.py``).

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD (as the backend-matrix workflow sets them).
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.integration.graph_db.test_record_organizations_real_backends import (
    backend,  # noqa: F401 - the fixture
)
from tests.support.unindex_world import run_scenario

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.mark.asyncio
async def test_unindex_record_clears_the_extracted_links_and_keeps_a_twin_whole(backend: tuple[Any, str]) -> None:  # noqa: F811
    provider, org = backend
    await provider.ensure_schema()
    await run_scenario(provider, org)
