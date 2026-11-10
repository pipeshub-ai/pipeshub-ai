"""The connector token refresh scan on PostgreSQL: it runs detached (no org bound) and must still reach every org's OAuth connectors,
each refreshed under its own org (``docs/org-context.md``: enumerate under ``system``, act under ``for_org``).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.core import org_context
from app.core.org_context import OrgContext, bind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    """Two orgs, each with an OAuth connector, an admin-consent one, an API-token one and an unauthenticated one."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {name}", "isActive": True}], "organizations")
            await pg_provider.batch_upsert_nodes(
                [{"_key": f"oauth-{name}-{tag}", "name": "GitLab", "type": "GITLAB", "authType": "OAUTH", "isActive": True, "orgId": org},
                 {"_key": f"consent-{name}-{tag}", "name": "Outlook", "type": "OUTLOOK", "authType": "OAUTH_ADMIN_CONSENT", "isActive": False, "orgId": org},
                 {"_key": f"api-{name}-{tag}", "name": "Jira", "type": "JIRA", "authType": "API_TOKEN", "isActive": True, "orgId": org},
                 {"_key": f"dead-{name}-{tag}", "name": "Drive", "type": "DRIVE", "authType": "OAUTH", "isAuthenticated": False, "orgId": org}],
                "apps")
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_a_detached_scan_refreshes_every_orgs_oauth_connectors_under_their_org(pg_provider, two_orgs, caplog) -> None:
    a, b, tag = two_orgs
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"credentials": {"refresh_token": "rt"}})
    svc = TokenRefreshService(config, pg_provider)
    seen: list[tuple[str, str, str]] = []

    async def record(connector_id: str, connector_type: str) -> None:
        seen.append((connector_id, connector_type, org_context.current_org_id()))
    svc._refresh_connector_token = record

    caplog.set_level(logging.DEBUG)
    # As TokenRefreshService.start runs it: spawn_detached, an empty context.
    await asyncio.get_running_loop().create_task(svc._refresh_all_tokens(), context=contextvars.Context())

    assert not [r.getMessage() for r in caplog.records if "Error refreshing tokens" in r.getMessage()]
    assert sorted(s for s in seen if s[0].endswith(tag)) == sorted([
        (f"oauth-a-{tag}", "GITLAB", a), (f"consent-a-{tag}", "OUTLOOK", a),
        (f"oauth-b-{tag}", "GITLAB", b), (f"consent-b-{tag}", "OUTLOOK", b),
    ])
