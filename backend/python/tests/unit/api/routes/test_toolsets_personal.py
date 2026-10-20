"""Personal toolset instances: actions a user adds for themselves, without an administrator.

Any user may create one; it is stored under their own key and only they see it, sign in to it
and use it. Administrators own the org instances and can't see or manage anyone's personal one.
These tests go through the real routes and lookups against one in-memory store.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import QueryParams

from app.agents import toolset_store
from app.api.middlewares.caller_role import CallerRoleStatus
from app.api.routes import toolsets
from app.api.routes.toolset_resolvers import (
    get_oauth_credentials_for_toolset,
    get_toolset_by_id,
)
from app.utils.url_fetcher import FetchError
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG_LIST = "/services/toolset-instances"
USER_PATH = "/services/toolset-user-instances/org-1/{}/{}"
CRED_PATH = "/services/toolsets/{}/{}"
RULES_PATH = "/services/toolset-tool-rules/{}"
OWNER_PATH = "/services/toolset-personal-owners/{}"

_OAUTH_FIELDS = [{"name": "clientId", "usage": "BOTH"}, {"name": "clientSecret", "usage": "BOTH", "isSecret": True}]
_METADATA: dict[str, dict[str, Any]] = {
    "jira": {
        "name": "jira", "display_name": "Jira", "supported_auth_types": ["API_TOKEN", "OAUTH"],
        "tools": [{"name": "search_issues", "description": ""}, {"name": "create_issue", "description": ""}],
        "config": {"auth": {"schemas": {
            "API_TOKEN": {"fields": [
                {"name": "baseUrl", "usage": "CONFIGURE", "fieldType": "URL"},
                {"name": "email", "usage": "AUTHENTICATE"},
                {"name": "apiToken", "usage": "AUTHENTICATE", "isSecret": True},
            ]},
            "OAUTH": {"fields": _OAUTH_FIELDS},
        }}},
    },
    "gmail": {
        "name": "gmail", "display_name": "Gmail", "supported_auth_types": ["OAUTH"],
        "tools": [{"name": "send_email", "description": ""}],
        "config": {"auth": {"schemas": {"OAUTH": {"fields": _OAUTH_FIELDS}}}},
    },
}


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.get_toolset_metadata.side_effect = lambda name, **_kwargs: _METADATA.get(name)
    registry.list_toolsets.return_value = list(_METADATA)
    return registry


def _record(
    instance_id: str, *, owner: str = "admin-1", toolset_type: str = "jira", auth_type: str = "API_TOKEN",
    scope: str | None = None, auth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "_id": instance_id, "instanceName": instance_id, "toolsetType": toolset_type, "authType": auth_type,
        "orgId": "org-1", "createdBy": owner, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }
    if scope:
        record["scope"] = scope
    if auth is not None:
        record["auth"] = auth
    return record


def _store(**extra: Any) -> FakeConfigService:  # noqa: ANN401
    return FakeConfigService({
        ORG_LIST: [_record("org-jira", auth={"baseUrl": "https://acme.atlassian.net"})],
        USER_PATH.format("u-alice", "alice-jira"): _record(
            "alice-jira", owner="u-alice", scope="personal", auth={"baseUrl": "https://alice.atlassian.net"},
        ),
        USER_PATH.format("u-alice", "alice-gmail"): _record(
            "alice-gmail", owner="u-alice", toolset_type="gmail", auth_type="OAUTH", scope="personal",
            auth={"clientId": "alice-client", "clientSecret": "alice-secret"},
        ),
        USER_PATH.format("u-bob", "bob-jira"): _record(
            "bob-jira", owner="u-bob", scope="personal", auth={"baseUrl": "https://bob.atlassian.net"},
        ),
        **extra,
    })


def _as(store: FakeConfigService, user_id: str, body: dict[str, Any] | None = None, *, in_use: list[str] | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": "org-1"}
    request.headers = {}
    request.query_params = QueryParams("")
    request.body = AsyncMock(return_value=json.dumps(body or {}).encode())
    request.app.state.toolset_registry = _registry()
    request.app.state.graph_provider = MagicMock(check_toolset_instance_in_use=AsyncMock(return_value=in_use or []))
    return request


async def _status(coro: Any) -> int:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value.status_code


@pytest.fixture(autouse=True)
def _roles() -> Iterator[None]:
    async def _is_admin(user_id: str, *_args: object, **_kwargs: object) -> bool:
        return user_id == "admin-1"

    with patch.object(toolsets, "_check_user_is_admin", new=_is_admin), \
         patch.object(toolsets, "fetch_caller_role", new=AsyncMock(return_value=MagicMock(status=CallerRoleStatus.VALID))):
        yield


@pytest.fixture(autouse=True)
def _public_unless_internal() -> Iterator[None]:
    """A name under `.internal` stands for a private address; nothing is looked up for real."""

    def _resolve(url: str, **_kwargs: object) -> None:
        if ".internal" in url:
            raise FetchError("private address")

    with patch.object(toolset_store, "resolve_public_http_target", new=_resolve):
        yield


@pytest.fixture(autouse=True)
def _no_refresh_service() -> Iterator[None]:
    with patch(
        "app.connectors.core.base.token_service.startup_service.startup_service.get_toolset_token_refresh_service",
        return_value=None,
    ):
        yield


def _jira(name: str = "My Jira", **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "instanceName": name, "toolsetType": "jira", "authType": "API_TOKEN",
        "authConfig": {"baseUrl": "https://me.atlassian.net"}, **overrides,
    }


class TestAMemberAddsTheirOwn:
    async def test_it_is_kept_under_their_key_and_never_in_the_org_list(self) -> None:
        store = _store()
        result = await toolsets.create_toolset_instance(_as(store, "u-carol", _jira()), config_service=store)

        instance = result["instance"]
        assert (instance["scope"], instance["createdBy"], instance["auth"]) == (
            "personal", "u-carol", {"baseUrl": "https://me.atlassian.net"},
        )
        assert store.writes == [OWNER_PATH.format(instance["_id"]), USER_PATH.format("u-carol", instance["_id"])]
        assert [i["_id"] for i in store.data[ORG_LIST]] == ["org-jira"]

    async def test_an_administrator_can_ask_for_one_too(self) -> None:
        store = _store()
        result = await toolsets.create_toolset_instance(_as(store, "admin-1", _jira(scope="personal")), config_service=store)
        assert store.writes == [OWNER_PATH.format(result["instance"]["_id"]), USER_PATH.format("admin-1", result["instance"]["_id"])]

    async def test_an_administrators_instance_is_otherwise_the_orgs(self) -> None:
        store = _store()
        result = await toolsets.create_toolset_instance(_as(store, "admin-1", _jira()), config_service=store)
        assert result["instance"]["scope"] == "org"
        assert result["instance"]["_id"] in [i["_id"] for i in store.data[ORG_LIST]]

    async def test_a_scope_that_isnt_one_is_refused(self) -> None:
        store = _store()
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", _jira(scope="team")), config_service=store)) == 400

    @pytest.mark.parametrize("setting", ["tokenUrl", "authorizeUrl", "redirectUri", "scopes", "apiToken"])
    async def test_it_stores_only_its_toolsets_setup_fields(self, setting: str) -> None:
        """Never an endpoint, scope or redirect, which would decide where its tokens go, nor the
        sign-in itself, which is the person's credential and kept apart."""
        store = _store()
        body = _jira(authConfig={"baseUrl": "https://me.atlassian.net", setting: "https://attacker.example"})
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)) == 400
        assert store.writes == []

    async def test_an_oauth_one_needs_its_own_app(self) -> None:
        store = _store()
        body = {"instanceName": "Mail", "toolsetType": "gmail", "authType": "OAUTH", "authConfig": {"clientId": "c"}}
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)) == 400

        body["authConfig"] = {"clientId": "c", "clientSecret": "s"}
        result = await toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)
        assert "oauthConfigId" not in result["instance"]
        assert not [key for key in store.data if key.startswith("/services/oauths/")]

    async def test_a_private_address_is_refused(self) -> None:
        store = _store()
        body = _jira(authConfig={"baseUrl": "https://jira.corp.internal"})
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)) == 400

    async def test_an_address_without_a_scheme_is_checked_and_kept_as_https(self) -> None:
        # Clients read a bare address as https (Salesforce adds it itself), so the check must too.
        store = _store()
        body = _jira(authConfig={"baseUrl": "jira.corp.internal"})
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)) == 400

        body = _jira(authConfig={"baseUrl": " me.atlassian.net "})
        result = await toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)
        saved = store.data[USER_PATH.format("u-carol", result["instance"]["_id"])]
        assert saved["auth"]["baseUrl"] == "https://me.atlassian.net"

    async def test_one_that_names_its_server_by_host_is_left_to_administrators(self) -> None:
        # A database driver can't be held to the address that was checked, as an HTTP client is.
        mariadb = {"mariadb": {
            "name": "mariadb", "display_name": "MariaDB", "supported_auth_types": ["BASIC_AUTH"], "tools": [],
            "config": {"auth": {"schemas": {"BASIC_AUTH": {"fields": [
                {"name": "host", "usage": "CONFIGURE"}, {"name": "port", "usage": "CONFIGURE"},
                {"name": "username", "usage": "AUTHENTICATE"}, {"name": "password", "usage": "AUTHENTICATE"},
            ]}}}},
        }}
        store = _store()
        body = {"instanceName": "My DB", "toolsetType": "mariadb", "authType": "BASIC_AUTH", "authConfig": {"host": "db.example.com"}}
        with patch.dict(_METADATA, mariadb):
            for caller in ("u-carol", "admin-1"):
                with pytest.raises(HTTPException) as exc:
                    await toolsets.create_toolset_instance(_as(store, caller, {**body, "scope": "personal"}), config_service=store)
                assert exc.value.status_code == 400 and "administrator" in exc.value.detail
        assert store.writes == []

    async def test_an_address_of_another_kind_is_refused(self) -> None:
        store = _store()
        body = _jira(authConfig={"baseUrl": "ftp://me.atlassian.net"})
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", body), config_service=store)) == 400

    async def test_a_person_has_a_limited_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(toolset_store.MAX_PERSONAL_INSTANCES_ENV, "2")
        store = _store()
        assert await _status(toolsets.create_toolset_instance(_as(store, "u-alice", _jira()), config_service=store)) == 409

    async def test_names_are_unique_among_the_persons_own(self) -> None:
        store = _store()
        assert await _status(
            toolsets.create_toolset_instance(_as(store, "u-alice", _jira("alice-jira")), config_service=store)
        ) == 409
        # Someone else's name is free: nobody else sees it.
        await toolsets.create_toolset_instance(_as(store, "u-carol", _jira("alice-jira")), config_service=store)

    async def test_an_unconfirmed_role_creates_nothing(self) -> None:
        store = _store()
        with patch.object(toolsets, "fetch_caller_role", new=AsyncMock(return_value=MagicMock(status=CallerRoleStatus.UNKNOWN))):
            assert await _status(toolsets.create_toolset_instance(_as(store, "u-carol", _jira()), config_service=store)) == 503
        assert store.writes == []


