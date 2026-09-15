"""Handler-level tests for the connectors routes that admit service tokens.

Token validation itself is covered by the auth middleware tests; these tests
check what each handler does with the verified claims it is given.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import _caller_org_and_user, get_record_content_internal

_ROUTER = "app.connectors.api.router"

_RECORD_CONTENT_CLAIMS = {
    "token_type": "scoped",
    "orgId": "org-1",
    "userId": "user-1",
    "scopes": ["record:content"],
}


def _record(org_id="org-1"):
    return SimpleNamespace(id="rec-1", org_id=org_id)


def _graph_provider(record=None, access=True):
    graph_provider = AsyncMock()
    graph_provider.get_record_by_id = AsyncMock(return_value=record)
    graph_provider.check_record_access_with_details = AsyncMock(return_value=access)
    return graph_provider


async def _get_content(claims, graph_provider):
    return await get_record_content_internal(
        request=MagicMock(),
        record_id="rec-1",
        version=None,
        graph_provider=graph_provider,
        config_service=AsyncMock(),
        claims=claims,
    )


class TestGetRecordContentInternal:
    async def test_missing_org_id_raises_401(self):
        claims = {k: v for k, v in _RECORD_CONTENT_CLAIMS.items() if k != "orgId"}
        with pytest.raises(HTTPException) as exc:
            await _get_content(claims, _graph_provider(_record()))
        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_missing_user_id_raises_403(self):
        """Without a user the read would silently become an admin read."""
        claims = {k: v for k, v in _RECORD_CONTENT_CLAIMS.items() if k != "userId"}
        with pytest.raises(HTTPException) as exc:
            await _get_content(claims, _graph_provider(_record()))
        assert exc.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_record_not_found_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            await _get_content(_RECORD_CONTENT_CLAIMS, _graph_provider(None))
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_other_org_record_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            await _get_content(_RECORD_CONTENT_CLAIMS, _graph_provider(_record(org_id="org-2")))
        assert exc.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_acl_denied_raises_403(self):
        graph_provider = _graph_provider(_record(), access=False)
        with pytest.raises(HTTPException) as exc:
            await _get_content(_RECORD_CONTENT_CLAIMS, graph_provider)
        assert exc.value.status_code == HttpStatusCode.FORBIDDEN.value
        graph_provider.check_record_access_with_details.assert_awaited_once_with(
            "user-1", "org-1", "rec-1"
        )

    async def test_allowed_read_runs_as_the_requesting_user(self):
        with patch(
            f"{_ROUTER}._resolve_record_content_response",
            new_callable=AsyncMock,
            return_value=Response(content=b"ok"),
        ) as mock_resolve:
            result = await _get_content(_RECORD_CONTENT_CLAIMS, _graph_provider(_record()))

        assert result.body == b"ok"
        kwargs = mock_resolve.await_args.kwargs
        assert kwargs["org_id"] == "org-1"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["is_admin"] is False


def _request_with_user(user):
    request = MagicMock()
    request.state.user = user
    return request


class TestCallerOrgAndUser:
    def test_indexing_service_token_may_omit_user(self):
        user = {"token_type": "scoped", "orgId": "org-1", "scopes": ["connector:signedUrl"]}
        assert _caller_org_and_user(_request_with_user(user)) == ("org-1", "", True)

    def test_other_service_scope_without_user_raises_401(self):
        user = {"token_type": "scoped", "orgId": "org-1", "scopes": ["fetch:config"]}
        with pytest.raises(HTTPException) as exc:
            _caller_org_and_user(_request_with_user(user))
        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    def test_oauth_scopes_do_not_make_a_caller_a_service(self):
        """An OAuth token listing connector:signedUrl is still a user token."""
        user = {
            "token_type": "regular",
            "orgId": "org-1",
            "isOAuth": True,
            "oauthScopes": ["connector:signedUrl"],
        }
        with pytest.raises(HTTPException) as exc:
            _caller_org_and_user(_request_with_user(user))
        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    def test_session_user(self):
        user = {"token_type": "regular", "orgId": "org-1", "userId": "user-1"}
        assert _caller_org_and_user(_request_with_user(user)) == ("org-1", "user-1", False)

    def test_session_user_with_service_claims_is_not_a_service(self):
        user = {
            "token_type": "regular",
            "orgId": "org-1",
            "userId": "user-1",
            "scopes": ["connector:signedUrl"],
        }
        assert _caller_org_and_user(_request_with_user(user)) == ("org-1", "user-1", False)
