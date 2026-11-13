"""Each sync toggle numbers itself in the flip's transaction and sends the number with its event.

The appDisabled handler compares it with the connector's current number, so an off whose event is
handled after a later on cannot turn the connector off again (the 11-07 nightly's Confluence runs).
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_BETA_PATCH = "app.connectors.api.router.check_beta_connector_access"
_EVICT_PATCH = "app.connectors.api.router.evict_cached_connector"


def _request(body: dict[str, Any]) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    req.headers = {}
    req.json = AsyncMock(return_value=body)
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    container.messaging_producer = AsyncMock()
    req.app.container = container
    req.app.state.connector_registry = AsyncMock()
    return req


def _instance(**overrides: object) -> dict[str, Any]:
    return {
        "_key": "c1", "type": "Confluence", "scope": "team", "createdBy": "u1", "authType": "API_TOKEN",
        "isActive": True, "isAgentActive": False, "supportsAgent": True, "isConfigured": True,
        "name": "Wiki", "appGroup": "Atlassian", "appGroupId": None, **overrides,
    }


def _graph(seq: int | None = 7) -> AsyncMock:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"_key": "o1", "accountType": "free"})
    graph.begin_transaction = AsyncMock(return_value="txn1")
    graph.increment_node_field = AsyncMock(return_value=seq)
    return graph


async def _toggle(request: MagicMock, graph: AsyncMock, *, updated: dict | bool | None = True) -> dict[str, Any]:
    from app.connectors.api.router import toggle_connector_instance

    registry = request.app.state.connector_registry
    registry.get_connector_instance = AsyncMock(return_value=_instance())
    registry.update_connector_instance = AsyncMock(return_value=updated)
    with patch(_BETA_PATCH, new_callable=AsyncMock), patch(_EVICT_PATCH, new_callable=AsyncMock):
        return await toggle_connector_instance("c1", request, graph_provider=graph)


@pytest.mark.asyncio
async def test_a_sync_toggle_numbers_itself_with_the_flip_and_sends_the_number() -> None:
    request, graph = _request({"type": "sync"}), _graph(seq=7)

    assert (await _toggle(request, graph))["success"] is True

    graph.begin_transaction.assert_awaited_once()
    graph.increment_node_field.assert_awaited_once_with("c1", "apps", "toggleSeq", transaction="txn1")
    update = request.app.state.connector_registry.update_connector_instance.await_args
    assert update.kwargs["transaction"] == "txn1" and update.kwargs["updates"]["isActive"] is False
    graph.commit_transaction.assert_awaited_once_with("txn1")
    graph.rollback_transaction.assert_not_awaited()
    message = request.app.container.messaging_producer.send_message.await_args.kwargs["message"]
    assert message["eventType"] == "appDisabled"
    assert message["payload"]["toggleSeq"] == 7


@pytest.mark.asyncio
async def test_a_flip_that_does_not_land_takes_its_number_back() -> None:
    request, graph = _request({"type": "sync"}), _graph(seq=7)

    with pytest.raises(HTTPException) as refused:
        await _toggle(request, graph, updated=None)

    assert refused.value.status_code == 404
    graph.rollback_transaction.assert_awaited_once_with("txn1")
    graph.commit_transaction.assert_not_awaited()
    request.app.container.messaging_producer.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_agent_toggle_sends_no_event_and_takes_no_number() -> None:
    request, graph = _request({"type": "agent"}), _graph()

    assert (await _toggle(request, graph))["success"] is True

    graph.begin_transaction.assert_not_awaited()
    graph.increment_node_field.assert_not_awaited()
    assert "transaction" not in request.app.state.connector_registry.update_connector_instance.await_args.kwargs


_CONFIG_PATCH = "app.connectors.api.router.resolve_config_service"
_FILTERS_PATCH = "app.connectors.api.router._validate_sync_filter_selections"
_INIT_PATCH = "app.connectors.api.router._ensure_connector_initialized"


async def _enable_whose_event_fails(graph: AsyncMock, *, revert_lands: bool) -> MagicMock:
    from app.connectors.api.router import toggle_connector_instance

    request = _request({"type": "sync"})
    registry = request.app.state.connector_registry
    registry.get_connector_instance = AsyncMock(return_value=_instance(isActive=False))
    registry.update_connector_instance = AsyncMock(return_value={"updatedAtTimestamp": 1000})
    graph.update_node_fields_if_match = AsyncMock(return_value=revert_lands)
    request.app.container.messaging_producer.send_message = AsyncMock(side_effect=[RuntimeError("broker down"), None])
    with patch(_BETA_PATCH, new_callable=AsyncMock), patch(_EVICT_PATCH, new_callable=AsyncMock), \
         patch(_CONFIG_PATCH, return_value=AsyncMock()), patch(_FILTERS_PATCH, new_callable=AsyncMock), \
         patch(_INIT_PATCH, new_callable=AsyncMock), pytest.raises(HTTPException):
        await toggle_connector_instance("c1", request, graph_provider=graph)
    return request


@pytest.mark.asyncio
async def test_a_reverted_enable_takes_the_next_number_and_sends_the_disable_it_owes() -> None:
    """An off handled while the on stood was skipped as superseded; the revert puts the connector
    off again, so its sync stop and cleanup are sent once more, under a number a later on beats."""
    graph = _graph(seq=7)

    request = await _enable_whose_event_fails(graph, revert_lands=True)

    _, _, reverted, expected = graph.update_node_fields_if_match.await_args.args
    assert reverted["isActive"] is False and reverted["toggleSeq"] == 8
    assert expected == {"updatedAtTimestamp": 1000, "toggleSeq": 7}
    sent = [c.kwargs["message"] for c in request.app.container.messaging_producer.send_message.await_args_list]
    assert [m["eventType"] for m in sent] == ["appEnabled", "appDisabled"]
    assert sent[1]["payload"]["toggleSeq"] == 8 and sent[1]["payload"]["connectorId"] == "c1"


@pytest.mark.asyncio
async def test_a_revert_beaten_by_a_newer_toggle_sends_nothing() -> None:
    graph = _graph(seq=7)

    request = await _enable_whose_event_fails(graph, revert_lands=False)

    sent = [c.kwargs["message"]["eventType"] for c in request.app.container.messaging_producer.send_message.await_args_list]
    assert sent == ["appEnabled"]
