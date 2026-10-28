"""POST /api/v1/connectors/registry/{connector_type}/test-connection and GET .../network/egress-ips."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.redaction import REDACTED_PLACEHOLDER
from app.connectors.api.router import (
    get_connector_egress_ips,
    get_connector_schema,
    check_connector_connection as run_test_connection,
)
from app.connectors.core.base.connector.connector_service import ConnectionCheckResult

_ROUTER = "app.connectors.api.router"


class _CheckingConnector:
    check_connection = AsyncMock()


def _make_request(body, *, saved_config=None, metadata=None, role="member"):
    user = {"userId": "u1", "orgId": "o1", "role": role}
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


_PASSWORD_SCHEMA = {
    "config": {"auth": {"schemas": {"BASIC": {"fields": [{"name": "password", "fieldType": "PASSWORD"}]}}}}
}


async def test_editing_keeps_a_saved_secret_sent_back_as_the_mask(checking_connector) -> None:
    saved = {"auth": {"host": "db.example.com", "password": "saved-pw"}}
    request, config_service = _make_request(
        {"auth": {"host": "db.example.com", "password": REDACTED_PLACEHOLDER}, "connectorId": "c1"},
        saved_config=saved,
        metadata=_PASSWORD_SCHEMA,
    )
    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "PostgreSQL"})),
        patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
    ):
        await run_test_connection("PostgreSQL", request)

    auth, _logger = checking_connector.check_connection.await_args.args
    assert auth == {"host": "db.example.com", "password": "saved-pw"}


async def test_a_new_secret_replaces_the_saved_one(checking_connector) -> None:
    saved = {"auth": {"host": "db.example.com", "password": "saved-pw"}}
    request, config_service = _make_request(
        {"auth": {"password": "new-pw"}, "connectorId": "c1"}, saved_config=saved, metadata=_PASSWORD_SCHEMA
    )
    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "PostgreSQL"})),
        patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
    ):
        await run_test_connection("PostgreSQL", request)

    auth, _logger = checking_connector.check_connection.await_args.args
    assert auth == {"host": "db.example.com", "password": "new-pw"}


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


def _public_only_of_the_check(checking_connector) -> bool:
    return checking_connector.check_connection.await_args.kwargs["public_only"]


class TestTheAddressRuleOfTheCheck:
    """A member sets up personal connectors, an admin team ones; each gets that scope's address rule."""

    async def test_a_member_gets_the_public_only_rule(self, checking_connector) -> None:
        request, _ = _make_request({"auth": {"host": "10.0.0.7"}})
        await run_test_connection("PostgreSQL", request)
        assert _public_only_of_the_check(checking_connector) is True

    async def test_an_admin_follows_the_deployment_setting(self, checking_connector) -> None:
        request, _ = _make_request({"auth": {"host": "10.0.0.7"}}, role="admin")
        await run_test_connection("PostgreSQL", request)
        assert _public_only_of_the_check(checking_connector) is False

    async def test_a_member_cannot_check_a_type_that_has_no_personal_scope(self, checking_connector) -> None:
        request, _ = _make_request({"auth": {"host": "x"}}, metadata={"config": {}, "scope": ["team"]})
        with pytest.raises(HTTPException) as caught:
            await run_test_connection("PostgreSQL", request)
        assert caught.value.status_code == 403
        checking_connector.check_connection.assert_not_awaited()

    async def test_an_admin_can_check_a_team_only_type(self, checking_connector) -> None:
        request, _ = _make_request(
            {"auth": {"host": "x"}}, metadata={"config": {}, "scope": ["team"]}, role="admin"
        )
        await run_test_connection("PostgreSQL", request)
        checking_connector.check_connection.assert_awaited_once()

    @pytest.mark.parametrize(
        ("role", "instance_scope", "public_only"),
        [
            ("member", "personal", True),
            ("admin", "personal", True),  # an admin editing a personal connector keeps its scope
            ("admin", "team", False),
        ],
    )
    async def test_editing_keeps_the_scope_of_the_instance(
        self, checking_connector, role, instance_scope, public_only
    ) -> None:
        request, config_service = _make_request(
            {"auth": {"host": "10.0.0.7"}, "connectorId": "c1"}, saved_config={"auth": {}}, role=role
        )
        instance = {"type": "PostgreSQL", "scope": instance_scope}
        with (
            patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value=instance)),
            patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
        ):
            await run_test_connection("PostgreSQL", request)
        assert _public_only_of_the_check(checking_connector) is public_only

    async def test_an_instance_without_a_scope_gets_the_stricter_rule(self, checking_connector) -> None:
        request, config_service = _make_request(
            {"auth": {"host": "10.0.0.7"}, "connectorId": "c1"}, saved_config={"auth": {}}, role="admin"
        )
        with (
            patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "PostgreSQL"})),
            patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
        ):
            await run_test_connection("PostgreSQL", request)
        assert _public_only_of_the_check(checking_connector) is True