async def _my_toolsets(store: FakeConfigService, user_id: str, scope: str | None = None) -> list[dict[str, Any]]:
    result = await toolsets.get_my_toolsets(
        request=_as(store, user_id), search=None, page=1, limit=50, include_registry=False,
        toolset_type=None, auth_status=None, scope=scope, config_service=store,
    )
    return result["toolsets"]


class TestOnlyTheOwnerSeesIt:
    async def test_their_list_has_the_orgs_and_their_own(self) -> None:
        listed = await _my_toolsets(_store(), "u-alice")
        assert sorted((t["instanceId"], t["scope"]) for t in listed) == [
            ("alice-gmail", "personal"), ("alice-jira", "personal"), ("org-jira", "org"),
        ]

    async def test_the_admin_catalog_leaves_their_own_out(self) -> None:
        """The team page lists the org's instances only, counts included."""
        assert [t["instanceId"] for t in await _my_toolsets(_store(), "u-alice", scope="org")] == ["org-jira"]

    async def test_nobody_elses_appears_not_even_for_an_administrator(self) -> None:
        assert [t["instanceId"] for t in await _my_toolsets(_store(), "admin-1")] == ["org-jira"]
        listed = await toolsets.get_toolset_instances(_as(_store(), "admin-1"), page=1, limit=50, search=None, config_service=_store())
        assert [i["_id"] for i in listed["instances"]] == ["org-jira"]

    @pytest.mark.parametrize("caller", ["u-alice", "admin-1"])
    async def test_someone_elses_is_not_found(self, caller: str) -> None:
        store = _store()
        assert await _status(toolsets.get_toolset_instance("bob-jira", _as(store, caller), config_service=store)) == 404

    async def test_the_owner_gets_its_settings(self) -> None:
        store = _store()
        result = await toolsets.get_toolset_instance("alice-jira", _as(store, "u-alice"), config_service=store)
        assert (result["instance"]["scope"], result["instance"]["auth"]) == ("personal", {"baseUrl": "https://alice.atlassian.net"})


