"""Agents store the API toolset operations they may call (`apiToolsetTools`), checked on save."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.openapi import service
from app.agents.openapi.store import ApiToolsetStore
from app.api.routes.agent import create_agent, update_agent
from tests.support.in_memory_config import InMemoryConfigService

ACME_SPEC = (Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture" / "openapi.yaml").read_bytes()
PER_MEMBER, SHARED = "a" * 32, "b" * 32
MODEL = {"modelKey": "mk1", "modelName": "mn1", "isReasoning": True}


async def _services() -> dict[str, Any]:
    config = InMemoryConfigService()
    store = ApiToolsetStore(config)
    for instance_id, shared in ((PER_MEMBER, False), (SHARED, True)):
        await store.save_instance(service.build_instance(
            service.InstanceInput(name=f"Acme {instance_id[0]}", base_url="https://helpdesk.acme.test", operations=["list_tickets"],
                                  spec=ACME_SPEC, auth_scheme_name="apiKeyAuth", use_shared_credential=shared),
            instance_id=instance_id, org_id="o1", user_id="u1",
        ))
    graph = AsyncMock()
    graph.begin_transaction = AsyncMock(return_value="txn-1")
    graph.batch_upsert_nodes = AsyncMock(return_value=True)
    graph.batch_create_edges = AsyncMock(return_value=True)
    graph.commit_transaction = AsyncMock()
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": True})
    graph.update_agent = AsyncMock(return_value=True)
    return {"graph_provider": graph, "logger": MagicMock(), "config_service": config}


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    return request


async def _call(handler: Any, services: dict[str, Any], *args: Any) -> Any:  # noqa: ANN401
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):
        return await handler(*args)


def _stored_agent(services: dict[str, Any]) -> dict[str, Any]:
    for call in services["graph_provider"].batch_upsert_nodes.await_args_list:
        for node in call.args[0]:
            if "systemPrompt" in node:
                return node
    raise AssertionError("no agent was written")


@pytest.mark.asyncio
async def test_create_stores_the_attached_operations() -> None:
    services = await _services()
    body = {"name": "A1", "models": [MODEL], "apiToolsetTools": [f"{PER_MEMBER}:list_tickets"]}
    result = await _call(create_agent, services, _request(body))
    assert result.status_code == 200
    assert _stored_agent(services)["apiToolsetTools"] == [f"{PER_MEMBER}:list_tickets"]


@pytest.mark.asyncio
async def test_create_without_attachments_stores_an_empty_list() -> None:
    services = await _services()
    await _call(create_agent, services, _request({"name": "A1", "models": [MODEL]}))
    assert _stored_agent(services)["apiToolsetTools"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_extra", "message"),
    [
        ({"apiToolsetTools": [f"{'c' * 32}:list_tickets"]}, "doesn't exist"),
        ({"apiToolsetTools": [f"{PER_MEMBER}:list_tickets"], "isServiceAccount": True}, "shared credential"),
    ],
)
async def test_create_refuses_what_it_cannot_use(body_extra: dict[str, Any], message: str) -> None:
    services = await _services()
    with pytest.raises(HTTPException) as raised:
        await _call(create_agent, services, _request({"name": "A1", "models": [MODEL], **body_extra}))
    assert raised.value.status_code == 400 and message in str(raised.value.detail)
    services["graph_provider"].batch_upsert_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_checks_and_passes_the_attachments_on() -> None:
    services = await _services()
    services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
    result = await _call(update_agent, services, _request({"apiToolsetTools": [f"{SHARED}:list_tickets"]}), "a1")
    assert result.status_code == 200
    sent = services["graph_provider"].update_agent.await_args.args[1]
    assert sent["apiToolsetTools"] == [f"{SHARED}:list_tickets"]


@pytest.mark.asyncio
async def test_becoming_a_service_account_rechecks_what_is_attached() -> None:
    services = await _services()
    services["graph_provider"].get_agent = AsyncMock(return_value={
        "name": "A1", "can_edit": True, "apiToolsetTools": [f"{PER_MEMBER}:list_tickets"],
    })
    with pytest.raises(HTTPException) as raised:
        await _call(update_agent, services, _request({"isServiceAccount": True}), "a1")
    assert raised.value.status_code == 400 and "shared credential" in str(raised.value.detail)
    services["graph_provider"].update_agent.assert_not_awaited()
