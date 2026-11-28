"""API1-P4b: toolset, MCP and API-toolset routes must not read a store outage as "absent".

Before the fix each route below turned a config-store read that failed into the answer for "not there": the MCP and
API-toolset gates said "disabled" (403), an instance "not found" (404), a credential "not signed in" (409), a list
empty (200). Now a store that could not answer is a 503 with ``Retry-After: 5``; a real absence keeps its answer.
Route handlers are called in-process on the real ConfigurationService over ``StoreKV``: ``fail_reads`` makes chosen
reads raise ``ConnectionError`` (what the Redis, etcd and Postgres stores raise when down), ``raise_for`` makes every
strict read of a key raise a given error (``DecryptionError`` for a value saved under another SECRET_KEY).
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException

from app.agents import toolset_store
from app.agents.agent_loop import tool_approvals
from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_oauth_state_path,
    get_mcp_user_instance_path,
)
from app.agents.constants.toolset_constants import (
    get_toolset_personal_owner_path,
    get_toolset_user_instance_path,
)
from app.agents.mcp import service as mcp_service
from app.api.routes import api_toolsets, mcp_servers, toolsets
from app.config.configuration_service import ConfigurationService
from app.core import org_context
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_api_toolsets_enabled,
    is_mcp_enabled,
)
from app.utils import error_class
from app.utils.encryption.encryption_service import DecryptionError
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.connectors.test_oauth_config_read_outage import LOG, FlakyKV

if TYPE_CHECKING:
    from collections.abc import Awaitable

pytestmark = pytest.mark.asyncio

ORG, USER = "o1", "u1"
LIST = toolsets._get_instances_path(ORG)
JIRA_APPS = toolsets._get_toolset_oauth_config_path("jira")
JIRA = {"_id": "t1", "orgId": ORG, "toolsetType": "jira", "instanceName": "Jira", "authType": "API_TOKEN"}
GITHUB = {"_id": "g1", "orgId": ORG, "toolsetType": "github", "instanceName": "GitHub", "authType": "OAUTH"}
PERSONAL = {"_id": "p1", "orgId": ORG, "createdBy": USER, "scope": "personal", "toolsetType": "jira",
            "instanceName": "Mine", "authType": "API_TOKEN"}
OAUTH_APP = {"_id": "c1", "orgId": ORG, "oauthInstanceName": "App", "config": {"clientId": "id", "clientSecret": "s"}}
MCP = {"_id": "m1", "orgId": ORG, "name": "Linear", "authMode": "api_token", "transport": "streamable_http",
       "url": "https://mcp.example.com/mcp", "createdBy": USER}
SHARED_MCP = {**MCP, "useAdminAuth": True}
SIGNED_IN = {"isAuthenticated": True, "auth": {"apiToken": "t"}}


class StoreKV(FlakyKV):
    """FlakyKV plus ``raise_for``: a strict read of the key raises that error; a soft one answers None, as the
    encrypted layer does for a value it cannot decode."""

    def __init__(self) -> None:
        super().__init__()
        self.raise_for: dict[str, BaseException] = {}

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if key in self.raise_for:
            if raise_on_error:
                raise self.raise_for[key]
            return None
        return await super().get_key(key, raise_on_error=raise_on_error)


@pytest.fixture
async def config(monkeypatch: pytest.MonkeyPatch) -> ConfigurationService:
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    kv = StoreKV()
    service = ConfigurationService(LOG, kv)
    service.kv = kv
    yield service
    await service.close()


@pytest.fixture(autouse=True)
def _org() -> Any:  # noqa: ANN401
    with org_context.for_org(ORG, job="p4b-test"):
        yield


def _down(config: ConfigurationService, key: str) -> None:
    config.kv.fail_reads[key] = set(range(1, 100))


def _request(config: ConfigurationService) -> Any:  # noqa: ANN401
    registry = SimpleNamespace(get_toolset_metadata=lambda _t: None, list_toolsets=lambda: [])
    state = SimpleNamespace(config_service=config, toolset_registry=registry)
    return SimpleNamespace(
        app=SimpleNamespace(state=state, container=None), state=SimpleNamespace(user={"userId": USER, "orgId": ORG}), scope={},
    )


def _admin(monkeypatch: pytest.MonkeyPatch, *, is_admin: bool) -> None:
    async def check(*_: Any, **__: Any) -> bool:  # noqa: ANN401
        return is_admin

    monkeypatch.setattr(toolsets, "_check_user_is_admin", check)


async def _outcome(call: Awaitable[Any]) -> tuple[BaseException | None, Any]:
    try:
        return None, await call
    except Exception as exc:
        return exc, None


def _describe(exc: BaseException | None, value: Any) -> str:  # noqa: ANN401
    if isinstance(exc, HTTPException):
        return f"{exc.status_code} {exc.detail!r}"
    return f"{exc!r}" if exc is not None else f"200 {value!r}"


async def _assert_503(call: Awaitable[Any]) -> HTTPException:
    exc, value = await _outcome(call)
    assert isinstance(exc, HTTPException) and exc.status_code == 503, f"answered {_describe(exc, value)}"
    assert (exc.headers or {}).get("Retry-After") == "5"
    return exc


async def _status(call: Awaitable[Any]) -> int:
    exc, value = await _outcome(call)
    if exc is not None and not isinstance(exc, HTTPException):
        raise AssertionError(f"raised {exc!r}")
    return exc.status_code if isinstance(exc, HTTPException) else 200


# ---- The router-level gates: every /api/v1/mcp-servers/* and /api/v1/api-toolsets/* route

async def test_mcp_gate_with_the_settings_unreadable_is_503_not_disabled(config: ConfigurationService) -> None:
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_MCP": True}})
    _down(config, PLATFORM_SETTINGS_KEY)
    raised = await _assert_503(mcp_servers._require_mcp_enabled(_request(config)))
    assert isinstance(raised.__cause__, ConnectionError)
    assert error_class._hides_text(raised.__cause__)


async def test_api_toolsets_gate_with_the_settings_unreadable_is_503_not_disabled(config: ConfigurationService) -> None:
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_API_TOOLSETS": True}})
    _down(config, PLATFORM_SETTINGS_KEY)
    await _assert_503(api_toolsets._require_enabled(_request(config)))


@pytest.mark.parametrize("error", [DecryptionError("saved under another key"), RuntimeError("a bug")], ids=["undecodable", "bug"])
async def test_gates_keep_their_fallback_for_a_failure_that_is_not_an_outage(
    config: ConfigurationService, error: BaseException,
) -> None:
    """Retrying cannot fix these, so they are not a 503: MCP stays fail-closed, API toolsets keep their default (off)."""
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_MCP": True, "ENABLE_API_TOOLSETS": True}})
    config.kv.raise_for[PLATFORM_SETTINGS_KEY] = error
    assert await _status(mcp_servers._require_mcp_enabled(_request(config))) == 403
    assert await _status(api_toolsets._require_enabled(_request(config))) == 403


async def test_control_mcp_gate_saved_off_is_403_and_never_saved_is_on(config: ConfigurationService) -> None:
    assert await _status(mcp_servers._require_mcp_enabled(_request(config))) == 200
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_MCP": False}})
    assert await _status(mcp_servers._require_mcp_enabled(_request(config))) == 403


async def test_control_api_toolsets_gate_unset_is_403_and_saved_on_passes(config: ConfigurationService) -> None:
    assert await _status(api_toolsets._require_enabled(_request(config))) == 403
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_API_TOOLSETS": True}})
    assert await _status(api_toolsets._require_enabled(_request(config))) == 200


# ---- MCP instance reads (GET /instances/{id} stands for every route through _get_org_instance)

async def test_mcp_org_instance_unreadable_is_503_not_404(config: ConfigurationService) -> None:
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    _down(config, get_mcp_instance_path("m1"))
    await _assert_503(mcp_servers.get_instance(_request(config), "m1"))


async def test_mcp_personal_instance_unreadable_is_503_not_404(config: ConfigurationService) -> None:
    personal = get_mcp_user_instance_path(ORG, USER, "m1")
    await config.set_config(personal, {**MCP, "scope": "personal"})
    _down(config, personal)
    await _assert_503(mcp_servers.get_instance(_request(config), "m1"))


async def test_mcp_instance_undecodable_is_500_not_404(config: ConfigurationService) -> None:
    """A record that is there but cannot be decoded is not "not found"; retrying cannot fix it either."""
    config.kv.raise_for[get_mcp_instance_path("m1")] = DecryptionError("saved under another key")
    exc, _ = await _outcome(mcp_servers.get_instance(_request(config), "m1"))
    assert isinstance(exc, DecryptionError)


async def test_control_mcp_instance_missing_is_404(config: ConfigurationService) -> None:
    assert await _status(mcp_servers.get_instance(_request(config), "m1")) == 404


async def test_mcp_oauth_callback_with_the_instance_unreadable_is_503_not_instance_not_found(
    config: ConfigurationService,
) -> None:
    state = "s" * 32
    await config.set_config(get_mcp_oauth_state_path(state), {
        "instanceId": "m1", "orgId": ORG, "userId": USER, "initiatedBy": USER,
        "expiresAt": get_epoch_timestamp_in_ms() + 600_000,
    })
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    _down(config, get_mcp_instance_path("m1"))
    await _assert_503(mcp_servers.handle_oauth_callback(_request(config), code="c", state=state, error=None))


# ---- MCP credential reads: /tools and auto-authenticate

async def test_mcp_tools_with_the_credential_unreadable_is_503_not_not_authenticated(config: ConfigurationService) -> None:
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    await config.set_config(get_mcp_credentials_path("m1", USER), SIGNED_IN)
    _down(config, get_mcp_credentials_path("m1", USER))
    await _assert_503(mcp_servers.get_instance_tools(_request(config), "m1"))


async def test_mcp_tools_with_control_plane_credentials_on_and_the_credential_unreadable_is_503(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    await config.set_config(get_mcp_credentials_path("m1", USER), SIGNED_IN)
    _down(config, get_mcp_credentials_path("m1", USER))
    await _assert_503(mcp_servers.get_instance_tools(_request(config), "m1"))


async def test_mcp_tools_with_a_legacy_shared_credential_unreadable_is_503(config: ConfigurationService) -> None:
    """A shared credential saved before it had its own slot is read at its creator's path."""
    await config.set_config(get_mcp_instance_path("m1"), SHARED_MCP)
    await config.set_config(get_mcp_credentials_path("m1", USER), SIGNED_IN)
    _down(config, get_mcp_credentials_path("m1", USER))
    await _assert_503(mcp_servers.get_instance_tools(_request(config), "m1"))


