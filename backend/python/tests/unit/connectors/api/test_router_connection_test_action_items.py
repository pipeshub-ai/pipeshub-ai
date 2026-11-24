"""What a connection test finds for the admin is stored on the instance before any sync runs.

Drupal Wiki 8.44 refuses the GraphQL queries for a personal access token; the
connection test passes but notes it as an action item, which the instance card shows.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.api import router as router_module
from app.connectors.core.base.connector.connector_service import (
    ActionItem,
    action_items_json,
)
from app.connectors.core.constants import ConnectorStateKeys

CONNECTOR_ID = "wiki-conn-1"
ORG_ID = "org-1"


async def _turn_on(last_action_items: list[ActionItem] | None) -> MagicMock:
    connector = MagicMock()
    connector.init = AsyncMock(return_value=True)

    async def test_connection_and_access() -> bool:
        connector.last_action_items = last_action_items
        return True

    connector.test_connection_and_access = test_connection_and_access
    container = MagicMock()
    container.connectors_map = {}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(
        return_value={"scope": "team", "createdBy": "user-1", "orgId": ORG_ID, "name": "Wiki"}
    )
    registry = MagicMock()
    registry.update_connector_instance = AsyncMock()
    with patch.object(router_module, "resolve_config_service", return_value=AsyncMock()), \
         patch.object(router_module, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(router_module, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(router_module.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)):
        await router_module._build_and_store_connector(
            container, CONNECTOR_ID, "DRUPAL WIKI", registry, graph_provider, "user-1", ORG_ID,
            is_admin=True, logger=logging.getLogger("test_router_connection_test_action_items"),
        )
    return registry.update_connector_instance


@pytest.mark.asyncio
async def test_an_item_the_test_found_is_stored_with_the_sign_in() -> None:
    item = ActionItem(code="drupal_wiki_graphql_refused", title="t", message="m")

    update = await _turn_on([item])

    updates = update.await_args.kwargs["updates"]
    assert updates["isAuthenticated"] is True
    assert json.loads(updates[ConnectorStateKeys.ACTION_ITEMS])[0]["code"] == "drupal_wiki_graphql_refused"


@pytest.mark.asyncio
async def test_nothing_found_clears_the_card() -> None:
    update = await _turn_on([])

    assert update.await_args.kwargs["updates"][ConnectorStateKeys.ACTION_ITEMS] is None


@pytest.mark.asyncio
async def test_a_test_that_found_nothing_either_way_leaves_the_card_as_it_is() -> None:
    update = await _turn_on(None)

    assert update.await_args.kwargs["updates"] == {"isAuthenticated": True}


def test_an_item_without_params_is_stored_with_exactly_the_keys_it_had() -> None:
    # Items of other connectors must not gain a key they never set.
    plain = ActionItem(code="certificate_sign_in", title="t", message="m", docs_url="https://example.com")

    assert json.loads(action_items_json([plain]))[0] == {
        "code": "certificate_sign_in", "severity": "warning", "title": "t", "message": "m",
        "action": None, "docsUrl": "https://example.com",
    }


def test_an_item_with_params_keeps_them() -> None:
    item = ActionItem(code="c", title="t", message="m", params={"days": "30"})

    assert json.loads(action_items_json([item]))[0]["params"] == {"days": "30"}
