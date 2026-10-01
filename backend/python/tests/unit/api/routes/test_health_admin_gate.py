"""The health-check routes call whatever provider URL the request body names, so they
are admin-only; "admin" is decided by the edition's own check (app.edition_config)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (must load before the route modules)
from app.api.routes import health


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    request.app.container.config_service.return_value = MagicMock()
    return request


async def _gate(is_admin: bool) -> AsyncMock:
    check = AsyncMock(return_value=is_admin)
    with patch("app.edition_config.check_user_is_admin", new=check):
        await health.require_admin_caller(_request())
    return check


class TestRequireAdminCaller:
    async def test_admin_passes(self) -> None:
        check = await _gate(True)

        assert check.await_args.args[:2] == ("u1", "o1")

    async def test_non_admin_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _gate(False)

        assert exc.value.status_code == 403

    async def test_request_without_a_user_is_refused(self) -> None:
        request = _request()
        request.state.user = None

        with patch("app.edition_config.check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await health.require_admin_caller(request)

        assert exc.value.status_code == 403


def test_every_health_route_sits_behind_the_gate() -> None:
    gated = {dep.dependency for dep in health.router.dependencies}

    assert health.require_admin_caller in gated
    assert health.router.routes
