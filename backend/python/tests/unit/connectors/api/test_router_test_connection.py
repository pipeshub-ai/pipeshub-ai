"""POST /api/v1/connectors/registry/{connector_type}/test-connection and GET .../network/egress-ips."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (
    get_connector_egress_ips,
    get_connector_schema,
    check_connector_connection as run_test_connection,
)
from app.connectors.core.base.connector.connector_service import ConnectionCheckResult

_ROUTER = "app.connectors.api.router"


class _CheckingConnector:
    check_connection = AsyncMock()


def _make_request(body, *, saved_config=None, metadata=None):
    user = {"userId": "u1", "orgId": "o1", "role": "member"}
    request = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test"))
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(
        return_value=metadata if metadata is not None else {"config": {}}
    )
    request.app.state.connector_registry = registry
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=saved_config)
    return request, config_service


@pytest.fixture
def checking_connector():
    _CheckingConnector.check_connection = AsyncMock(
        return_value=ConnectionCheckResult(success=True, message="Connected")
    )
    with (
        patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()),
        patch(f"{_ROUTER}._connection_check_class", return_value=_CheckingConnector),
    ):
        yield _CheckingConnector


async def test_new_instance_checks_the_trimmed_form_values(checking_connector):
    request, _ = _make_request({"auth": {"host": " db.example.com ", "password": "pw", "connectorType": "x"}})

    result = await run_test_connection("PostgreSQL", request)

    assert result == {"success": True, "message": "Connected"}
    auth, _logger = checking_connector.check_connection.await_args.args
    assert auth == {"host": "db.example.com", "password": "pw"}


async def test_failed_check_is_a_200_with_the_message(checking_connector):
    checking_connector.check_connection.return_value = ConnectionCheckResult(
        success=False, message="Timed out"
    )
    request, _ = _make_request({"auth": {"host": "db"}})

    assert await run_test_connection("PostgreSQL", request) == {"success": False, "message": "Timed out"}


async def test_editing_lays_the_form_over_the_saved_settings(checking_connector):
    saved = {"auth": {"host": "old.example.com", "password": "saved-pw", "database": "shop"}}
    request, config_service = _make_request(
        {"auth": {"host": "new.example.com"}, "connectorId": "c1"}, saved_config=saved
    )
    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "PostgreSQL"})) as validate,
        patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
    ):
        await run_test_connection("PostgreSQL", request)

    validate.assert_awaited_once_with("c1", request)
    config_service.get_config.assert_awaited_once_with("/services/connectors/c1/config")
    auth, _logger = checking_connector.check_connection.await_args.args
    assert auth == {"host": "new.example.com", "password": "saved-pw", "database": "shop"}


async def test_editing_refuses_an_instance_of_another_type(checking_connector):
    request, _ = _make_request({"auth": {}, "connectorId": "c1"})
    with patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "Jira"})):
        with pytest.raises(HTTPException) as exc:
            await run_test_connection("PostgreSQL", request)
    assert exc.value.status_code == 400
    checking_connector.check_connection.assert_not_called()


async def test_a_crashing_check_is_reported_as_a_failure(checking_connector):
    checking_connector.check_connection.side_effect = RuntimeError("boom")
    request, _ = _make_request({"auth": {"host": "db"}})

    result = await run_test_connection("PostgreSQL", request)

    assert result["success"] is False
    assert "boom" not in result["message"]


async def test_connector_without_a_check_is_rejected():
    request, _ = _make_request({"auth": {}})
    with (
        patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()),
        patch(f"{_ROUTER}._connection_check_class", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await run_test_connection("Jira", request)
    assert exc.value.status_code == 400


async def test_unknown_connector_type_is_404():
    request, _ = _make_request({"auth": {}})
    request.app.state.connector_registry.get_connector_metadata = AsyncMock(return_value=None)
    with patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await run_test_connection("Nope", request)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("body", [{"auth": "host=db"}, {}, ["auth"]])
async def test_auth_must_be_an_object(checking_connector, body):
    request, _ = _make_request(body)
    with pytest.raises(HTTPException) as exc:
        await run_test_connection("PostgreSQL", request)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(("connector_type", "supported"), [("PostgreSQL", True), ("Jira", False)])
async def test_schema_says_whether_the_check_exists(connector_type, supported):
    request, _ = _make_request({}, metadata={"config": {"auth": {}}})
    with patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()):
        response = await get_connector_schema(connector_type, request)
    assert response["schema"]["supportsConnectionCheck"] is supported


async def test_egress_ips_route():
    with patch(f"{_ROUTER}.get_egress_ips", AsyncMock(return_value=["203.0.113.10"])):
        assert await get_connector_egress_ips() == {"success": True, "egressIps": ["203.0.113.10"]}
