"""What a connector save may store about its shared OAuth app.

A connector linked to a shared OAuth app borrows that app's client secret, so the fields
that decide where the secret is sent (the app's owning org, ``authorizeUrl``, ``tokenUrl``,
``instanceUrl``) are taken from the app by the server on every save path. These tests send
those fields from the client and check what reaches the config store.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _build_oauth_flow_config,
    _prepare_connector_config,
    get_connector_instance,
    update_connector_instance_auth_config,
    update_connector_instance_config,
)

_ROUTER = "app.connectors.api.router"
ORG, OTHER_ORG = "org-1", "org-other"
APP_ID = "app-1"
REGISTRY_AUTHORIZE = "https://registry.example/authorize"
REGISTRY_TOKEN = "https://registry.example/token"
APP_AUTHORIZE = "https://app.example/authorize"
APP_TOKEN = "https://app.example/token"
APP_INSTANCE = "https://git.company.example"
CLIENT_URLS = {
    "authorizeUrl": "https://elsewhere.example/authorize",
    "tokenUrl": "https://elsewhere.example/token",
    "instanceUrl": "https://elsewhere.example",
}


def _app(*, org_id: str = ORG, urls: bool = True, instance_url: str | None = APP_INSTANCE) -> dict[str, Any]:
    app: dict[str, Any] = {
        "_id": APP_ID,
        "orgId": org_id,
        "oauthInstanceName": "Shared app",
        "config": {"clientId": "app-client", "clientSecret": "app-secret"},
    }
    if urls:
        app["authorizeUrl"] = APP_AUTHORIZE
        app["tokenUrl"] = APP_TOKEN
    if instance_url:
        app["config"]["instanceUrl"] = instance_url
    return app


def _annotate_like_ee(auth: dict[str, Any], shared: dict[str, Any], org_id: str) -> None:
    """The EE rule: name the owning org of an inherited app, else clear it (OSS only clears)."""
    auth.pop("orgId", None)
    if shared.get("orgId") and shared["orgId"] != org_id:
        auth["inheritedFromOrgId"] = shared["orgId"]
    else:
        auth.pop("inheritedFromOrgId", None)


def _metadata() -> dict[str, Any]:
    return {
        "config": {
            "auth": {
                "schemas": {"OAUTH": {"redirectUri": "oauth/callback"}},
                "oauthConfigs": {
                    "OAUTH": {"authorizeUrl": REGISTRY_AUTHORIZE, "tokenUrl": REGISTRY_TOKEN, "scopes": ["read"]}
                },
            }
        }
    }


def _config_service(stored_auth: dict[str, Any] | None = None) -> AsyncMock:
    stored = {"auth": dict(stored_auth or {"connectorScope": "personal"}), "credentials": None}

    async def get_config(path: str, **kwargs: Any) -> Any:
        if path.endswith("/config"):
            return stored
        if path == "/services/endpoints":
            return {"frontend": {"publicEndpoint": "https://app.example.com"}}
        return kwargs.get("default", {})

    service = AsyncMock()
    service.get_config = AsyncMock(side_effect=get_config)
    service.set_config = AsyncMock(return_value=True)
    return service


def _request(body: dict[str, Any], *, role: str = "member") -> MagicMock:
    user = {"userId": "u1", "orgId": ORG, "role": role}
    request = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.headers.get = lambda key, default=None: default
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test"))
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=_metadata())
    registry.update_connector_instance = AsyncMock(return_value={"_key": "conn1"})
    request.app.state.connector_registry = registry
    return request


def _instance(auth_type: str = "OAUTH") -> dict[str, Any]:
    return {
        "_key": "conn1",
        "type": "GITLAB",
        "name": "GitLab",
        "isActive": False,
        "authType": auth_type,
        "scope": "personal",
        "createdBy": "u1",
    }


async def _save_auth(
    auth: dict[str, Any],
    *,
    shared: dict[str, Any] | None,
    stored_auth: dict[str, Any] | None = None,
    auth_type: str = "OAUTH",
) -> tuple[dict[str, Any], AsyncMock, AsyncMock]:
    """PUT /config/auth as a member. Returns (stored auth, config service, app lookup)."""
    service = _config_service(stored_auth)
    lookup = AsyncMock(return_value=shared)
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value={"scope": "personal"})
    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance(auth_type)),
        patch(f"{_ROUTER}.resolve_config_service", return_value=service),
        patch(f"{_ROUTER}.resolve_oauth_config", lookup),
        patch(f"{_ROUTER}.annotate_oauth_inheritance", _annotate_like_ee),
        patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret", "instanceUrl"]),
        patch(f"{_ROUTER}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}),
        patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
        patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1),
    ):
        result = await update_connector_instance_auth_config("conn1", _request({"auth": auth}), graph_provider)
    return result["config"]["auth"], service, lookup


class TestHelpers:
    @pytest.mark.parametrize(
        ("auth", "kept"),
        [
            ({"oauthConfigId": APP_ID, "inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG}, {"oauthConfigId": APP_ID}),
            ({"inheritedFromOrgId": ""}, {}),
            ({"orgId": None, "apiToken": "t"}, {"apiToken": "t"}),
            ({"clientId": "c", "instanceUrl": "https://x.example"}, {"clientId": "c", "instanceUrl": "https://x.example"}),
            ({"connectorType": "Slack", "oauthConfigId": APP_ID}, {"oauthConfigId": APP_ID}),
            ({}, {}),
        ],
    )
    def test_fields_the_server_sets_are_never_taken_from_the_client(self, auth: dict, kept: dict) -> None:
        from app.connectors.api.router import _without_server_set_auth_fields

        before = dict(auth)
        assert _without_server_set_auth_fields(auth) == kept
        assert auth == before

    @pytest.mark.parametrize(
        ("auth", "shared", "expected"),
        [
            ({}, _app(), APP_INSTANCE),
            ({"instanceUrl": "https://elsewhere.example"}, _app(), APP_INSTANCE),
            ({"instanceUrl": "https://own.example"}, _app(instance_url=None), None),
            ({"instanceUrl": "https://own.example"}, _app(instance_url=""), None),
            ({"instanceUrl": "https://own.example"}, {"_id": APP_ID}, None),
            ({"instanceUrl": "https://own.example"}, {"_id": APP_ID, "config": None}, None),
            ({}, _app(instance_url=None), None),
        ],
        ids=["fills-in", "app-wins", "app-has-none", "app-has-empty", "app-has-no-config", "app-config-is-null", "nothing-anywhere"],
    )
    def test_a_linked_connector_carries_its_apps_instance_url_and_no_other(
        self, auth: dict, shared: dict, expected: str | None
    ) -> None:
        from app.connectors.api.router import _mirror_shared_instance_url

        _mirror_shared_instance_url(auth, shared)
        assert auth.get("instanceUrl") == expected


class TestSaveAuthOfALinkedConnector:
    async def test_the_apps_endpoints_replace_whatever_the_client_sent(self) -> None:
        auth, service, _ = await _save_auth(
            {"oauthConfigId": APP_ID, "inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG, **CLIENT_URLS},
            shared=_app(),
        )
        assert auth["authorizeUrl"] == APP_AUTHORIZE
        assert auth["tokenUrl"] == APP_TOKEN
        assert auth["instanceUrl"] == APP_INSTANCE
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth
        assert auth["oauthConfigId"] == APP_ID
        assert service.set_config.await_args.args[1]["auth"] == auth

    async def test_an_app_without_urls_falls_back_to_the_registry_not_the_client(self) -> None:
        auth, _, _ = await _save_auth({"oauthConfigId": APP_ID, **CLIENT_URLS}, shared=_app(urls=False))
        assert auth["authorizeUrl"] == REGISTRY_AUTHORIZE
        assert auth["tokenUrl"] == REGISTRY_TOKEN

    async def test_an_instance_url_is_not_kept_when_the_app_has_none(self) -> None:
        """Sign-in then goes to the provider's default host, so the connector must not point elsewhere."""
        auth, _, _ = await _save_auth(
            {"oauthConfigId": APP_ID, "instanceUrl": "https://own.example"},
            shared=_app(instance_url=None),
            stored_auth={"connectorScope": "personal", "instanceUrl": "https://stored.example"},
        )
        assert "instanceUrl" not in auth

    @pytest.mark.parametrize("claimed", [None, ORG, "org-unrelated"])
    async def test_an_inherited_apps_org_is_recorded_from_the_app(self, claimed: str | None) -> None:
        sent = {"oauthConfigId": APP_ID}
        if claimed:
            sent["inheritedFromOrgId"] = claimed
        auth, _, _ = await _save_auth(sent, shared=_app(org_id=OTHER_ORG))
        assert auth["inheritedFromOrgId"] == OTHER_ORG

    async def test_an_owning_org_stored_earlier_is_cleared_when_relinked_to_an_own_app(self) -> None:
        auth, _, _ = await _save_auth(
            {"oauthConfigId": APP_ID},
            shared=_app(),
            stored_auth={
                "connectorScope": "personal",
                "oauthConfigId": "old-app",
                "inheritedFromOrgId": OTHER_ORG,
                "orgId": OTHER_ORG,
            },
        )
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth

    async def test_the_open_source_edition_never_keeps_an_owning_org(self) -> None:
        from app.connectors.api.connector_resolvers import annotate_oauth_inheritance

        auth = {"oauthConfigId": APP_ID, "inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG, "instanceUrl": "https://x.example"}
        annotate_oauth_inheritance(auth, _app(org_id=OTHER_ORG), ORG)
        assert auth == {"oauthConfigId": APP_ID, "instanceUrl": "https://x.example"}

    async def test_the_app_is_looked_up_for_the_callers_org(self) -> None:
        _, service, lookup = await _save_auth({"oauthConfigId": APP_ID}, shared=_app())
        args = lookup.await_args.args
        assert args[1:] == ("/services/oauth/gitlab", ORG, APP_ID, service)

    async def test_naming_an_app_that_cannot_be_used_is_refused_and_nothing_is_saved(self) -> None:
        service = _config_service()
        graph_provider = AsyncMock()
        with (
            patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()),
            patch(f"{_ROUTER}.resolve_config_service", return_value=service),
            patch(f"{_ROUTER}.resolve_oauth_config", AsyncMock(return_value=None)),
            patch(f"{_ROUTER}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}),
            patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
            pytest.raises(HTTPException) as refused,
        ):
            await update_connector_instance_auth_config(
                "conn1", _request({"auth": {"oauthConfigId": "someone-elses-app", **CLIENT_URLS}}), graph_provider
            )
        assert refused.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "someone-elses-app" in refused.value.detail
        service.set_config.assert_not_awaited()

    async def test_a_link_saved_earlier_still_decides_the_urls(self) -> None:
        auth, _, lookup = await _save_auth(
            dict(CLIENT_URLS),
            shared=_app(),
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID},
        )
        assert lookup.await_args.args[3] == APP_ID
        assert auth["authorizeUrl"] == APP_AUTHORIZE
        assert auth["tokenUrl"] == APP_TOKEN
        assert auth["instanceUrl"] == APP_INSTANCE

    async def test_an_earlier_link_that_no_longer_resolves_still_keeps_client_urls_out(self) -> None:
        """Not refused (the request did not name the app), but the client's URLs are not stored."""
        auth, service, _ = await _save_auth(
            {"authorizeUrl": CLIENT_URLS["authorizeUrl"], "tokenUrl": CLIENT_URLS["tokenUrl"]},
            shared=None,
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID},
        )
        assert auth["authorizeUrl"] == REGISTRY_AUTHORIZE
        assert auth["tokenUrl"] == REGISTRY_TOKEN
        service.set_config.assert_awaited_once()

    @pytest.mark.parametrize("stored_type", ["GITLAB", "Slack", None], ids=["right", "changed-earlier", "missing"])
    async def test_the_connectors_type_always_comes_from_the_connector_itself(self, stored_type: str | None) -> None:
        """The stored type picks the registry defaults for the app's URLs."""
        stored_auth = {"connectorScope": "personal"}
        if stored_type:
            stored_auth["connectorType"] = stored_type
        auth, _, _ = await _save_auth(
            {"oauthConfigId": APP_ID, "connectorType": "Slack"}, shared=_app(), stored_auth=stored_auth
        )
        assert auth["connectorType"] == "GITLAB"

    async def test_urls_already_stored_on_the_connector_are_overwritten(self) -> None:
        auth, _, _ = await _save_auth(
            {"oauthConfigId": APP_ID},
            shared=_app(),
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID, **CLIENT_URLS},
        )
        assert auth["authorizeUrl"] == APP_AUTHORIZE
        assert auth["tokenUrl"] == APP_TOKEN
        assert auth["instanceUrl"] == APP_INSTANCE