async def test_resolving_a_legacy_shared_credential_without_adopting_it_raises_an_unreadable_store(
    config: ConfigurationService,
) -> None:
    await config.set_config(get_mcp_credentials_path("m1", USER), SIGNED_IN)
    _down(config, get_mcp_credentials_path("m1", USER))
    with pytest.raises(ConnectionError):
        await mcp_service.resolve_effective_user_auth(SHARED_MCP, USER, config, adopt=False, raise_on_error=True)


async def test_mcp_tools_with_an_undecodable_credential_is_500_not_not_authenticated(config: ConfigurationService) -> None:
    """Q2: unlike a toolset's sign-in, an MCP credential that cannot be decoded is not read as "not signed in"."""
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    config.kv.raise_for[get_mcp_credentials_path("m1", USER)] = DecryptionError("saved under another key")
    exc, _ = await _outcome(mcp_servers.get_instance_tools(_request(config), "m1"))
    assert isinstance(exc, DecryptionError)


async def test_control_mcp_tools_without_a_credential_is_409(config: ConfigurationService) -> None:
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    assert await _status(mcp_servers.get_instance_tools(_request(config), "m1")) == 409


async def test_auto_authenticate_with_the_shared_credential_unreadable_is_503_not_admin_has_not_configured(
    config: ConfigurationService,
) -> None:
    shared = get_mcp_credentials_path("m1", mcp_service.SHARED_CREDENTIAL_OWNER)
    await config.set_config(get_mcp_instance_path("m1"), SHARED_MCP)
    await config.set_config(shared, SIGNED_IN)
    _down(config, shared)
    await _assert_503(mcp_servers.auto_authenticate_instance(_request(config), "m1"))


