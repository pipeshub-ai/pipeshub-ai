"""A toggle that names the state it wants (`enabled`) can be sent twice with the same result.

Node retries a request whose connection drops, and the first attempt may already have landed. A
flip would then be undone by the retry; a request for a state the connector is already in changes
nothing and sends no event. A request without `enabled` still flips, for clients not yet sending it.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_BETA_PATCH = "app.connectors.api.router.check_beta_connector_access"
_EVICT_PATCH = "app.connectors.api.router.evict_cached_connector"
_CONFIG_PATCH = "app.connectors.api.router.resolve_config_service"
_FILTERS_PATCH = "app.connectors.api.router._validate_sync_filter_selections"
_INIT_PATCH = "app.connectors.api.router._ensure_connector_initialized"


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


def _graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"_key": "o1", "accountType": "free"})
    graph.begin_transaction = AsyncMock(return_value="txn1")
    graph.increment_node_field = AsyncMock(return_value=7)
    return graph


async def _toggle(body: dict[str, Any], **instance: object) -> tuple[dict[str, Any], MagicMock, AsyncMock]:
    from app.connectors.api.router import toggle_connector_instance

    request, graph = _request(body), _graph()
    registry = request.app.state.connector_registry
    registry.get_connector_instance = AsyncMock(return_value=_instance(**instance))
    registry.update_connector_instance = AsyncMock(return_value={"updatedAtTimestamp": 1000})
    with patch(_BETA_PATCH, new_callable=AsyncMock), patch(_EVICT_PATCH, new_callable=AsyncMock), \
         patch(_CONFIG_PATCH, return_value=AsyncMock()), patch(_FILTERS_PATCH, new_callable=AsyncMock), \
         patch(_INIT_PATCH, new_callable=AsyncMock):
        result = await toggle_connector_instance("c1", request, graph_provider=graph)
    return result, request, graph


def _assert_nothing_written(request: MagicMock, graph: AsyncMock) -> None:
    request.app.state.connector_registry.update_connector_instance.assert_not_awaited()
    graph.begin_transaction.assert_not_awaited()
    graph.increment_node_field.assert_not_awaited()
    request.app.container.messaging_producer.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_active", [True, False])
async def test_a_sync_request_for_the_current_state_changes_nothing(is_active: bool) -> None:
    result, request, graph = await _toggle({"type": "sync", "enabled": is_active}, isActive=is_active)

    assert result["success"] is True
    assert result["enabled"] is is_active and result["changed"] is False
    _assert_nothing_written(request, graph)


@pytest.mark.asyncio
async def test_a_retried_enable_does_not_turn_the_connector_back_off() -> None:
    """The case a flip gets wrong: the first enable landed, and its retry finds the connector on."""
    first, request, _ = await _toggle({"type": "sync", "enabled": True}, isActive=False)
    assert first["changed"] is True and first["enabled"] is True
    update = request.app.state.connector_registry.update_connector_instance.await_args
    assert update.kwargs["updates"]["isActive"] is True

    retry, request, graph = await _toggle({"type": "sync", "enabled": True}, isActive=True)
    assert retry["changed"] is False and retry["enabled"] is True
    _assert_nothing_written(request, graph)


@pytest.mark.asyncio
async def test_a_sync_disable_writes_the_requested_state_and_sends_its_event() -> None:
    result, request, graph = await _toggle({"type": "sync", "enabled": False}, isActive=True)

    assert result["changed"] is True and result["enabled"] is False
    registry = request.app.state.connector_registry
    lookup = registry.get_connector_instance.await_args
    assert lookup.kwargs == {"connector_id": "c1", "user_id": "u1", "org_id": "o1", "is_admin": True}
    update = registry.update_connector_instance.await_args
    assert update.kwargs["connector_id"] == "c1" and update.kwargs["transaction"] == "txn1"
    assert (update.kwargs["user_id"], update.kwargs["org_id"], update.kwargs["is_admin"]) == ("u1", "o1", True)
    assert update.kwargs["updates"]["isActive"] is False
    graph.increment_node_field.assert_awaited_once()
    message = request.app.container.messaging_producer.send_message.await_args.kwargs["message"]
    assert message["eventType"] == "appDisabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_agent_active", [True, False])
async def test_an_agent_request_sets_the_state_it_names(is_agent_active: bool) -> None:
    wanted = not is_agent_active
    result, request, _ = await _toggle({"type": "agent", "enabled": wanted}, isAgentActive=is_agent_active)

    assert result["changed"] is True and result["enabled"] is wanted
    update = request.app.state.connector_registry.update_connector_instance.await_args
    assert update.kwargs["updates"]["isAgentActive"] is wanted


@pytest.mark.asyncio
async def test_an_agent_request_for_the_current_state_changes_nothing() -> None:
    result, request, graph = await _toggle({"type": "agent", "enabled": False}, isAgentActive=False)

    assert result["changed"] is False and result["enabled"] is False
    _assert_nothing_written(request, graph)


@pytest.mark.asyncio
async def test_a_disable_of_a_disabled_connector_skips_the_enable_checks() -> None:
    """An unconfigured, off agent asked to be off is not refused as 'must be configured before enabling'."""
    result, request, graph = await _toggle(
        {"type": "agent", "enabled": False}, isAgentActive=False, isConfigured=False, supportsAgent=False,
    )

    assert result["changed"] is False
    _assert_nothing_written(request, graph)


@pytest.mark.asyncio
@pytest.mark.parametrize(("toggle_type", "field", "current"), [("sync", "isActive", True), ("agent", "isAgentActive", True)])
async def test_a_request_without_enabled_still_flips(toggle_type: str, field: str, current: bool) -> None:
    result, request, _ = await _toggle({"type": toggle_type}, **{field: current})

    assert result["changed"] is True and result["enabled"] is (not current)
    update = request.app.state.connector_registry.update_connector_instance.await_args
    assert update.kwargs["updates"][field] is (not current)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", ["true", 1, 0, "false", [], {}, None])
async def test_an_enabled_that_is_not_a_boolean_is_refused(enabled: object) -> None:
    with pytest.raises(HTTPException) as refused:
        await _toggle({"type": "sync", "enabled": enabled})

    assert refused.value.status_code == 400


@pytest.mark.asyncio
async def test_a_retry_that_changes_nothing_succeeds_while_its_sync_holds_the_lock() -> None:
    """The enable started a full sync, whose prep locks the connector; its retry still answers 200."""
    result, request, graph = await _toggle({"type": "sync", "enabled": True}, isActive=True, isLocked=True, status="FULL_SYNCING")

    assert result["changed"] is False and result["enabled"] is True
    _assert_nothing_written(request, graph)


@pytest.mark.asyncio
async def test_a_change_to_a_locked_connector_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        await _toggle({"type": "sync", "enabled": False}, isActive=True, isLocked=True, status="SYNCING")

    assert refused.value.status_code == 409
