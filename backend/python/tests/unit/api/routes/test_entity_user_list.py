"""Who may ask the directory for machine identities.

``GET /entity/user/list`` is what the gateway's ``GET /users/graph/list``
proxies to, and each row carries the ``userId`` that binds an agent to a
service account. The rule is the people list's rule: only an administrator, and
only when asked. The query that drops them is tested in
``tests/unit/services/graph_db/test_organization_users_hide_service_accounts.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes.entity import _may_list_service_accounts


def _request() -> MagicMock:
    request = MagicMock()
    request.app.container.config_service.return_value = MagicMock()
    return request


@pytest.mark.asyncio
async def test_nobody_gets_them_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not even an administrator, and the role is not looked up at all: this is
    the directory of people, and every caller gets that by default."""
    asked = []

    async def role(_request: object, _config: object) -> CallerRole:
        asked.append(True)
        return CallerRole(CallerRoleStatus.VALID, "admin")

    monkeypatch.setattr("app.api.routes.entity.fetch_caller_role", role)

    assert await _may_list_service_accounts(_request(), asked=False) is False
    assert asked == []


@pytest.mark.asyncio
async def test_a_member_asking_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    async def role(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.VALID, "member")

    monkeypatch.setattr("app.api.routes.entity.fetch_caller_role", role)

    assert await _may_list_service_accounts(_request(), asked=True) is False


@pytest.mark.asyncio
async def test_an_administrator_asking_gets_them(monkeypatch: pytest.MonkeyPatch) -> None:
    async def role(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.VALID, "admin")

    monkeypatch.setattr("app.api.routes.entity.fetch_caller_role", role)

    assert await _may_list_service_accounts(_request(), asked=True) is True


@pytest.mark.asyncio
async def test_an_unconfirmed_role_narrows_rather_than_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike the binding gate, this one only decides what a listing holds, so
    "not an administrator" is the safe answer and the request still works."""
    async def role(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.UNKNOWN)

    monkeypatch.setattr("app.api.routes.entity.fetch_caller_role", role)

    assert await _may_list_service_accounts(_request(), asked=True) is False
