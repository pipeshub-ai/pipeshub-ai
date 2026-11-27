"""A sign-in the source no longer accepts stops the run before anything is written from it."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.connector.failure_notices import (
    ReconnectRequiredError,
    SyncIncompleteError,
    SyncRunState,
)
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.linear import connector as linear_mod
from app.connectors.sources.linear.api_gateway import LinearAuthError
from app.connectors.sources.linear.connector import LinearConnector
from app.connectors.sources.zammad import connector as zammad_mod
from app.connectors.sources.zammad.connector import ReadOutcome, ZammadConnector
from app.connectors.sources.zendesk import connector as zendesk_mod
from app.connectors.sources.zendesk.connector import (
    ZendeskAuthError,
    ZendeskConnector,
    ZendeskNotAdminError,
)
from app.services.notification.types import NotificationType

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import BaseConnector

pytestmark = pytest.mark.asyncio

_C = TypeVar("_C")


def _bare(cls: type[_C], **attrs: object) -> _C:
    c = object.__new__(cls)
    c.connector_id, c.connector_name, c.created_by = "c1", SimpleNamespace(value="X"), "u1"
    c.connector_instance_name = "Eng"
    c.logger = logging.getLogger("test.rejected")
    c.notify = AsyncMock()
    c.run_state = SyncRunState()
    for name, value in attrs.items():
        setattr(c, name, value)
    return c


def _types(c: BaseConnector) -> list[NotificationType]:
    return [call.kwargs["type"] for call in c.notify.await_args_list]

async def test_zammad_dead_token_writes_no_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zammad_mod, "load_connector_filters", AsyncMock(return_value=({}, {})))
    processor = MagicMock(on_new_app_users=AsyncMock(), on_new_user_groups=AsyncMock())
    ds = MagicMock(get_current_user=AsyncMock(return_value=SimpleNamespace(status_code=401, error=None, message="no")))
    c = _bare(
        ZammadConnector, external_client=MagicMock(), config_service=MagicMock(), data_entities_processor=processor,
        _get_fresh_datasource=AsyncMock(return_value=ds), _fetch_roles=AsyncMock(return_value=([], ReadOutcome.REFUSED)),
        _fetch_users=AsyncMock(return_value=([], {})),
    )
    with pytest.raises(ReconnectRequiredError, match="access token"):
        await c.run_sync()
    c._fetch_users.assert_not_awaited()
    processor.on_new_user_groups.assert_not_awaited()
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]


def _jira_dc(me_status: int) -> JiraDataCenterConnector:
    return _bare(
        JiraDataCenterConnector, creator_email="me@x.com", config_service=MagicMock(get_config=AsyncMock(return_value={})),
        data_source=MagicMock(get_current_user_v2=AsyncMock(return_value=SimpleNamespace(status=me_status))),
    )


async def test_jira_dc_dead_token_does_not_replace_stored_project_access() -> None:
    c = _jira_dc(401)
    with pytest.raises(ReconnectRequiredError):
        await c._fallback_permissions_for_forbidden_scheme("ENG", 401, "permission scheme")
    c.notify.assert_not_awaited()


async def test_jira_dc_401_for_a_missing_permission_still_falls_back() -> None:
    [grant] = await _jira_dc(200)._fallback_permissions_for_forbidden_scheme("ENG", 401, "permission scheme")
    assert grant.email == "me@x.com"


async def test_jira_dc_keeps_stored_project_access_when_it_cannot_tell_whether_the_token_works() -> None:
    unavailable = _jira_dc(503)
    assert await unavailable._fallback_permissions_for_forbidden_scheme("ENG", 401, "permission scheme") is None
    unreachable = _jira_dc(200)
    unreachable.data_source.get_current_user_v2 = AsyncMock(side_effect=TimeoutError())
    assert await unreachable._fallback_permissions_for_forbidden_scheme("ENG", 401, "permission scheme") is None
    unavailable.notify.assert_not_awaited()


async def test_jira_dc_dead_token_sends_one_reconnect_notice() -> None:
    c = _jira_dc(401)
    with pytest.raises(ReconnectRequiredError):
        await c.run_sync()
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]


@pytest.mark.parametrize("cls", [JiraDataCenterConnector, ZammadConnector])
async def test_a_sync_that_cannot_start_in_the_background_tells_someone(cls: type) -> None:
    c = _bare(cls)
    await c.on_background_init_failed(None)
    assert len(_types(c)) == 1


async def test_zammad_keeps_members_when_it_cannot_tell_whether_the_token_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zammad_mod, "load_connector_filters", AsyncMock(return_value=({}, {})))
    processor = MagicMock(
        on_new_app_users=AsyncMock(), on_new_user_groups=AsyncMock(), on_new_record_groups=AsyncMock()
    )
    unavailable = SimpleNamespace(status_code=503, success=False, error="unavailable", message=None)
    ds = MagicMock(get_current_user=AsyncMock(return_value=unavailable))
    c = _bare(
        ZammadConnector, external_client=MagicMock(), config_service=MagicMock(), data_entities_processor=processor,
        _get_fresh_datasource=AsyncMock(return_value=ds), _user_access={},
        _fetch_roles=AsyncMock(return_value=([], ReadOutcome.COMPLETE)),
        _fetch_groups=AsyncMock(return_value=([], [MagicMock()])), _sync_roles=AsyncMock(),
        _sync_tickets_for_groups=AsyncMock(), _remove_tickets_in_excluded_groups=AsyncMock(),
        _sync_knowledge_bases=AsyncMock(),
    )

    async def refused_users(_roles: object) -> tuple[list[object], dict[str, str]]:
        c._users_read = ReadOutcome.REFUSED
        return [], {}

    c._fetch_users = refused_users
    with pytest.raises(SyncIncompleteError):
        await c.run_sync()
    processor.on_new_user_groups.assert_not_awaited()
    c._sync_roles.assert_not_awaited()


@pytest.mark.parametrize(("error", "counted"), [
    (ZendeskNotAdminError("The connected account is an agent, not an admin."), False),
    (ZendeskAuthError("Zendesk rejected the sign-in."), True),
])
async def test_zendesk_counts_only_a_refused_sign_in_towards_turning_sync_off(
    monkeypatch: pytest.MonkeyPatch, error: ZendeskAuthError, *, counted: bool,
) -> None:
    switched_off = AsyncMock(return_value=False)
    monkeypatch.setattr(zendesk_mod, "switched_off_after_rejection", switched_off)
    c = _bare(ZendeskConnector, _run_sync=AsyncMock(side_effect=error))
    with pytest.raises(ZendeskAuthError):
        await c.run_sync()
    assert switched_off.await_count == int(counted)
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]


def _linear_config(credentials: dict[str, str]) -> MagicMock:
    config = {"auth": {"authType": "OAUTH"}, "credentials": credentials}
    return MagicMock(get_config=AsyncMock(return_value=config))


async def test_linear_refusing_the_sign_in_at_start_up_is_counted_and_asks_to_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = SimpleNamespace(success=False, status_code=401, message="Authentication required", errors=[])
    monkeypatch.setattr(linear_mod.LinearClient, "build_from_services", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(linear_mod, "LinearDataSource", lambda _client: MagicMock(organization=AsyncMock(return_value=refused)))
    switched_off = AsyncMock(return_value=False)
    monkeypatch.setattr(linear_mod, "switched_off_after_rejection", switched_off)
    c = _bare(LinearConnector, config_service=_linear_config({"access_token": "tok"}))

    with pytest.raises(ConnectorInitError) as raised:
        await c.init()
    assert isinstance(raised.value.__cause__, LinearAuthError)
    assert "no refresh token" in str(raised.value.__cause__)

    await c.on_background_init_failed(raised.value)
    switched_off.assert_awaited_once()
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]


@pytest.mark.parametrize("refresh_refused", [False, True])
async def test_linear_renews_a_token_refused_at_start_up_before_giving_up(
    monkeypatch: pytest.MonkeyPatch, *, refresh_refused: bool,
) -> None:
    refused = SimpleNamespace(success=False, status_code=401, message="Authentication required", errors=[])
    found = SimpleNamespace(success=True, data={"organization": {"id": "o1", "name": "Acme", "urlKey": "acme"}})
    ds = MagicMock(organization=AsyncMock(side_effect=[refused, found]))
    monkeypatch.setattr(linear_mod.LinearClient, "build_from_services", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(linear_mod, "LinearDataSource", lambda _client: ds)
    service = MagicMock(refresh_now=AsyncMock(side_effect=RefreshTokenInvalidError("revoked") if refresh_refused else None))
    monkeypatch.setattr(startup_service, "get_token_refresh_service", lambda: service)
    c = _bare(LinearConnector, config_service=_linear_config({"access_token": "tok", "refresh_token": "r"}))

    if refresh_refused:
        with pytest.raises(ConnectorInitError) as raised:
            await c.init()
        assert "refresh token was also rejected" in str(raised.value.__cause__)
    else:
        assert await c.init() is True
        assert c.organization_id == "o1"
    service.refresh_now.assert_awaited_once()


async def test_zammad_keeps_members_when_the_token_was_replaced_after_the_refused_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zammad_mod, "load_connector_filters", AsyncMock(return_value=({}, {})))
    processor = MagicMock(
        on_new_app_users=AsyncMock(), on_new_user_groups=AsyncMock(), on_new_record_groups=AsyncMock()
    )
    accepted = SimpleNamespace(status_code=200, success=True, error=None, message=None)
    ds = MagicMock(get_current_user=AsyncMock(return_value=accepted))
    old_client, new_client = SimpleNamespace(token="old"), SimpleNamespace(token="new")
    c = _bare(
        ZammadConnector, external_client=MagicMock(get_client=MagicMock(return_value=old_client)),
        config_service=MagicMock(), data_entities_processor=processor, _user_access={},
        _users_read=ReadOutcome.COMPLETE, _fetch_roles=AsyncMock(return_value=([], ReadOutcome.COMPLETE)),
        _fetch_groups=AsyncMock(return_value=([], [MagicMock()])), _sync_roles=AsyncMock(),
        _sync_tickets_for_groups=AsyncMock(), _remove_tickets_in_excluded_groups=AsyncMock(),
        _sync_knowledge_bases=AsyncMock(),
    )

    async def fresh_datasource() -> MagicMock:
        # The owner saved a new token after the user list was refused with the old one.
        if c._users_read is ReadOutcome.REFUSED:
            c.external_client.get_client.return_value = new_client
        return ds

    async def refused_users(_roles: object) -> tuple[list[object], dict[str, str]]:
        c._users_read = ReadOutcome.REFUSED
        return [], {}

    c._get_fresh_datasource = fresh_datasource
    c._fetch_users = refused_users
    with pytest.raises(SyncIncompleteError):
        await c.run_sync()
    processor.on_new_user_groups.assert_not_awaited()
    c._sync_roles.assert_not_awaited()


async def test_zammad_does_not_reject_a_token_replaced_while_it_was_checked() -> None:
    in_use = SimpleNamespace(token="old")
    refused = SimpleNamespace(status_code=401, success=False, error="no", message=None)

    async def current_user() -> SimpleNamespace:
        in_use.token = "new"  # the owner saved a new token while the check was in flight
        return refused

    c = _bare(
        ZammadConnector, external_client=MagicMock(get_client=MagicMock(return_value=in_use)),
        _get_fresh_datasource=AsyncMock(return_value=MagicMock(get_current_user=current_user)),
    )
    assert await c._stop_if_signed_out() is False
    assert c.run_state.rejected is None
    c.notify.assert_not_awaited()
