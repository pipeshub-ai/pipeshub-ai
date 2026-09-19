"""Unit tests for the shared admin dependency (KG Clean Rebuild plan, Phase 7).

See also ``tests/unit/api/routes/test_toolsets.py::TestCheckUserIsAdmin`` for
the original (pre-extraction) coverage of the same HTTP-call logic, still
exercised the same way since ``toolsets.py`` now delegates here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.middlewares.admin import (
    check_user_is_admin,
    get_user_context,
    require_admin,
)


class TestGetUserContext:
    def test_missing_user_id_raises_401(self) -> None:
        request = MagicMock()
        request.state.user = {}
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            get_user_context(request)
        assert exc_info.value.status_code == 401

    def test_extracts_user_and_org_from_state(self) -> None:
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "org-1"}
        request.headers = {}

        context = get_user_context(request)

        assert context == {"user_id": "u1", "org_id": "org-1"}

    def test_falls_back_to_headers(self) -> None:
        request = MagicMock()
        request.state.user = {}
        request.headers = {"X-User-Id": "u2", "X-Organization-Id": "org-2"}

        context = get_user_context(request)

        assert context == {"user_id": "u2", "org_id": "org-2"}


class TestCheckUserIsAdmin:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self) -> None:
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"nodejs": {"endpoint": "http://localhost:3001"}})
        request = MagicMock()
        request.headers = {"authorization": "Bearer token"}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.api.middlewares.admin.httpx.AsyncClient", return_value=mock_client):
            result = await check_user_is_admin("u1", request, cs)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_403(self) -> None:
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"nodejs": {"endpoint": "http://localhost:3001"}})
        request = MagicMock()
        request.headers = {}

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.api.middlewares.admin.httpx.AsyncClient", return_value=mock_client):
            result = await check_user_is_admin("u1", request, cs)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_fails_closed_to_non_admin(self) -> None:
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd down"))
        request = MagicMock()
        request.headers = {}

        with patch("app.api.middlewares.admin.httpx.AsyncClient", side_effect=Exception("network")):
            result = await check_user_is_admin("u1", request, cs)
        assert result is False


class TestRequireAdmin:
    @pytest.mark.asyncio
    async def test_admin_passes(self) -> None:
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "org-1"}
        request.headers = {}
        request.app.container.config_service.return_value = MagicMock()

        with patch("app.api.middlewares.admin.check_user_is_admin", new=AsyncMock(return_value=True)):
            await require_admin(request)  # should not raise

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self) -> None:
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "org-1"}
        request.headers = {}
        request.app.container.config_service.return_value = MagicMock()

        with patch("app.api.middlewares.admin.check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc_info:
                await require_admin(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401_before_admin_check(self) -> None:
        request = MagicMock()
        request.state.user = {}
        request.headers = {}

        with patch("app.api.middlewares.admin.check_user_is_admin", new=AsyncMock()) as mock_check:
            with pytest.raises(HTTPException) as exc_info:
                await require_admin(request)
        assert exc_info.value.status_code == 401
        mock_check.assert_not_called()