class TestSaveAuthOfOtherConnectors:
    async def test_an_unlinked_oauth_connector_keeps_its_own_urls(self) -> None:
        auth, _, lookup = await _save_auth(
            {"inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG, **CLIENT_URLS}, shared=_app()
        )
        lookup.assert_not_awaited()
        assert auth["authorizeUrl"] == CLIENT_URLS["authorizeUrl"]
        assert auth["tokenUrl"] == CLIENT_URLS["tokenUrl"]
        assert auth["instanceUrl"] == CLIENT_URLS["instanceUrl"]
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth

    async def test_an_unlinked_oauth_connector_without_urls_gets_the_registrys(self) -> None:
        auth, _, _ = await _save_auth({"clientId": "own-client"}, shared=None)
        assert auth["authorizeUrl"] == REGISTRY_AUTHORIZE
        assert auth["tokenUrl"] == REGISTRY_TOKEN

    async def test_a_token_connector_keeps_its_fields_and_never_looks_up_an_app(self) -> None:
        auth, _, lookup = await _save_auth(
            {"apiToken": "t", "baseUrl": "https://jira.example", "oauthConfigId": APP_ID, "orgId": OTHER_ORG,
             "inheritedFromOrgId": OTHER_ORG, "tokenUrl": "https://own.example/token"},
            shared=_app(),
            auth_type="API_TOKEN",
        )
        lookup.assert_not_awaited()
        assert auth["apiToken"] == "t"
        assert auth["baseUrl"] == "https://jira.example"
        assert auth["tokenUrl"] == "https://own.example/token"
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth


async def _prepare(
    auth: dict[str, Any],
    *,
    shared: dict[str, Any] | None,
    auth_type: str = "OAUTH",
    link_at_top_level: bool = True,
) -> dict[str, Any]:
    with (
        patch(f"{_ROUTER}.resolve_oauth_config", AsyncMock(return_value=shared)),
        patch(f"{_ROUTER}.annotate_oauth_inheritance", _annotate_like_ee),
        patch(f"{_ROUTER}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}),
        patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
    ):
        prepared = await _prepare_connector_config(
            config={"auth": auth},
            connector_type="GITLAB",
            scope="personal",
            oauth_config_id=APP_ID if shared and link_at_top_level else None,
            metadata=_metadata(),
            selected_auth_type=auth_type,
            user_id="u1",
            org_id=ORG,
            is_admin=False,
            config_service=_config_service(),
            base_url="https://app.example.com",
            logger=logging.getLogger("test"),
        )
    return prepared["auth"]


class TestCreate:
    async def test_the_apps_endpoints_replace_whatever_the_client_sent(self) -> None:
        auth = await _prepare(
            {"oauthConfigId": APP_ID, "inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG, **CLIENT_URLS},
            shared=_app(),
        )
        assert auth["authorizeUrl"] == APP_AUTHORIZE
        assert auth["tokenUrl"] == APP_TOKEN
        assert auth["instanceUrl"] == APP_INSTANCE
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth

    async def test_an_inherited_apps_org_is_recorded_from_the_app(self) -> None:
        auth = await _prepare({"oauthConfigId": APP_ID, "inheritedFromOrgId": "org-unrelated"}, shared=_app(org_id=OTHER_ORG))
        assert auth["inheritedFromOrgId"] == OTHER_ORG

    async def test_a_link_given_only_inside_auth_is_treated_the_same(self) -> None:
        """The connector form sends ``oauthConfigId`` inside ``auth`` and nothing at the top level."""
        auth = await _prepare(
            {"oauthConfigId": APP_ID, "inheritedFromOrgId": "org-unrelated", **CLIENT_URLS},
            shared=_app(org_id=OTHER_ORG),
            link_at_top_level=False,
        )
        assert auth["authorizeUrl"] == APP_AUTHORIZE
        assert auth["tokenUrl"] == APP_TOKEN
        assert auth["instanceUrl"] == APP_INSTANCE
        assert auth["inheritedFromOrgId"] == OTHER_ORG

    async def test_a_link_inside_auth_that_cannot_be_used_is_refused(self) -> None:
        with (
            patch(f"{_ROUTER}.resolve_oauth_config", AsyncMock(return_value=None)),
            patch(f"{_ROUTER}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}),
            patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
            pytest.raises(HTTPException) as refused,
        ):
            await _prepare_connector_config(
                config={"auth": {"oauthConfigId": "someone-elses-app"}},
                connector_type="GITLAB",
                scope="personal",
                oauth_config_id=None,
                metadata=_metadata(),
                selected_auth_type="OAUTH",
                user_id="u1",
                org_id=ORG,
                is_admin=False,
                config_service=_config_service(),
                base_url="https://app.example.com",
                logger=logging.getLogger("test"),
            )
        assert refused.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_a_token_connector_never_looks_up_an_app(self) -> None:
        lookup = AsyncMock(return_value=_app())
        with patch(f"{_ROUTER}.resolve_oauth_config", lookup):
            prepared = await _prepare_connector_config(
                config={"auth": {"apiToken": "t", "oauthConfigId": APP_ID, "instanceUrl": "https://own.example"}},
                connector_type="GITLAB",
                scope="personal",
                oauth_config_id=None,
                metadata=_metadata(),
                selected_auth_type="API_TOKEN",
                user_id="u1",
                org_id=ORG,
                is_admin=False,
                config_service=_config_service(),
                base_url="https://app.example.com",
                logger=logging.getLogger("test"),
            )
        lookup.assert_not_awaited()
        assert prepared["auth"]["instanceUrl"] == "https://own.example"

    async def test_an_instance_url_is_not_kept_when_the_app_has_none(self) -> None:
        auth = await _prepare({"oauthConfigId": APP_ID, "instanceUrl": "https://own.example"}, shared=_app(instance_url=None))
        assert "instanceUrl" not in auth

    @pytest.mark.parametrize("auth_type", ["OAUTH", "API_TOKEN"])
    async def test_a_claimed_owning_org_is_dropped_without_a_link_too(self, auth_type: str) -> None:
        auth = await _prepare({"apiToken": "t", "inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG}, shared=None, auth_type=auth_type)
        assert "inheritedFromOrgId" not in auth
        assert "orgId" not in auth
        assert auth["apiToken"] == "t"


async def _save_config(
    body: dict[str, Any],
    *,
    shared: dict[str, Any] | None,
    stored_auth: dict[str, Any] | None = None,
    auth_type: str = "OAUTH",
) -> tuple[dict[str, Any], AsyncMock, AsyncMock]:
    """PUT /config as a member. Returns (stored auth, config service, app lookup)."""
    service = _config_service(stored_auth)
    lookup = AsyncMock(return_value=shared)
    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance(auth_type)),
        patch(f"{_ROUTER}.resolve_config_service", return_value=service),
        patch(f"{_ROUTER}.resolve_oauth_config", lookup),
        patch(f"{_ROUTER}.annotate_oauth_inheritance", _annotate_like_ee),
        patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
        patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1),
    ):
        await update_connector_instance_config("conn1", _request(body))
    return service.set_config.await_args.args[1]["auth"], service, lookup


class TestSaveFullConfig:
    @pytest.mark.parametrize("auth_type", ["OAUTH", "API_TOKEN"])
    async def test_a_claimed_owning_org_is_not_stored(self, auth_type: str) -> None:
        stored, _, _ = await _save_config(
            {"auth": {"inheritedFromOrgId": OTHER_ORG, "orgId": OTHER_ORG, "includeJiraScope": True}},
            shared=_app(),
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID},
            auth_type=auth_type,
        )
        assert "inheritedFromOrgId" not in stored
        assert "orgId" not in stored
        assert stored["includeJiraScope"] is True
        assert stored["oauthConfigId"] == APP_ID

    @pytest.mark.parametrize(
        "body",
        [
            {"auth": {**CLIENT_URLS}},
            {"auth": {"oauthConfigId": APP_ID, **CLIENT_URLS}},
            {"oauthConfigId": APP_ID, "auth": {**CLIENT_URLS}},
        ],
        ids=["link-saved-earlier", "link-inside-auth", "link-at-top-level"],
    )
    async def test_a_linked_connector_takes_its_apps_endpoints_however_the_link_is_given(self, body: dict) -> None:
        stored, _, lookup = await _save_config(
            body, shared=_app(org_id=OTHER_ORG), stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID}
        )
        assert lookup.await_args.args[1:4] == ("/services/oauth/gitlab", ORG, APP_ID)
        assert stored["authorizeUrl"] == APP_AUTHORIZE
        assert stored["tokenUrl"] == APP_TOKEN
        assert stored["instanceUrl"] == APP_INSTANCE
        assert stored["inheritedFromOrgId"] == OTHER_ORG

    @pytest.mark.parametrize(
        "body",
        [{"auth": {"oauthConfigId": "someone-elses-app"}}, {"oauthConfigId": "someone-elses-app", "auth": {}}],
        ids=["inside-auth", "top-level"],
    )
    async def test_naming_an_app_that_cannot_be_used_is_refused_and_nothing_is_saved(self, body: dict) -> None:
        service = _config_service({"connectorScope": "personal", "oauthConfigId": APP_ID})
        with (
            patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()),
            patch(f"{_ROUTER}.resolve_config_service", return_value=service),
            patch(f"{_ROUTER}.resolve_oauth_config", AsyncMock(return_value=None)),
            patch(f"{_ROUTER}._get_oauth_config_path", return_value="/services/oauth/gitlab"),
            pytest.raises(HTTPException) as refused,
        ):
            await update_connector_instance_config("conn1", _request(body))
        assert refused.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert "someone-elses-app" in refused.value.detail
        service.set_config.assert_not_awaited()

    async def test_an_earlier_link_that_no_longer_resolves_does_not_block_the_save(self) -> None:
        stored, service, _ = await _save_config(
            {"auth": {"includeJiraScope": True, "tokenUrl": CLIENT_URLS["tokenUrl"]}},
            shared=None,
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID},
        )
        assert stored["tokenUrl"] == REGISTRY_TOKEN
        assert stored["includeJiraScope"] is True
        service.set_config.assert_awaited_once()

    @pytest.mark.parametrize("auth_type", ["OAUTH", "API_TOKEN"])
    async def test_the_connectors_type_always_comes_from_the_connector_itself(self, auth_type: str) -> None:
        stored, _, _ = await _save_config(
            {"auth": {"connectorType": "Slack"}},
            shared=_app(),
            stored_auth={"connectorScope": "personal", "connectorType": "Slack"},
            auth_type=auth_type,
        )
        assert stored["connectorType"] == "GITLAB"

    async def test_a_save_without_auth_never_looks_up_the_app(self) -> None:
        _, _, lookup = await _save_config(
            {"sync": {"strategy": "MANUAL"}},
            shared=_app(),
            stored_auth={"connectorScope": "personal", "oauthConfigId": APP_ID},
        )
        lookup.assert_not_awaited()

    async def test_a_token_connector_never_looks_up_an_app(self) -> None:
        stored, _, lookup = await _save_config(
            {"auth": {"apiToken": "t", "instanceUrl": "https://own.example", "oauthConfigId": APP_ID}},
            shared=_app(),
            auth_type="API_TOKEN",
        )
        lookup.assert_not_awaited()
        assert stored["instanceUrl"] == "https://own.example"


