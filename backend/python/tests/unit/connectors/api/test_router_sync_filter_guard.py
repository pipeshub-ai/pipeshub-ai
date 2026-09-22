"""The enable-toggle guard for required sync filters (e.g. "exactly one repository").

Registry lookup is exact-match on the registered connector name, so these tests give
``get_connector_metadata`` the same exact-match behaviour as the real registry. That is
the point of the suite: a handler that hands the lookup a case-folded connector type
gets ``None`` back and skips validation silently, letting a connector enable with no
repository selected and fail much later inside the background sync task.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_BETA_PATCH = "app.connectors.api.router.check_beta_connector_access"
_TIMESTAMP_PATCH = "app.connectors.api.router.get_epoch_timestamp_in_ms"
_INIT_PATCH = "app.connectors.api.router._ensure_connector_initialized"

REGISTERED_TYPE = "GitHub Teams"

METADATA = {
    "config": {
        "filters": {
            "sync": {
                "schema": {
                    "fields": [
                        {
                            "name": "repo_ids",
                            "displayName": "Repository",
                            "filterType": "select",
                            "category": "sync",
                            "required": True,
                        },
                        {
                            "name": "org_ids",
                            "displayName": "Github Organizations",
                            "filterType": "multiselect",
                            "category": "sync",
                        },
                    ]
                }
            }
        }
    }
}


def _registry() -> AsyncMock:
    registry = AsyncMock()

    async def get_metadata(connector_type: str, instance_data: Any = None) -> dict | None:
        return METADATA if connector_type == REGISTERED_TYPE else None

    registry.get_connector_metadata = AsyncMock(side_effect=get_metadata)
    return registry


def _request(config: dict[str, Any]) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    req.headers = {}
    req.json = AsyncMock(return_value={"type": "sync"})

    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=config)
    container.config_service.return_value = config_service
    container.messaging_producer = AsyncMock()

    req.app.container = container
    req.app.state.connector_registry = _registry()
    req.app.state.graph_provider = AsyncMock()
    return req


def _instance(**overrides: Any) -> dict[str, Any]:
    inst = {
        "_key": "c1",
        "type": REGISTERED_TYPE,
        "scope": "team",
        "createdBy": "u1",
        "authType": "OAUTH",
        "isActive": False,
        "isConfigured": True,
        "isAuthenticated": True,
        "name": "My connector",
        "appGroup": "Github",
        "appGroupId": "ag1",
    }
    inst.update(overrides)
    return inst


REPO_SELECTED = {
    "credentials": {"access_token": "tok"},
    "filters": {"sync": {"values": {"repo_ids": {"operator": "in", "value": ["o/r"]}}}},
}
NO_REPO = {"credentials": {"access_token": "tok"}}


async def _toggle(request: MagicMock, instance: dict[str, Any]) -> Any:
    from app.connectors.api.router import toggle_connector_instance

    request.app.state.connector_registry.get_connector_instance = AsyncMock(
        return_value=instance
    )
    request.app.state.connector_registry.update_connector_instance = AsyncMock(
        return_value=True
    )
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(
        return_value={"_key": "o1", "accountType": "free"}
    )
    with patch(_BETA_PATCH, new_callable=AsyncMock), \
         patch(_TIMESTAMP_PATCH, return_value=1000), \
         patch(_INIT_PATCH, new_callable=AsyncMock, return_value=MagicMock()):
        return await toggle_connector_instance("c1", request, graph_provider=graph_provider)


class TestEnableTogglePassesTheRegistryLookupableType:
    """The regression: the handler must not case-fold the type before the lookup."""

    async def test_enabling_without_a_repository_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _toggle(_request(NO_REPO), _instance())
        assert exc.value.status_code == 400
        assert "repository" in str(exc.value.detail).lower()

    async def test_the_lookup_receives_the_type_verbatim(self) -> None:
        """Upper- or lower-casing it resolves no metadata, and the guard then does nothing."""
        request = _request(NO_REPO)
        with pytest.raises(HTTPException):
            await _toggle(request, _instance())

        looked_up = [
            call.args[0]
            for call in request.app.state.connector_registry.get_connector_metadata.call_args_list
        ]
        assert REGISTERED_TYPE in looked_up, (
            f"guard queried the registry with {looked_up!r}; only the registered name "
            f"{REGISTERED_TYPE!r} resolves, anything else silently skips validation"
        )

    async def test_enabling_with_one_repository_succeeds(self) -> None:
        result = await _toggle(_request(REPO_SELECTED), _instance())
        assert result["success"] is True

    async def test_disabling_is_never_blocked(self) -> None:
        """A connector already enabled with a bad config must still be switchable off."""
        result = await _toggle(_request(NO_REPO), _instance(isActive=True))
        assert result["success"] is True

    async def test_connector_without_required_sync_filters_is_untouched(self) -> None:
        request = _request(NO_REPO)
        result = await _toggle(request, _instance(type="SLACK"))
        assert result["success"] is True
