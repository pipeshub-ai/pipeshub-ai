"""Connector-owned auth rules and action items, as the router applies them on every save.

OneDrive is the connector with rules today: a new instance signs in with a client
certificate, one already signing in with a client secret keeps it.
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _auth_save_problem,
    _reject_auth_the_connector_refuses,
    _store_config_action_items,
    check_connector_connection,
    create_connector_instance,
)
from app.connectors.core.base.connector.connector_service import ConnectionCheckResult

_ROUTER = "app.connectors.api.router"
SIGN_IN = {"tenantId": "tenant-1", "clientId": "client-1", "hasAdminConsent": True}


def certificate_pair() -> dict[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "router-test")])
    start = datetime.now(timezone.utc) - timedelta(days=1)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(start)
        .not_valid_after(start + timedelta(days=365)).sign(key, hashes.SHA256())
    )
    return {
        "certificate": certificate.public_bytes(serialization.Encoding.PEM).decode(),
        "privateKey": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    }


CERTIFICATE = certificate_pair()


class TestOneDriveSaveRule:
    def test_a_new_connector_with_a_client_secret_is_refused(self) -> None:
        problem = _auth_save_problem("OneDrive", {**SIGN_IN, "clientSecret": "s"}, None)

        assert problem is not None and "client certificate, not a client secret" in problem

    def test_a_new_connector_without_a_certificate_is_refused(self) -> None:
        assert "Upload the client certificate" in _auth_save_problem("OneDrive", SIGN_IN, None)

    def test_a_new_connector_with_a_certificate_is_accepted(self) -> None:
        assert _auth_save_problem("OneDrive", {**SIGN_IN, **CERTIFICATE}, None) is None

    def test_a_new_connector_with_an_unusable_certificate_is_told_why(self) -> None:
        upload = {**CERTIFICATE, "privateKey": certificate_pair()["privateKey"]}

        assert "does not belong to the client certificate" in _auth_save_problem("OneDrive", {**SIGN_IN, **upload}, None)

    def test_an_instance_created_without_auth_gets_the_rule_on_its_first_auth_save(self) -> None:
        assert _auth_save_problem("OneDrive", {**SIGN_IN, "clientSecret": "s"}, {}) is not None

    @pytest.mark.parametrize("change", [{}, {"clientSecret": "rotated"}, CERTIFICATE, {"clientSecret": ""}])
    def test_a_connector_signing_in_with_a_secret_keeps_it(self, change: dict[str, str]) -> None:
        stored = {**SIGN_IN, "clientSecret": "s"}

        assert _auth_save_problem("OneDrive", change, stored) is None

    def test_a_connector_without_rules_accepts_anything(self) -> None:
        assert _auth_save_problem("PostgreSQL", {"password": "pw"}, None) is None

    def test_a_refused_save_is_a_400_with_the_reason(self) -> None:
        with pytest.raises(HTTPException) as refused:
            _reject_auth_the_connector_refuses("OneDrive", {**SIGN_IN, "clientSecret": "s"}, None)

        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "client certificate" in refused.value.detail


def check_request(body: dict, saved_config: dict | None = None) -> tuple[MagicMock, AsyncMock]:
    user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test"))
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value={"config": {}})
    request.app.state.connector_registry = registry
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=saved_config)
    return request, config_service


@pytest.fixture
def checking_connector() -> Iterator[type]:
    class Checking:
        check_connection = AsyncMock(return_value=ConnectionCheckResult(success=True, message="Connected"))

    with (
        patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()),
        patch(f"{_ROUTER}._connection_check_class", return_value=Checking),
    ):
        yield Checking


class TestConnectionCheckApplyTheRule:
    async def test_a_new_connector_is_told_before_any_sign_in(self, checking_connector) -> None:
        request, _ = check_request({"auth": {**SIGN_IN, "clientSecret": "s"}})

        result = await check_connector_connection("OneDrive", request)

        assert result["success"] is False and "client certificate" in result["message"]
        checking_connector.check_connection.assert_not_awaited()

    async def test_a_connector_signing_in_with_a_secret_is_checked_as_before(self, checking_connector) -> None:
        request, config_service = check_request(
            {"auth": {"clientSecret": "rotated"}, "connectorId": "c1"},
            saved_config={"auth": {**SIGN_IN, "clientSecret": "s"}},
        )
        with (
            patch(f"{_ROUTER}.get_validated_connector_instance", AsyncMock(return_value={"type": "OneDrive"})),
            patch(f"{_ROUTER}.resolve_config_service", return_value=config_service),
        ):
            result = await check_connector_connection("OneDrive", request)

        assert result == {"success": True, "message": "Connected"}
        auth, _logger = checking_connector.check_connection.await_args.args
        assert auth == {**SIGN_IN, "clientSecret": "rotated"}


class TestCreateAppliesTheRule:
    async def test_a_new_onedrive_with_a_secret_is_refused_before_it_exists(self) -> None:
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}}}, "scope": ["team"], "supportedAuthTypes": ["OAUTH_ADMIN_CONSENT"],
        })
        registry._normalize_connector_name = MagicMock(return_value="onedrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())
        registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": "c1"})
        graph_provider = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        request, _ = check_request({
            "connectorType": "OneDrive", "instanceName": "OneDrive", "scope": "team",
            "authType": "OAUTH_ADMIN_CONSENT", "config": {"auth": {**SIGN_IN, "clientSecret": "s"}},
        })
        request.app.container = container
        request.app.state.connector_registry = registry

        with patch(f"{_ROUTER}.check_beta_connector_access", AsyncMock()), pytest.raises(HTTPException) as refused:
            await create_connector_instance(request, graph_provider=graph_provider)

        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        registry.create_connector_instance_on_configuration.assert_not_awaited()


class TestActionItemsOnSave:
    def save_request(self) -> MagicMock:
        request = MagicMock()
        request.app.state.graph_provider = AsyncMock()
        return request

    async def test_saving_onedrive_auth_stores_what_the_admin_has_to_do(self) -> None:
        request = self.save_request()

        await _store_config_action_items(request, "OneDrive", "c1", {**SIGN_IN, "clientSecret": "s"}, logging.getLogger("t"))

        connector_id, collection, update = request.app.state.graph_provider.update_node.await_args.args
        assert (connector_id, collection) == ("c1", "apps")
        stored = json.loads(update["actionItems"])
        assert [item["code"] for item in stored] == ["certificate_sign_in"]
        assert stored[0]["docsUrl"].startswith("https://docs.pipeshub.com/"), "camelCase, as the UI reads it"

    async def test_fixing_the_auth_clears_them(self) -> None:
        request = self.save_request()

        await _store_config_action_items(request, "OneDrive", "c1", {**SIGN_IN, **CERTIFICATE}, logging.getLogger("t"))

        assert request.app.state.graph_provider.update_node.await_args.args[2] == {"actionItems": None}

    async def test_a_connector_that_reports_none_is_left_alone(self) -> None:
        request = self.save_request()

        await _store_config_action_items(request, "PostgreSQL", "c1", {"password": "pw"}, logging.getLogger("t"))

        request.app.state.graph_provider.update_node.assert_not_awaited()

    async def test_a_failed_write_does_not_fail_the_save(self) -> None:
        request = self.save_request()
        request.app.state.graph_provider.update_node.side_effect = RuntimeError("graph unavailable")

        await _store_config_action_items(request, "OneDrive", "c1", {**SIGN_IN, "clientSecret": "s"}, logging.getLogger("t"))