async def _flow(shared: dict[str, Any], registry_type: str = "") -> dict[str, Any]:
    with patch(f"{_ROUTER}.resolve_shared_oauth_config_for_flow", AsyncMock(return_value=shared)):
        return await _build_oauth_flow_config(
            {"oauthConfigId": APP_ID, **CLIENT_URLS},
            "GITLAB",
            ORG,
            AsyncMock(),
            logging.getLogger("test"),
            registry_type=registry_type,
        )


class TestSignInNeedsSomewhereToGo:
    async def test_no_url_on_the_app_or_in_the_registry_is_refused_with_a_reason(self) -> None:
        with pytest.raises(HTTPException) as refused:
            await _flow(_app(urls=False, instance_url=None), registry_type="No Such Connector Type")
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert APP_ID in refused.value.detail

    async def test_the_clients_urls_do_not_fill_the_gap(self) -> None:
        """Before, the connector's own URLs were used when the app and registry had none."""
        with pytest.raises(HTTPException):
            await _flow(_app(urls=False, instance_url=None))

    async def test_an_instance_url_on_the_app_is_enough(self) -> None:
        flow = await _flow(_app(urls=False), registry_type="No Such Connector Type")
        assert flow["instanceUrl"] == APP_INSTANCE

    async def test_urls_in_the_apps_config_section_are_enough(self) -> None:
        shared = _app(urls=False, instance_url=None)
        shared["config"].update({"authorizeUrl": APP_AUTHORIZE, "tokenUrl": APP_TOKEN})
        flow = await _flow(shared, registry_type="No Such Connector Type")
        assert (flow["authorizeUrl"], flow["tokenUrl"]) == (APP_AUTHORIZE, APP_TOKEN)


