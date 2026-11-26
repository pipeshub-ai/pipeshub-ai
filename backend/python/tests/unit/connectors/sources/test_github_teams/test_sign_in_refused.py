"""A GitHub sign-in that GitHub refuses, and that cannot be refreshed, is an error, and the owner is told to reconnect.

Each hourly sync failed and told nobody."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.sources.github.connector import GithubConnector
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.github_teams.constants import _GITHUB_MAX_RETRIES
from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.connectors.sources.github_teams.users import UsersSync
from app.services.notification.types import NotificationType
from app.sources.client.github.github import GitHubResponse
from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector

pytestmark = pytest.mark.anyio

REFUSED = GitHubResponse(success=False, error="401 Bad credentials", status_code=401)


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _runtime(config: dict) -> tuple[MagicMock, RuntimeHelper]:
    c = make_mock_connector()
    c.config_service.get_config = AsyncMock(return_value=config)
    return c, RuntimeHelper(c)


_SERVICE = "app.connectors.core.base.token_service.startup_service.startup_service.get_token_refresh_service"


async def _call(runtime: RuntimeHelper, *answers: GitHubResponse, service: object = None) -> GitHubResponse:
    with patch(_SERVICE, return_value=MagicMock(refresh_now=AsyncMock()) if service is None else service):
        return await runtime.ds_call(AsyncMock(side_effect=answers, __name__="op"))


def _logged(c: MagicMock) -> str:
    return " ".join(str(call.args) for call in c.logger.error.call_args_list)


@pytest.mark.parametrize("config", [
    {"auth": {"authType": "OAUTH"}, "credentials": {}},
    {"auth": {"authType": "API_TOKEN"}, "credentials": {}},
])
async def test_a_refusal_that_cannot_be_refreshed_is_remembered_and_logged_with_githubs_reason(config: dict) -> None:
    c, runtime = _runtime(config)

    await _call(runtime, REFUSED)

    assert runtime.sign_in_refused is True
    assert "401 Bad credentials" in _logged(c) and "revoked, expired or wrong" in _logged(c)


async def test_a_refused_api_token_is_refused_even_without_the_refresh_service() -> None:
    """Found by the GitHub simulator, which runs no refresh service: an API token is never refreshed anyway."""
    _c, runtime = _runtime({"auth": {"authType": "API_TOKEN"}, "credentials": {}})
    await _call(runtime, REFUSED, service=False)
    assert runtime.sign_in_refused is True


async def test_a_rejected_refresh_token_is_a_refused_sign_in() -> None:
    _c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    rejected = MagicMock(refresh_now=AsyncMock(side_effect=RefreshTokenInvalidError("invalid_grant")))
    await _call(runtime, REFUSED, service=rejected)
    assert runtime.sign_in_refused is True


@pytest.mark.parametrize("service", [
    False,  # the refresh service is not running
    MagicMock(refresh_now=AsyncMock(side_effect=ConnectionError("token endpoint unreachable"))),
    MagicMock(refresh_now=AsyncMock(side_effect=RuntimeError("502 from GitHub's token endpoint"))),
], ids=["service-unavailable", "network-error", "token-endpoint-5xx"])
async def test_a_refresh_that_fails_for_a_passing_reason_is_not_a_refused_sign_in(service: object) -> None:
    _c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    answer = await _call(runtime, REFUSED, service=service)
    assert answer is REFUSED
    assert runtime.sign_in_refused is False


async def test_an_unreadable_config_is_not_a_refused_sign_in() -> None:
    _c, runtime = _runtime({})
    await _call(runtime, REFUSED)
    assert runtime.sign_in_refused is False


async def test_a_refusal_after_a_refresh_is_remembered() -> None:
    _c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    await _call(runtime, REFUSED, REFUSED)
    assert runtime.sign_in_refused is True


@pytest.mark.parametrize(("last", "refused"), [(REFUSED, True), (GitHubResponse(success=True, data="x"), False)])
async def test_a_refusal_on_the_last_attempt_is_remembered(last: GitHubResponse, refused: bool) -> None:
    _c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    unavailable = GitHubResponse(success=False, error="502", status_code=502)
    with patch("app.connectors.sources.github_teams.runtime.asyncio.sleep", AsyncMock()):
        await _call(runtime, *[unavailable] * (_GITHUB_MAX_RETRIES - 1), REFUSED, last)
    assert runtime.sign_in_refused is refused


@pytest.mark.parametrize("answer", [
    GitHubResponse(success=True, data="x"),
    GitHubResponse(success=False, error="403 Forbidden", status_code=403),
    GitHubResponse(success=False, error="Not Found", status_code=404),
])
async def test_another_answer_is_not_a_refused_sign_in(answer: GitHubResponse) -> None:
    _c, runtime = _runtime({"credentials": {}})
    await _call(runtime, answer)
    assert runtime.sign_in_refused is False


def _sync(error: Exception | None, *, refused: bool) -> MagicMock:
    c = make_mock_connector()
    c.notify = AsyncMock()
    c._tell_to_reconnect = lambda: GitHubTeamsConnector._tell_to_reconnect(c)
    c._stop_if_sign_in_refused = lambda e: GitHubTeamsConnector._stop_if_sign_in_refused(c, e)
    c.repos.timestamps.cancel = AsyncMock()
    c.runtime.refresh_token_if_needed = AsyncMock()
    c.runtime.sign_in_refused = True  # left by an earlier run

    async def users() -> None:
        assert c.runtime.sign_in_refused is False, "each run starts afresh"
        c.runtime.sign_in_refused = refused
        if error:
            raise error

    c.users.sync_users = users
    c.projects.sync_all_repos = AsyncMock()
    c.projects.failures = []
    return c


async def _run(c: MagicMock) -> None:
    with patch("app.connectors.sources.github_teams.connector.load_connector_filters", AsyncMock(return_value=({}, {}))):
        await GitHubTeamsConnector.run_sync(c)


async def test_the_owner_is_told_to_reconnect_and_the_run_still_fails() -> None:
    c = _sync(RuntimeError("every configured org failed"), refused=True)

    with pytest.raises(ConnectorInitError, match="Reconnect") as raised:
        await _run(c)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert any("sync stopped" in str(call.args[0]) for call in c.logger.error.call_args_list)
    assert c.notify.await_args.kwargs["type"] is NotificationType.CONNECTOR_AUTH_ERROR
    # Repositories that left the scope may already have been removed in this run.
    assert "removed" not in c.notify.await_args.kwargs["message"]


async def test_another_failure_is_logged_and_raised_as_before() -> None:
    c = _sync(RuntimeError("GitHub is down"), refused=False)

    with pytest.raises(RuntimeError, match="GitHub is down"):
        await _run(c)

    c.logger.error.assert_called_once()
    c.notify.assert_not_awaited()


async def test_a_notice_that_cannot_be_sent_does_not_hide_the_reason() -> None:
    c = _sync(RuntimeError("x"), refused=True)
    c.notify = AsyncMock(side_effect=RuntimeError("broker down"))
    with pytest.raises(ConnectorInitError, match="Reconnect"):
        await _run(c)


async def test_the_message_also_fits_a_connector_signed_in_with_a_token() -> None:
    c = _sync(RuntimeError("x"), refused=True)
    with pytest.raises(ConnectorInitError, match="replace its token"):
        await _run(c)


def _personal_sync(*, refused: bool) -> MagicMock:
    c = make_mock_connector()
    c.notify = AsyncMock()
    c._tell_to_reconnect = lambda: GitHubTeamsConnector._tell_to_reconnect(c)
    c._stop_if_sign_in_refused = lambda e: GitHubTeamsConnector._stop_if_sign_in_refused(c, e)
    c.repos.timestamps.cancel = AsyncMock()
    c.runtime.refresh_token_if_needed = AsyncMock()
    c.runtime.sign_in_refused = True  # left by an earlier run
    c.creator_email = "owner@example.com"
    c.ensure_creator_user_app_relation = AsyncMock()

    async def repos() -> None:
        assert c.runtime.sign_in_refused is False, "each run starts afresh"
        c.runtime.sign_in_refused = refused
        raise RuntimeError("no repository could be listed")

    c.projects.sync_all_repos = repos
    return c


@pytest.mark.parametrize("refused", [True, False])
async def test_the_personal_connector_tells_the_owner_to_reconnect_only_on_a_refused_sign_in(refused: bool) -> None:
    c = _personal_sync(refused=refused)
    with patch("app.connectors.sources.github.connector.load_connector_filters", AsyncMock(return_value=({}, {}))):
        with pytest.raises(ConnectorInitError if refused else RuntimeError, match="Reconnect" if refused else "listed"):
            await GithubConnector.run_sync(c)
    if refused:
        assert c.notify.await_args.kwargs["type"] is NotificationType.CONNECTOR_AUTH_ERROR
    else:
        c.notify.assert_not_awaited()


async def test_a_run_that_works_again_is_not_judged_by_the_last_one() -> None:
    c = _sync(None, refused=False)
    await _run(c)
    c.notify.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 500])
async def test_a_failed_member_listing_is_an_error(status: int) -> None:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=GitHubResponse(success=False, error="no", status_code=status))
    users = UsersSync(c)

    _found, any_success = await users._discover_principals(["acme"])

    assert any_success is False
    c.logger.error.assert_called_once()
