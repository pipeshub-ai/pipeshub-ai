"""A service-account agent uses only the sources shared with its org, on the PostgreSQL graph store.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import pytest

from tests.integration.graph_db.test_service_account_knowledge_real_backends import (
    check_service_account_knowledge,
)

pytestmark = pytest.mark.integration


async def test_a_service_account_agent_keeps_only_what_is_shared_with_the_org(pg_provider) -> None:
    await check_service_account_knowledge(pg_provider)
