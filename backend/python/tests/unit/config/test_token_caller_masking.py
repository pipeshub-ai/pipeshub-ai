"""Stored OAuth client secrets and toolset credentials read with a token (a PAT or an
OAuth app) follow TOKEN_RESTRICTIONS_MODE, as on the Node config routes: with
`enforce` a token caller gets the mask whatever HIDE_SECRET_CONFIG says; otherwise
it keeps today's response and the read is logged."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

# The routes module only loads cleanly once app.edition_config is imported first.
import app.edition_config  # noqa: F401
from app.api.routes.toolsets import _instance_for_response, list_toolset_oauth_configs
from app.config.redaction import REDACTED_PLACEHOLDER, hide_secrets_for
from app.connectors.api.router import get_oauth_config_by_id, list_oauth_configs
from app.core.org_context import OrgContext, bind

SECRET = "s3cret-value"
STORED = {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "App", "config": {"clientId": "cid", "clientSecret": SECRET}}


def _request(*, token: bool, admin: bool = True) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin" if admin else "member"}
    if token:
        request.state.user["isOAuth"] = True
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    request.scope["app"] = MagicMock(container=container)
    return request


@pytest.fixture(autouse=True)
def _fresh_observation_window(monkeypatch) -> None:
    monkeypatch.setattr("app.config.redaction._observed", {})


@pytest.fixture
def shown(monkeypatch) -> None:
    monkeypatch.setenv("HIDE_SECRET_CONFIG", "false")
    monkeypatch.delenv("TOKEN_RESTRICTIONS_MODE", raising=False)


@pytest.fixture
def enforce(shown, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_RESTRICTIONS_MODE", "enforce")


class TestHideSecretsFor:
    def test_hidden_stays_hidden(self, shown) -> None:
        assert hide_secrets_for(_request(token=False), hidden=True) is True

    def test_session_caller_follows_the_flag_in_both_modes(self, enforce) -> None:
        assert hide_secrets_for(_request(token=False), hidden=False) is False

    def test_token_caller_is_masked_in_enforce(self, enforce) -> None:
        assert hide_secrets_for(_request(token=True), hidden=False) is True

    def test_token_caller_is_logged_not_masked_by_default(self, shown, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert hide_secrets_for(_request(token=True), hidden=False) is False
        assert "token_restriction_observed" in caplog.text

    def test_the_log_names_the_bound_org_not_the_request_user(self, shown, caplog) -> None:
        request = _request(token=True)
        request.state.user["orgId"] = "claimed-org"
        with bind(OrgContext("bound-org", "u1", "user", "jwt", "req-test")), caplog.at_level(logging.WARNING):
            assert hide_secrets_for(request, hidden=False) is False
        assert "org=bound-org" in caplog.text
        assert "claimed-org" not in caplog.text

    def test_the_log_names_the_route_template_never_the_raw_path(self, shown, caplog) -> None:
        request = _request(token=True)
        request.scope["path"] = "/api/v1/connectors/secret-instance-id/config"
        with caplog.at_level(logging.WARNING):
            hide_secrets_for(request, hidden=False)
        assert "secret-instance-id" not in caplog.text
        assert "route=GET unmatched" in caplog.text

    @pytest.mark.parametrize("mode", ["", "log", "ENFORCE", "true"])
    def test_only_the_literal_enforce_masks(self, shown, monkeypatch, mode) -> None:
        monkeypatch.setenv("TOKEN_RESTRICTIONS_MODE", mode)
        assert hide_secrets_for(_request(token=True), hidden=False) is False

    def test_a_new_entry_drops_entries_whose_window_ended(self, shown, monkeypatch) -> None:
        from app.config import redaction

        clock = [1000.0]
        monkeypatch.setattr(redaction.time, "monotonic", lambda: clock[0])
        with bind(OrgContext("org-a", "u1", "user", "jwt", "req-a")):
            hide_secrets_for(_request(token=True), hidden=False)
        assert len(redaction._observed) == 1
        clock[0] += redaction._OBSERVATION_WINDOW_SECONDS
        with bind(OrgContext("org-b", "u1", "user", "jwt", "req-b")):
            hide_secrets_for(_request(token=True), hidden=False)
        assert [key[0] for key in redaction._observed] == ["org-b"]

    def test_no_request_keeps_the_flag(self, enforce) -> None:
        assert hide_secrets_for(None, hidden=False) is False


async def _get_by_id(request: Request) -> dict[str, Any]:
    with patch("app.connectors.api.router.resolve_oauth_config", AsyncMock(return_value=dict(STORED))):
        result = await get_oauth_config_by_id("GMAIL", "cfg1", request, config_service=AsyncMock())
    return result["oauthConfig"]["config"]


async def _list(request: Request) -> dict[str, Any]:
    registry = MagicMock()
    registry.get_oauth_configs_for_connector = AsyncMock(
        return_value={"oauthConfigs": [dict(STORED)], "pagination": {}}
    )
    with patch("app.connectors.api.router.resolve_oauth_configs", AsyncMock(return_value=[dict(STORED)])), \
         patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry", return_value=registry):
        result = await list_oauth_configs(
            "GMAIL", request, page=1, limit=20, search=None, scope=None, config_service=AsyncMock()
        )
    return result["oauthConfigs"][0]["config"]


async def _toolset_list(request: Request) -> dict[str, Any]:
    with patch("app.api.routes.toolsets._check_user_is_admin", AsyncMock(return_value=True)), \
         patch("app.api.routes.toolsets._get_oauth_config_resolver", return_value=None), \
         patch("app.api.routes.toolsets._get_oauth_configs_for_type", AsyncMock(return_value=[dict(STORED)])):
        result = await list_toolset_oauth_configs("jira", request, config_service=AsyncMock())
    return result["oauthConfigs"][0]


ROUTES = {
    "GET /api/v1/oauth/{type}/{id}": _get_by_id,
    "GET /api/v1/oauth/{type}": _list,
    "GET /api/v1/toolsets/oauth-configs/{type}": _toolset_list,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(ROUTES))
class TestOAuthConfigReads:
    async def test_token_caller_gets_the_mask_in_enforce(self, enforce, route) -> None:
        cfg = await ROUTES[route](_request(token=True))
        assert cfg["clientSecret"] == REDACTED_PLACEHOLDER
        assert cfg["clientId"] == "cid"

    async def test_session_admin_response_is_unchanged_in_enforce(self, enforce, route) -> None:
        # Whatever this edition returns to a signed-in admin, enforce does not change it.
        with patch.dict("os.environ", {"TOKEN_RESTRICTIONS_MODE": "log"}):
            before = await ROUTES[route](_request(token=False))
        assert await ROUTES[route](_request(token=False)) == before

    async def test_token_caller_response_is_unchanged_by_default(self, shown, route) -> None:
        session = await ROUTES[route](_request(token=False))
        assert await ROUTES[route](_request(token=True)) == session


class TestToolsetInstanceAuth:
    INSTANCE = {"_id": "i1", "auth": {"clientId": "cid", "clientSecret": SECRET, "apiToken": "tok"}}
    FIELDS = frozenset({"clientSecret", "apiToken"})

    def test_token_caller_gets_the_mask_in_enforce(self, enforce) -> None:
        out = _instance_for_response(
            self.INSTANCE, is_admin=True, secret_fields=self.FIELDS, request=_request(token=True)
        )
        assert out["auth"]["clientSecret"] == REDACTED_PLACEHOLDER
        assert out["auth"]["apiToken"] == REDACTED_PLACEHOLDER

    def test_token_caller_response_is_unchanged_by_default(self, shown) -> None:
        session = _instance_for_response(
            self.INSTANCE, is_admin=True, secret_fields=self.FIELDS, request=_request(token=False)
        )
        token = _instance_for_response(
            self.INSTANCE, is_admin=True, secret_fields=self.FIELDS, request=_request(token=True)
        )
        assert token == session
