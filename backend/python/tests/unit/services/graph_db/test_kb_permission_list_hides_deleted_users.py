"""A deleted user is not listed as having access to a knowledge base, in both graph providers.

Deleting a user keeps their graph node, marked ``isActive: false``, together
with its permission edges. The sharing list read those edges as they were, so
an admin saw a deleted person as still having access. "Active" here means what
the team member lists already use: ``isActive`` is true.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.mark.asyncio
async def test_neo4j_list_keeps_active_users_and_teams_only() -> None:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=None)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[])

    await provider.list_kb_permissions("kb-1")

    query = provider.client.execute_query.await_args.args[0]
    assert "WHERE NOT entity:User OR entity.isActive = true" in query


@pytest.mark.asyncio
async def test_arango_list_keeps_active_users_only() -> None:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.execute_query = AsyncMock(return_value=[])

    await provider.list_kb_permissions("kb-1")

    query = provider.execute_query.await_args.args[0]
    users_block = query[query.index("LET users"):query.index("LET team_ids")]
    assert "user.isActive == true" in users_block
    teams_block = query[query.index("LET teams"):query.index("FOR perm_data")]
    assert "isActive" not in teams_block, "teams have no active flag and must stay listed"