class TestTheOwnerManagesIt:
    async def test_they_rename_it_and_change_its_address(self) -> None:
        store = _store()
        body = {"instanceName": "Jira (mine)", "authConfig": {"baseUrl": "https://alice2.atlassian.net"}}
        await toolsets.update_toolset_instance("alice-jira", _as(store, "u-alice", body), config_service=store)

        saved = store.data[USER_PATH.format("u-alice", "alice-jira")]
        assert (saved["instanceName"], saved["auth"], saved["scope"]) == ("Jira (mine)", {"baseUrl": "https://alice2.atlassian.net"}, "personal")
        assert ORG_LIST not in store.writes

    async def test_a_new_oauth_app_signs_them_out_of_it(self) -> None:
        store = _store(**{CRED_PATH.format("alice-gmail", "u-alice"): {"isAuthenticated": True}})
        body = {"authConfig": {"clientId": "new-client", "clientSecret": "new-secret"}}
        result = await toolsets.update_toolset_instance("alice-gmail", _as(store, "u-alice", body), config_service=store)

        assert result["deauthenticatedUserCount"] == 1
        assert store.data[CRED_PATH.format("alice-gmail", "u-alice")]["isAuthenticated"] is False

    async def test_a_private_address_is_refused_on_edit_too(self) -> None:
        store = _store()
        body = {"authConfig": {"baseUrl": "https://jira.corp.internal"}}
        assert await _status(toolsets.update_toolset_instance("alice-jira", _as(store, "u-alice", body), config_service=store)) == 400

    async def test_another_member_cant_and_an_administrator_doesnt_find_it(self) -> None:
        store = _store()
        body = {"instanceName": "mine now"}
        assert await _status(toolsets.update_toolset_instance("alice-jira", _as(store, "u-bob", body), config_service=store)) == 403
        assert await _status(toolsets.update_toolset_instance("alice-jira", _as(store, "admin-1", body), config_service=store)) == 404
        assert await _status(toolsets.delete_toolset_instance("alice-jira", _as(store, "admin-1"), config_service=store)) == 404
        assert USER_PATH.format("u-alice", "alice-jira") in store.data

    async def test_deleting_it_takes_its_sign_ins_and_rules(self) -> None:
        store = _store(**{
            CRED_PATH.format("alice-jira", "u-alice"): {"isAuthenticated": True},
            RULES_PATH.format("alice-jira"): {"tools": {"create_issue": "allow"}},
            OWNER_PATH.format("alice-jira"): {"orgId": "org-1", "userId": "u-alice"},
        })
        result = await toolsets.delete_toolset_instance("alice-jira", _as(store, "u-alice"), config_service=store)

        assert result["deletedCredentialsCount"] == 1
        for key in (
            USER_PATH.format("u-alice", "alice-jira"), CRED_PATH.format("alice-jira", "u-alice"),
            RULES_PATH.format("alice-jira"), OWNER_PATH.format("alice-jira"),
        ):
            assert key not in store.data

    async def test_a_new_one_is_found_by_its_id_without_listing_anyones(self) -> None:
        # Tool clients and token refresh have only the id; listing keys is a full scan on Redis.
        store = _store()
        result = await toolsets.create_toolset_instance(_as(store, "u-carol", _jira()), config_service=store)
        instance_id = result["instance"]["_id"]
        store.list_keys_in_directory = AsyncMock(side_effect=AssertionError("listed keys"))  # type: ignore[method-assign]

        found = await toolset_store.find_personal_instance(store, "org-1", instance_id)  # type: ignore[arg-type]

        assert found and found["createdBy"] == "u-carol"
        assert await toolset_store.find_personal_instance(store, "org-2", instance_id) is None  # type: ignore[arg-type]

    async def test_one_an_agent_uses_isnt_deleted(self) -> None:
        store = _store()
        with pytest.raises(HTTPException):
            await toolsets.delete_toolset_instance("alice-jira", _as(store, "u-alice", in_use=["Triage bot"]), config_service=store)
        assert USER_PATH.format("u-alice", "alice-jira") in store.data


