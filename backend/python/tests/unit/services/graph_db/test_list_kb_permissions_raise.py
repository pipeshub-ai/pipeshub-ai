from unittest.mock import AsyncMock

import pytest

from tests.unit.services.graph_db.test_kb_team_edge_role import (  # noqa: F401
    arango_provider,
    neo4j_provider,
)


@pytest.mark.asyncio
async def test_neo4j_failure_is_empty_by_default_and_raised_on_request(neo4j_provider) -> None:  # noqa: F811
    neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("down"))

    assert await neo4j_provider.list_kb_permissions("kb1") == []
    with pytest.raises(RuntimeError, match="down"):
        await neo4j_provider.list_kb_permissions("kb1", raise_on_error=True)


@pytest.mark.asyncio
async def test_arango_failure_is_empty_by_default_and_raised_on_request(arango_provider) -> None:  # noqa: F811
    arango_provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("down"))

    assert await arango_provider.list_kb_permissions("kb1") == []
    with pytest.raises(RuntimeError, match="down"):
        await arango_provider.list_kb_permissions("kb1", raise_on_error=True)
