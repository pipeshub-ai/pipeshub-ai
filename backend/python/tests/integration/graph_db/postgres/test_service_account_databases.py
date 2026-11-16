"""A service-account agent uses only the databases shared with its org, on the PostgreSQL graph store.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import pytest

from tests.integration.graph_db.test_service_account_databases_real_backends import (
    check_service_account_databases,
)

pytestmark = pytest.mark.integration


async def test_a_service_account_agent_keeps_only_the_orgs_databases(pg_provider) -> None:
    await check_service_account_databases(pg_provider)
