"""Template routes: null memory becomes the default; only the template upsert is blamed on the caller."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.agent import create_agent_template, update_agent_template
from app.schema.node_validator import SchemaValidationError

_BODY = b'{"name":"T1","description":"Desc","systemPrompt":"SP","memory":null}'


def _patches(graph: AsyncMock) -> tuple:
    return (
        patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value={"graph_provider": graph, "logger": MagicMock()}),
        patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}),
        patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "k1"}),
    )


async def test_null_memory_is_replaced_on_create() -> None:
    graph = AsyncMock()
    request = MagicMock()
    request.body = AsyncMock(return_value=_BODY)
    a, b, c = _patches(graph)
    with a, b, c:
        await create_agent_template(request)
    assert graph.batch_upsert_nodes.await_args.args[0][0]["memory"] == {"type": []}


async def test_null_memory_is_replaced_on_update() -> None:
    graph = AsyncMock()
    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"memory":null}')
    a, b, c = _patches(graph)
    with a, b, c:
        await update_agent_template(request, "t1")
    assert graph.update_agent_template.await_args.args[1] == {"memory": {"type": []}}


async def test_edge_rejection_is_a_500_not_a_400() -> None:
    graph = AsyncMock()
    graph.batch_create_edges = AsyncMock(side_effect=SchemaValidationError("permission", "boom"))
    request = MagicMock()
    request.body = AsyncMock(return_value=_BODY)
    a, b, c = _patches(graph)
    with a, b, c, pytest.raises(HTTPException) as exc:
        await create_agent_template(request)
    assert exc.value.status_code == 500