async def test_control_auto_authenticate_without_a_shared_credential_is_409(config: ConfigurationService) -> None:
    await config.set_config(get_mcp_instance_path("m1"), {**SHARED_MCP, "createdBy": "someone-else"})
    assert await _status(mcp_servers.auto_authenticate_instance(_request(config), "m1")) == 409


# ---- Toolset instance list (_load_toolset_instances, through _find_instance and the list routes)

async def test_find_instance_with_the_list_unreadable_is_503_not_removed(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA])
    _down(config, LIST)
    await _assert_503(toolsets._find_instance("t1", ORG, USER, config))


async def test_get_instances_with_the_list_unreadable_is_503_not_empty(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA])
    _down(config, LIST)
    await _assert_503(toolsets.get_toolset_instances(_request(config), page=1, limit=50, search=None, config_service=config))


async def test_my_toolsets_with_the_list_unreadable_is_503_not_empty(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA])
    _down(config, LIST)
    await _assert_503(toolsets.get_my_toolsets(
        _request(config), search=None, page=1, limit=20, include_registry=False, toolset_type=None,
        auth_status=None, scope="org", config_service=config,
    ))


async def test_agent_toolset_authenticate_with_the_list_unreadable_is_503_not_removed(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def may_edit(*_: Any) -> None:  # noqa: ANN401
        return None

    monkeypatch.setattr(toolsets, "_require_agent_edit_access", may_edit)
    await config.set_config(LIST, [JIRA])
    _down(config, LIST)
    await _assert_503(toolsets.authenticate_agent_toolset("a1", "t1", _request(config), config_service=config))


async def test_a_write_guarded_by_the_list_is_503_when_it_is_unreadable(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /oauth-configs checks the list for instances using the app; it answered 500 for an outage."""
    _admin(monkeypatch, is_admin=True)
    await config.set_config(JIRA_APPS, [OAUTH_APP])
    await config.set_config(LIST, [JIRA])
    stored = config.kv.saved(JIRA_APPS)
    _down(config, LIST)
    await _assert_503(toolsets.delete_toolset_oauth_config("jira", "c1", _request(config), config_service=config))
    assert config.kv.saved(JIRA_APPS) == stored


async def test_a_list_read_that_fails_without_an_outage_is_500_not_503(config: ConfigurationService) -> None:
    config.kv.raise_for[LIST] = RuntimeError("a bug")
    exc, _ = await _outcome(toolsets._find_instance("t1", ORG, USER, config))
    assert isinstance(exc, HTTPException) and exc.status_code == 500


async def test_control_a_toolset_not_in_the_list_is_404(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA])
    assert await _status(toolsets._find_instance("nope", ORG, USER, config)) == 404


# ---- Personal toolset instances (toolset_store)

@pytest.mark.parametrize("route", ["update", "delete"])
async def test_owner_changing_their_personal_instance_while_it_is_unreadable_is_503_not_admins_only(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch, route: str,
) -> None:
    _admin(monkeypatch, is_admin=False)
    key = get_toolset_user_instance_path(ORG, USER, "p1")
    await config.set_config(key, PERSONAL)
    _down(config, key)
    handler = toolsets.update_toolset_instance if route == "update" else toolsets.delete_toolset_instance
    await _assert_503(handler("p1", _request(config), config_service=config))


async def test_find_instance_with_the_personal_instance_unreadable_is_503(config: ConfigurationService) -> None:
    await config.set_config(LIST, [])
    key = get_toolset_user_instance_path(ORG, USER, "p1")
    await config.set_config(key, PERSONAL)
    _down(config, key)
    await _assert_503(toolsets._find_instance("p1", ORG, USER, config))


async def test_find_personal_instance_strict_raises_when_the_owner_pointer_is_unreadable(
    config: ConfigurationService,
) -> None:
    """The check that keeps a personal toolset off a shared agent must not read an outage as "not personal"."""
    await config.set_config(get_toolset_personal_owner_path("p1"), {"orgId": ORG, "userId": USER})
    await config.set_config(get_toolset_user_instance_path(ORG, USER, "p1"), PERSONAL)
    _down(config, get_toolset_personal_owner_path("p1"))
    with pytest.raises(ConnectionError):
        await toolset_store.find_personal_instance(config, ORG, "p1", raise_on_error=True)


async def test_control_non_admin_without_a_personal_instance_is_403(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admin(monkeypatch, is_admin=False)
    assert await _status(toolsets.update_toolset_instance("p1", _request(config), config_service=config)) == 403


# ---- A toolset type's OAuth app list

async def test_delete_oauth_config_with_the_app_list_unreadable_is_503_not_404(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admin(monkeypatch, is_admin=True)
    await config.set_config(JIRA_APPS, [OAUTH_APP])
    _down(config, JIRA_APPS)
    await _assert_503(toolsets.delete_toolset_oauth_config("jira", "c1", _request(config), config_service=config))


async def test_list_oauth_configs_with_the_app_list_unreadable_is_503_not_empty(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admin(monkeypatch, is_admin=True)
    await config.set_config(JIRA_APPS, [OAUTH_APP])
    _down(config, JIRA_APPS)
    await _assert_503(toolsets.list_toolset_oauth_configs("jira", _request(config), config_service=config))


async def test_list_oauth_configs_with_the_app_list_undecodable_reads_as_empty(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As for connectors (P4a): a list no retry can decode reads as unset, so an admin can add an app again."""
    _admin(monkeypatch, is_admin=True)
    config.kv.raise_for[JIRA_APPS] = DecryptionError("saved under another key")
    _, body = await _outcome(toolsets.list_toolset_oauth_configs("jira", _request(config), config_service=config))
    assert body["oauthConfigs"] == []


async def test_saving_an_oauth_app_with_the_app_list_undecodable_fails_and_saves_nothing(
    config: ConfigurationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list is shared by every org: read as empty and saved back, it would delete every OAuth app of the type."""
    async def prepared(auth_config: dict[str, Any], *_: Any) -> dict[str, Any]:  # noqa: ANN401
        return dict(auth_config)

    monkeypatch.setattr(toolsets, "_prepare_toolset_auth_config", prepared)
    await config.set_config(JIRA_APPS, [OAUTH_APP])
    stored = config.kv.saved(JIRA_APPS)
    config.kv.raise_for[JIRA_APPS] = DecryptionError("saved under another key")
    exc, _ = await _outcome(toolsets._create_or_update_toolset_oauth_config(
        "jira", {"clientId": "new", "clientSecret": "x"}, "New app", USER, ORG, config, None, "https://app",
    ))
    assert isinstance(exc, HTTPException) and exc.status_code == 500
    assert config.kv.saved(JIRA_APPS) == stored


async def test_control_a_missing_oauth_app_is_404(config: ConfigurationService, monkeypatch: pytest.MonkeyPatch) -> None:
    _admin(monkeypatch, is_admin=True)
    assert await _status(toolsets.delete_toolset_oauth_config("jira", "c1", _request(config), config_service=config)) == 404


# ---- A user's sign-in to a toolset: /status and the GitHub pickers

async def test_status_with_the_sign_in_unreadable_is_503_not_signed_out(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA])
    auth = toolsets._get_user_auth_path("t1", USER)
    await config.set_config(auth, {"isAuthenticated": True})
    _down(config, auth)
    await _assert_503(toolsets.get_instance_status("t1", _request(config), config_service=config))


async def test_github_pickers_with_the_sign_in_unreadable_are_503_not_sign_in_first(config: ConfigurationService) -> None:
    await config.set_config(LIST, [GITHUB])
    auth = toolsets._get_user_auth_path("g1", USER)
    await config.set_config(auth, {"isAuthenticated": True, "credentials": {"access_token": "gho_x"}})
    _down(config, auth)
    await _assert_503(toolsets._github_token("g1", _request(config), config))


async def test_an_undecodable_sign_in_reads_as_signed_out(config: ConfigurationService) -> None:
    """P4a's rule for an OAuth record: no retry can decode it, and signing in again overwrites it."""
    await config.set_config(LIST, [JIRA, GITHUB])
    for instance_id in ("t1", "g1"):
        config.kv.raise_for[toolsets._get_user_auth_path(instance_id, USER)] = DecryptionError("saved under another key")
    _, body = await _outcome(toolsets.get_instance_status("t1", _request(config), config_service=config))
    assert body["isAuthenticated"] is False
    assert await _status(toolsets._github_token("g1", _request(config), config)) == 409


async def test_control_not_signed_in_reads_false_and_github_asks_to_sign_in(config: ConfigurationService) -> None:
    await config.set_config(LIST, [JIRA, GITHUB])
    _, body = await _outcome(toolsets.get_instance_status("t1", _request(config), config_service=config))
    assert body["isAuthenticated"] is False
    assert await _status(toolsets._github_token("g1", _request(config), config)) == 409


# ---- Callers outside HTTP (chat, the agent runtime, the harness) keep the soft default

PERSONAL_PENDING = tool_approvals.PendingCall(
    approval_id="a1", org_id=ORG, user_id=USER, conversation_id="c1", source="toolset", personal=True,
    instance_id="p1", server_name="Mine", tool_name="search", created_at=0.0, expires_at=0.0,
)
SOFT_CALLERS = {
    "is_mcp_enabled": (lambda c: is_mcp_enabled(c), False),
    "is_api_toolsets_enabled": (lambda c: is_api_toolsets_enabled(c), False),
    "get_instance": (lambda c: mcp_service.get_instance("m1", c, ORG, USER), None),
    "load_user_instances": (lambda c: toolset_store.load_user_instances(c, ORG, USER), []),
    "find_personal_instance": (lambda c: toolset_store.find_personal_instance(c, ORG, "p1"), None),
    "remember_for_personal_toolset": (
        lambda c: tool_approvals._remember_for_personal_toolset(
            SimpleNamespace(config_service=c, org_id=ORG, user_id=USER), PERSONAL_PENDING, "allow",
        ),
        "failed",
    ),
}


@pytest.mark.parametrize("caller", SOFT_CALLERS)
async def test_callers_outside_http_still_answer_softly_when_the_store_is_down(
    config: ConfigurationService, caller: str,
) -> None:
    """Only the routes ask for the strict read: an HTTPException or a raw store error here would break a chat or a run."""
    call, expected = SOFT_CALLERS[caller]
    await config.set_config(PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_MCP": True, "ENABLE_API_TOOLSETS": True}})
    await config.set_config(get_mcp_instance_path("m1"), MCP)
    await config.set_config(get_toolset_personal_owner_path("p1"), {"orgId": ORG, "userId": USER})
    personal = get_toolset_user_instance_path(ORG, USER, "p1")
    await config.set_config(personal, PERSONAL)
    for key in (PLATFORM_SETTINGS_KEY, get_mcp_instance_path("m1"), personal):
        _down(config, key)
    exc, value = await _outcome(call(config))
    assert exc is None, f"raised {exc!r}"
    assert value == expected
