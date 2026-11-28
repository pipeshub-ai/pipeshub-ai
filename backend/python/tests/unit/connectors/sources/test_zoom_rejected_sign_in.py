"""A Zoom sign-in that is no longer accepted stops the run before anything is written from it."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.failure_notices import (
    ReconnectRequiredError,
    SyncIncompleteError,
    SyncRunState,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.sources.zoom.connector import ZoomConnector, ZoomUser
from app.services.notification.types import NotificationType
from app.sources.client.zoom.zoom import ZoomResponse

if TYPE_CHECKING:
    from datetime import date

    from app.connectors.core.base.connector.connector_service import BaseConnector

pytestmark = pytest.mark.asyncio

UNAUTHORIZED = ZoomResponse(success=False, message="Failed with status 401")


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


def _zoom(monkeypatch: pytest.MonkeyPatch, credentials: dict[str, str], **datasource: object) -> tuple[ZoomConnector, MagicMock]:
    service = MagicMock(note_access_token_rejected=AsyncMock(return_value=False), refresh_now=AsyncMock())
    monkeypatch.setattr(startup_service, "get_token_refresh_service", lambda: service)
    ds = MagicMock(**datasource)
    client = MagicMock(get_token=MagicMock(return_value="tok"))
    c = _bare(
        ZoomConnector, data_source=ds, data_entities_processor=MagicMock(on_new_app_users=AsyncMock()),
        external_client=MagicMock(get_client=MagicMock(return_value=client)),
        config_service=MagicMock(get_config=AsyncMock(return_value={"auth": {"authType": "OAUTH"}, "credentials": credentials})),
        _get_fresh_datasource=AsyncMock(return_value=ds), _register_authenticated_identity=AsyncMock(),
        _load_filters=AsyncMock(),
    )
    return c, service


async def test_zoom_with_no_refresh_token_stops_instead_of_reporting_success(monkeypatch: pytest.MonkeyPatch) -> None:
    c, service = _zoom(monkeypatch, {"access_token": "tok"}, users=AsyncMock(return_value=UNAUTHORIZED))
    with pytest.raises(ReconnectRequiredError, match="no refresh token"):
        await c.run_sync()
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]
    service.note_access_token_rejected.assert_awaited_once()
    c.data_entities_processor.on_new_app_users.assert_not_awaited()


async def test_zoom_renews_an_expired_token_and_carries_on(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ZoomResponse(success=True, data={"users": []})
    c, service = _zoom(
        monkeypatch, {"access_token": "tok", "refresh_token": "r"}, users=AsyncMock(side_effect=[UNAUTHORIZED, page])
    )
    await c.run_sync()
    service.refresh_now.assert_awaited_once()
    assert _types(c) == []


async def test_zoom_signed_out_part_way_stops_at_that_user(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ZoomResponse(success=True, data={"users": [{"id": "a"}, {"id": "b"}]})
    c, _ = _zoom(
        monkeypatch, {"access_token": "tok"}, users=AsyncMock(return_value=page),
        report_meetings=AsyncMock(return_value=UNAUTHORIZED),
    )
    c._build_app_users = MagicMock(return_value=[])

    async def read_meetings(user: ZoomUser, _today: date) -> None:
        await c._list_report_meetings(user.id, None, "x")

    c._sync_meetings_for_user = AsyncMock(side_effect=read_meetings)
    with pytest.raises(ReconnectRequiredError):
        await c.run_sync()
    assert c._sync_meetings_for_user.await_count == 1


async def test_zoom_refusing_the_user_list_names_the_missing_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = ZoomResponse(success=False, message="Failed with status 400")
    c, _ = _zoom(monkeypatch, {"access_token": "tok"}, users=AsyncMock(return_value=refused))
    with pytest.raises(SyncIncompleteError):
        await c.run_sync()
    assert "user:read:list_users:admin" in c.notify.await_args.kwargs["message"]


async def test_a_zoom_sync_that_cannot_start_in_the_background_tells_someone() -> None:
    c = _bare(ZoomConnector)
    await c.on_background_init_failed(None)
    assert len(_types(c)) == 1


async def test_zoom_does_not_reject_a_token_replaced_while_the_call_was_in_flight() -> None:
    in_use = {"token": "old"}
    client = MagicMock(get_token=MagicMock(side_effect=lambda: in_use["token"]))
    c = _bare(ZoomConnector, external_client=MagicMock(get_client=MagicMock(return_value=client)),
              _get_fresh_datasource=AsyncMock())
    answers = [UNAUTHORIZED, ZoomResponse(success=True, data={})]

    async def call() -> ZoomResponse:
        in_use["token"] = "new"  # another call, or the owner, installed a new token meanwhile
        return answers.pop(0)

    response = await c._signed_in(call)
    assert response.success is True
    assert c.run_state.rejected is None
    c._get_fresh_datasource.assert_not_awaited()


async def test_zoom_rate_limit_marks_the_run_so_no_notice_is_sent() -> None:
    c = _bare(ZoomConnector, external_client=None)
    await c._signed_in(AsyncMock(return_value=ZoomResponse(success=False, message="Failed with status 429")))
    assert c.run_state.rate_limited is True