class TestSigningIn:
    async def test_the_owners_sign_in_is_marked_personal(self) -> None:
        """The approval gate reads the mark: a personal instance's rules are the person's own."""
        store = _store()
        body = {"auth": {"email": "alice@example.com", "apiToken": "t"}}
        await toolsets.authenticate_toolset_instance("alice-jira", _as(store, "u-alice", body), config_service=store)
        assert store.data[CRED_PATH.format("alice-jira", "u-alice")]["scope"] == "personal"

    async def test_nobody_else_can_sign_in_to_it(self) -> None:
        store = _store()
        body = {"auth": {"email": "bob@example.com", "apiToken": "t"}}
        assert await _status(toolsets.authenticate_toolset_instance("alice-jira", _as(store, "u-bob", body), config_service=store)) == 404
        assert await _status(toolsets.get_instance_status("alice-jira", _as(store, "u-bob"), config_service=store)) == 404

    async def test_oauth_uses_the_app_on_the_instance(self) -> None:
        store = _store()
        build = AsyncMock(side_effect=RuntimeError("stop here"))
        with patch.object(toolsets, "_build_oauth_config", new=build), pytest.raises(RuntimeError):
            await toolsets.get_instance_oauth_authorization_url(
                "alice-gmail", _as(store, "u-alice"), base_url="https://app.example", config_service=store,
            )
        auth_config = build.call_args.args[0]
        assert (auth_config["clientId"], auth_config["clientSecret"]) == ("alice-client", "alice-secret")

    async def test_the_callback_keeps_the_sign_in_for_the_instances_own_app(self) -> None:
        store = _store()
        state = toolsets._encode_state_with_instance("s-1", "alice-gmail", "u-alice")

        class _Provider:
            def __init__(self, *, credentials_path: str, **_kwargs: object) -> None:
                self.path = credentials_path

            async def handle_callback(self, code: str, state: str) -> Any:  # noqa: ANN401
                store.data[self.path] = {"credentials": {"access_token": "tok"}, "oauthConfigId": "stale"}
                return MagicMock(access_token="tok")

            async def close(self) -> None:
                return None

        with patch.object(toolsets, "_build_oauth_config", new=AsyncMock(return_value={})), \
             patch.object(toolsets, "get_oauth_config", new=MagicMock()), \
             patch.object(toolsets, "OAuthProvider", new=_Provider), \
             patch.object(toolsets, "_validate_toolset_oauth_setup", new=AsyncMock(return_value=None)):
            result = await toolsets.handle_toolset_oauth_callback(
                _as(store, "u-alice"), code="c", state=state, error=None, base_url="https://app.example",
                config_service=store, notification_service=MagicMock(),
            )

        assert result["success"] is True
        saved = store.data[CRED_PATH.format("alice-gmail", "u-alice")]
        assert (saved["isAuthenticated"], saved["scope"], "oauthConfigId" in saved) == (True, "personal", False)


