"""Addresses in a person's own OAuth app reach public servers only.

A GitLab Personal app carries the owner's instance URL, and this server sends the client
secret, sign-in codes and tokens there. So it must be https and public when saved, and every
call it leads to (token exchange, refresh, GitLab REST, GraphQL, attachments) is held to
public addresses when it is made. A personal connector keeps the deployment's looser rule only
while it is linked to an org app, whose addresses an administrator chose.
"""

import copy
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router as router_module
from app.connectors.api.router import _build_oauth_flow_config, _plan_oauth_app_write
from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.oauth_service import OAuthProvider
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)

# Importing a connector registers its OAuth fields: client ID, secret and instance URL.
from app.connectors.sources.gitlab_personal import (
    connector as _gitlab_personal,  # noqa: F401
)
from app.sources.client.gitlab.gitlab import GitLabClient
from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource
from app.utils.oauth_config import OAUTH_APP_OWNER_FIELD, get_oauth_config

ORG_ID = "org-1"
ALICE = "user-alice"
GITLAB_APPS = "/services/oauth/gitlabpersonal"


class InMemoryConfigService:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = copy.deepcopy(values or {})

    async def get_config(self, path: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        del use_cache
        return copy.deepcopy(self.values.get(path, default))

    async def set_config(self, path: str, value: object) -> bool:
        self.values[path] = copy.deepcopy(value)
        return True


def _app(app_id: str, owner: str | None) -> dict[str, Any]:
    app = {
        "_id": app_id,
        "oauthInstanceName": app_id,
        "connectorType": "GitLab Personal",
        "orgId": ORG_ID,
        "config": {"clientId": "c", "clientSecret": "s", "instanceUrl": "https://gitlab.example.com"},
    }
    if owner:
        app[OAUTH_APP_OWNER_FIELD] = owner
    return app


async def _plan(instance_url: str, *, scope: str = "personal", admin: bool = False) -> object:
    return await _plan_oauth_app_write(
        "GitLab Personal",
        {"clientId": "c", "clientSecret": "s", "instanceUrl": instance_url},
        "My GitLab",
        ALICE,
        ORG_ID,
        is_admin=admin,
        config_service=InMemoryConfigService(),
        oauth_config_id=None,
        connector_scope=scope,
    )


@pytest.mark.asyncio
class TestSavingAnAddress:
    @pytest.mark.parametrize(
        "instance_url",
        [
            "http://gitlab.example.com",
            "https://127.0.0.1",
            "https://10.0.0.5",
            "https://169.254.169.254",
            "https://[::1]",
        ],
    )
    async def test_a_personal_app_refuses_plain_http_and_non_public_addresses(self, instance_url: str) -> None:
        with pytest.raises(HTTPException) as exc:
            await _plan(instance_url)

        assert exc.value.status_code == 400

    async def test_a_personal_app_accepts_a_public_https_address(self) -> None:
        with patch.object(router_module, "check_url_allowed") as check:
            plan = await _plan("https://gitlab.example.com")

        check.assert_called_once_with("https://gitlab.example.com", allow_private=False)
        assert plan.owner_user_id == ALICE

    async def test_an_org_app_keeps_the_deployment_s_rule(self) -> None:
        with patch.object(router_module, "check_url_allowed") as check:
            plan = await _plan("https://10.0.0.5", scope="team", admin=True)

        check.assert_not_called()
        assert plan.owner_user_id is None


class TestTokenRequests:
    def test_the_flow_config_s_public_only_reaches_the_oauth_config(self) -> None:
        base = {"clientId": "c", "clientSecret": "s", "authorizeUrl": "https://a.example/auth", "tokenUrl": "https://a.example/token"}

        assert get_oauth_config({**base, "publicOnly": True}).public_only is True
        assert get_oauth_config(base).public_only is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("public_only", [True, False])
    async def test_the_token_session_follows_it(self, public_only: bool) -> None:
        config = oauth_service.OAuthConfig("c", "s", "", "https://a.example/auth", "https://a.example/token", public_only=public_only)
        provider = OAuthProvider(config, MagicMock(), "/creds")
        with (
            patch.object(oauth_service, "private_addresses_blocked", return_value=False),
            patch.object(oauth_service, "guarded_session_kwargs", return_value={}) as kwargs,
        ):
            await provider.session
        await provider.close()

        kwargs.assert_called_once_with(allow_private=True, public_https_only=public_only)

    @pytest.mark.parametrize(
        ("scope", "app", "public_only"),
        [
            ("personal", _app("own", ALICE), True),
            ("personal", {}, True),
            ("personal", _app("org", None), False),
            ("team", _app("org", None), False),
        ],
    )
    def test_the_refresh_holds_a_personal_connector_to_public_addresses(
        self, scope: str, app: dict[str, Any], public_only: bool
    ) -> None:
        flow = TokenRefreshService._with_address_rule({}, scope, app)

        assert flow.get("publicOnly", False) is public_only


@pytest.mark.asyncio
class TestSignInFlow:
    @pytest.mark.parametrize(("app_id", "public_only"), [("own", True), ("org", False)])
    async def test_only_an_org_app_lifts_the_public_rule(self, app_id: str, public_only: bool) -> None:
        store = InMemoryConfigService({GITLAB_APPS: [_app("own", ALICE), _app("org", None)]})

        flow = await _build_oauth_flow_config(
            auth_config={"oauthConfigId": app_id, "connectorScope": "personal"},
            connector_type="GitLab Personal",
            org_id=ORG_ID,
            config_service=store,
            logger=logging.getLogger("test"),
            registry_type="GitLab Personal",
            user_id=ALICE,
            connector={"scope": "personal", "createdBy": ALICE},
        )

        assert flow.get("publicOnly", False) is public_only


@pytest.mark.asyncio
class TestGitLabCalls:
    async def _build(self, app_id: str | None) -> GitLabClient:
        auth = {
            "authType": "OAUTH",
            "connectorType": "GitLab Personal",
            "connectorScope": "personal",
            "instanceUrl": "https://gitlab.example.com",
        }
        if app_id:
            auth["oauthConfigId"] = app_id
        store = InMemoryConfigService({
            GITLAB_APPS: [_app("own", ALICE), _app("org", None)],
            "/services/connectors/c1/config": {"auth": auth, "credentials": {"access_token": "t"}},
        })
        return await GitLabClient.build_from_services(logging.getLogger("test"), store, "c1")

    @pytest.mark.parametrize(
        ("app_id", "public_only"),
        # Its own app, no app, or an app that is gone: nothing an admin chose vouches for the host.
        [("own", True), (None, True), ("gone", True), ("org", False)],
    )
    async def test_rest_graphql_and_attachments_follow_the_rule(self, app_id: str | None, public_only: bool) -> None:
        # Private networks allowed by the deployment, so only the public-only rule can refuse them.
        with patch("app.sources.client.http.address_guard.private_addresses_blocked", return_value=False):
            client = await self._build(app_id)
            data_source = GitLabDataSource(client, base_url="https://gitlab.example.com")
            rest_guard = client.get_sdk().session.get_adapter("https://gitlab.example.com")
            http_guard = data_source.http_client._transport

        assert rest_guard._allow_private is not public_only
        assert http_guard._allow_private is not public_only
        await data_source.aclose()