class TestReadingAConnectorLeavesRegistryDefaultsAlone:
    async def test_an_instances_urls_are_not_written_into_the_registrys_metadata(self) -> None:
        registry_metadata = {
            "type": "GITLAB",
            "authType": "OAUTH",
            "config": {"auth": {"oauthConfigs": {"OAUTH": {"authorizeUrl": REGISTRY_AUTHORIZE, "tokenUrl": REGISTRY_TOKEN}}}},
        }
        request = _request({})
        # The registry returns a new top-level dict per call; what it shares is the nested metadata.
        request.app.state.connector_registry.get_connector_instance = AsyncMock(side_effect=lambda **_: dict(registry_metadata))
        service = _config_service({"authorizeUrl": CLIENT_URLS["authorizeUrl"], "tokenUrl": CLIENT_URLS["tokenUrl"]})
        with (
            patch(f"{_ROUTER}.resolve_config_service", return_value=service),
            patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock),
        ):
            result = await get_connector_instance("conn1", request)

        shown = result["connector"]["config"]["auth"]["oauthConfigs"]["OAUTH"]
        assert shown["tokenUrl"] == CLIENT_URLS["tokenUrl"]
        kept = registry_metadata["config"]["auth"]
        assert kept["oauthConfigs"]["OAUTH"] == {"authorizeUrl": REGISTRY_AUTHORIZE, "tokenUrl": REGISTRY_TOKEN}
        assert "tokenUrl" not in kept