class TestItsToolApprovals:
    async def test_the_owner_reads_and_edits_them(self) -> None:
        store = _store()
        read = await toolsets.get_toolset_tool_rules("alice-jira", _as(store, "u-alice"), config_service=store)
        assert read["canEdit"] is True

        body = toolsets.ToolsetToolRulesBody(tools={"create_issue": "allow"})
        await toolsets.update_toolset_tool_rules("alice-jira", body, _as(store, "u-alice"), config_service=store)
        assert store.data[RULES_PATH.format("alice-jira")]["tools"] == {"create_issue": "allow"}

    @pytest.mark.parametrize("caller", ["u-bob", "admin-1"])
    async def test_nobody_else_sees_or_changes_them(self, caller: str) -> None:
        store = _store()
        body = toolsets.ToolsetToolRulesBody(tools={"create_issue": "allow"})
        assert await _status(toolsets.get_toolset_tool_rules("alice-jira", _as(store, caller), config_service=store)) == 404
        assert await _status(toolsets.update_toolset_tool_rules("alice-jira", body, _as(store, caller), config_service=store)) == 404

    async def test_an_org_instances_stay_the_administrators(self) -> None:
        store = _store()
        read = await toolsets.get_toolset_tool_rules("org-jira", _as(store, "u-alice"), config_service=store)
        assert read["canEdit"] is False
        body = toolsets.ToolsetToolRulesBody(tools={"create_issue": "allow"})
        assert await _status(toolsets.update_toolset_tool_rules("org-jira", body, _as(store, "u-alice"), config_service=store)) == 403


