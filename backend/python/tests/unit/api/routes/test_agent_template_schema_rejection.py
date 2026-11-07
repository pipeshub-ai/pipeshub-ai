"""A template body the graph schema rejects is answered 400 (the caller's mistake), not 500."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jsonschema import ValidationError as JsonSchemaValidationError

from app.api.routes.agent import create_agent_template
from app.schema.node_validator import SchemaValidationError


async def test_a_schema_rejected_template_is_a_400_and_counted() -> None:
    graph = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock(
        side_effect=SchemaValidationError(
            "agentTemplates", "at 'tags': 'x' is not of type 'array'",
            JsonSchemaValidationError("'x' is not of type 'array'", path=["tags"]),
        )
    )
    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"name":"T1","description":"Desc","systemPrompt":"SP","tags":"x"}')

    with (
        patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value={"graph_provider": graph, "logger": MagicMock()}),
        patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}),
        patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "k1"}),
        patch("app.api.routes.agent.record_schema_rejection") as counted,
        pytest.raises(HTTPException) as exc,
    ):
        await create_agent_template(request)

    assert exc.value.status_code == 400
    assert "tags" in exc.value.detail
    assert "agentTemplates" not in exc.value.detail
    counted.assert_called_once_with("agentTemplates")
    graph.batch_create_edges.assert_not_awaited()