class TestUsingIt:
    async def test_the_main_chat_gets_the_owners_signed_in_personal_instances(self) -> None:
        store = _store(**{
            CRED_PATH.format("alice-jira", "u-alice"): {"isAuthenticated": True},
            CRED_PATH.format("bob-jira", "u-bob"): {"isAuthenticated": True},
        })
        listed, auth = await toolsets.get_authenticated_toolsets("u-alice", "org-1", store, _registry())  # type: ignore[arg-type]
        assert [(t["instanceId"], t["scope"]) for t in listed] == [("alice-jira", "personal")]
        assert list(auth) == ["alice-jira"]

    async def test_a_tool_client_reads_its_settings_by_id(self) -> None:
        assert (await get_toolset_by_id("bob-jira", _store()))["auth"] == {"baseUrl": "https://bob.atlassian.net"}  # type: ignore[arg-type,index]

    async def test_an_address_that_turned_private_is_never_used(self) -> None:
        store = _store()
        store.data[USER_PATH.format("u-bob", "bob-jira")]["auth"] = {"baseUrl": "https://jira.corp.internal"}
        assert await get_toolset_by_id("bob-jira", store, "org-1") is None  # type: ignore[arg-type]

    async def test_token_refresh_gets_the_instances_own_app(self) -> None:
        record = {"instanceId": "alice-gmail", "toolsetType": "gmail", "orgId": "org-1", "credentials": {}}
        app = await get_oauth_credentials_for_toolset(record, _store())  # type: ignore[arg-type]
        assert (app["clientId"], app["clientSecret"]) == ("alice-client", "alice-secret")


class TestCleanup:
    async def test_a_deleted_users_instances_sign_ins_and_rules_go(self) -> None:
        store = _store(**{
            CRED_PATH.format("alice-gmail", "u-alice"): {"isAuthenticated": True},
            RULES_PATH.format("alice-gmail"): {"tools": {}},
            CRED_PATH.format("bob-jira", "u-bob"): {"isAuthenticated": True},
        })
        assert await toolset_store.remove_user_personal_instances(store, "org-1", "u-alice") == 2  # type: ignore[arg-type]

        assert not [key for key in store.data if "alice" in key]
        assert USER_PATH.format("u-bob", "bob-jira") in store.data
        assert CRED_PATH.format("bob-jira", "u-bob") in store.data

    async def test_a_deleted_orgs_all_go(self) -> None:
        store = _store()
        assert await toolset_store.remove_org_personal_instances(store, "org-1") == 3  # type: ignore[arg-type]
        assert not [key for key in store.data if key.startswith("/services/toolset-user-instances/")]
        assert [i["_id"] for i in store.data[ORG_LIST]] == ["org-jira"]
