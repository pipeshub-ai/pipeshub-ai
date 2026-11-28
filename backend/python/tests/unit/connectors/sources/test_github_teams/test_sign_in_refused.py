"""A GitHub sign-in that GitHub refuses, and that cannot be refreshed, is an error, and the owner is told to reconnect.

Each hourly sync failed and told nobody."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.failure_notices import (
    ReconnectRequiredError,
    SyncRunState,
)
from app.connectors.core.base.token_service.oauth_service import (
    OAuthTokenRequestError,
    RefreshTokenInvalidError,
)
from app.connectors.sources.github.connector import GithubConnector
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.github_teams.constants import _GITHUB_MAX_RETRIES
from app.connectors.sources.github_teams.runtime import RefreshOutcome, RuntimeHelper
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
    c.run_state = SyncRunState()
    c.config_service.get_config = AsyncMock(return_value=config)
    # A second call is refused too, so the 401 is the sign-in's and not one endpoint's.
    c.data_source.get_authenticated = AsyncMock(return_value=REFUSED)
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
async def test_a_refusal_that_cannot_be_refreshed_stops_the_run_and_is_logged_with_githubs_reason(config: dict) -> None:
    c, runtime = _runtime(config)

    await _call(runtime, REFUSED)

    assert c.run_state.rejected is not None
    assert "401 Bad credentials" in _logged(c) and "revoked, expired or is wrong" in _logged(c)


async def test_a_refused_api_token_is_refused_even_without_the_refresh_service() -> None:
    """Found by the GitHub simulator, which runs no refresh service: an API token is never refreshed anyway."""
    c, runtime = _runtime({"auth": {"authType": "API_TOKEN"}, "credentials": {}})
    await _call(runtime, REFUSED, service=False)
    assert c.run_state.rejected is not None


def _refused_by_token_endpoint(code: str) -> RefreshTokenInvalidError:
    """As refresh_access_token raises it: the token endpoint's refusal, with its OAuth code, is the cause."""
    try:
        raise RefreshTokenInvalidError(f"refused ({code})") from OAuthTokenRequestError("refused", status=200, error_code=code)
    except RefreshTokenInvalidError as e:
        return e


@pytest.mark.parametrize("error", [
    RefreshTokenInvalidError("invalid_grant"),
    _refused_by_token_endpoint("bad_refresh_token"),
    _refused_by_token_endpoint("invalid_grant"),
], ids=["no-cause", "bad_refresh_token", "invalid_grant"])
async def test_a_rejected_refresh_token_is_a_refused_sign_in(error: RefreshTokenInvalidError) -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    rejected = MagicMock(refresh_now=AsyncMock(side_effect=error))
    await _call(runtime, REFUSED, service=rejected)
    assert "refresh token" in str(c.run_state.rejected)


@pytest.mark.parametrize("code", ["invalid_client", "unauthorized_client", "incorrect_client_credentials"])
async def test_a_refused_oauth_app_is_not_a_refused_sign_in(code: str) -> None:
    """Reconnecting with the same OAuth app cannot help: its client ID or secret has to be fixed."""
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    refused_app = MagicMock(refresh_now=AsyncMock(side_effect=_refused_by_token_endpoint(code)))

    with patch(_SERVICE, return_value=refused_app):
        assert await runtime.force_refresh_oauth_token() is RefreshOutcome.CLIENT_REJECTED
    answer = await _call(runtime, REFUSED, service=refused_app)

    assert answer is REFUSED
    assert c.run_state.rejected is None
    assert "OAuth app" in _logged(c)
    # The owner is still told what to fix, in the run's problems.
    assert any("OAuth app" in problem for problem in c.run_state.problems)


@pytest.mark.parametrize("service", [
    False,  # the refresh service is not running
    MagicMock(refresh_now=AsyncMock(side_effect=ConnectionError("token endpoint unreachable"))),
    MagicMock(refresh_now=AsyncMock(side_effect=RuntimeError("502 from GitHub's token endpoint"))),
], ids=["service-unavailable", "network-error", "token-endpoint-5xx"])
async def test_a_refresh_that_fails_for_a_passing_reason_is_not_a_refused_sign_in(service: object) -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    answer = await _call(runtime, REFUSED, service=service)
    assert answer is REFUSED
    assert c.run_state.rejected is None


async def test_a_refreshed_token_that_could_not_be_installed_is_not_retried_or_refused() -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}})
    c.external_client = MagicMock()
    # The config read after the refresh fails: the client keeps the old token.
    c.config_service.get_config = AsyncMock(side_effect=[
        {"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r"}}, ConnectionError("store down"),
    ])
    answer = await _call(runtime, REFUSED, REFUSED)
    assert answer is REFUSED
    assert c.run_state.rejected is None


async def test_an_unreadable_config_is_not_a_refused_sign_in() -> None:
    c, runtime = _runtime({})
    await _call(runtime, REFUSED)
    assert c.run_state.rejected is None


async def test_a_refusal_after_a_refresh_stops_the_run() -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r", "access_token": "a"}})
    await _call(runtime, REFUSED, REFUSED)
    assert c.run_state.rejected is not None


@pytest.mark.parametrize(("last", "refused"), [(REFUSED, True), (GitHubResponse(success=True, data="x"), False)])
async def test_a_refusal_on_the_last_attempt_stops_the_run(last: GitHubResponse, *, refused: bool) -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r", "access_token": "a"}})
    unavailable = GitHubResponse(success=False, error="502", status_code=502)
    with patch("app.connectors.sources.github_teams.runtime.asyncio.sleep", AsyncMock()):
        await _call(runtime, *[unavailable] * (_GITHUB_MAX_RETRIES - 1), REFUSED, last)
    assert (c.run_state.rejected is not None) is refused


@pytest.mark.parametrize("answer", [
    GitHubResponse(success=True, data="x"),
    GitHubResponse(success=False, error="403 Forbidden", status_code=403),
    GitHubResponse(success=False, error="Not Found", status_code=404),
])
async def test_another_answer_is_not_a_refused_sign_in(answer: GitHubResponse) -> None:
    c, runtime = _runtime({"credentials": {}})
    await _call(runtime, answer)
    assert c.run_state.rejected is None


def _sync(error: Exception | None, *, refused: bool) -> MagicMock:
    c = make_mock_connector()
    c.notify = AsyncMock()
    c.run_state = SyncRunState()
    c.run_state.reject("left by an earlier run")
    c.repos.timestamps.cancel = AsyncMock()
    c.runtime.refresh_token_if_needed = AsyncMock()

    async def users() -> None:
        assert c.run_state.rejected is None, "each run starts afresh"
        if refused:
            c.run_state.reject("GitHub rejected this connector's sign-in. Reconnect it, or replace its token.")
        if error:
            raise error

    c.users.sync_users = users
    c.projects.sync_all_repos = AsyncMock()
    c.projects.failures = []
    return c


async def _run(c: MagicMock, run_sync: object = GitHubTeamsConnector.run_sync) -> None:
    with (
        patch("app.connectors.sources.github_teams.connector.load_connector_filters", AsyncMock(return_value=({}, {}))),
        patch("app.connectors.sources.github.connector.load_connector_filters", AsyncMock(return_value=({}, {}))),
        patch(_SERVICE, return_value=None),
    ):
        await run_sync(c)


def _types(c: MagicMock) -> list[NotificationType]:
    return [call.kwargs["type"] for call in c.notify.await_args_list]


async def test_the_owner_is_told_to_reconnect_once_and_the_run_still_fails() -> None:
    c = _sync(RuntimeError("every configured org failed"), refused=True)

    with pytest.raises(ReconnectRequiredError, match="Reconnect") as raised:
        await _run(c)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]


async def test_another_failure_is_raised_as_before_with_a_sync_failed_notice() -> None:
    c = _sync(RuntimeError("GitHub is down"), refused=False)

    with pytest.raises(RuntimeError, match="GitHub is down"):
        await _run(c)

    assert _types(c) == [NotificationType.CONNECTOR_SYNC_ERROR]


async def test_a_notice_that_cannot_be_sent_does_not_hide_the_reason() -> None:
    c = _sync(RuntimeError("x"), refused=True)
    c.notify = AsyncMock(side_effect=RuntimeError("broker down"))
    with pytest.raises(ReconnectRequiredError, match="Reconnect"):
        await _run(c)


def _personal_sync(*, refused: bool) -> MagicMock:
    c = make_mock_connector()
    c.notify = AsyncMock()
    c.run_state = SyncRunState()
    c.repos.timestamps.cancel = AsyncMock()
    c.runtime.refresh_token_if_needed = AsyncMock()
    c.creator_email = "owner@example.com"
    c.ensure_creator_user_app_relation = AsyncMock()

    async def repos() -> None:
        if refused:
            c.run_state.reject("GitHub rejected this connector's sign-in. Reconnect it, or replace its token.")
        raise RuntimeError("no repository could be listed")

    c.projects.sync_all_repos = repos
    return c


@pytest.mark.parametrize("refused", [True, False])
async def test_the_personal_connector_tells_the_owner_to_reconnect_only_on_a_refused_sign_in(*, refused: bool) -> None:
    c = _personal_sync(refused=refused)
    with pytest.raises(ReconnectRequiredError if refused else RuntimeError, match="Reconnect" if refused else "listed"):
        await _run(c, GithubConnector.run_sync)
    assert _types(c) == [
        NotificationType.CONNECTOR_AUTH_ERROR if refused else NotificationType.CONNECTOR_SYNC_ERROR
    ]


async def test_a_run_that_works_again_is_not_judged_by_the_last_one() -> None:
    c = _sync(None, refused=False)
    await _run(c)
    c.notify.assert_not_awaited()


async def test_a_dead_token_sends_exactly_one_notice_end_to_end() -> None:
    """The real call path: GitHub refuses the token, nothing can renew it, and the step fails on its own too."""
    c = make_mock_connector()
    c.notify = AsyncMock()
    c.run_state = SyncRunState()
    c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": "tok"}})
    c.external_client.get_client.return_value.get_token.return_value = "tok"
    c.data_source.get_authenticated = AsyncMock(return_value=REFUSED)
    c.runtime = RuntimeHelper(c)
    c.repos.timestamps.cancel = AsyncMock()

    async def users() -> None:
        await c.runtime.ds_call(AsyncMock(return_value=REFUSED, __name__="list_org_members"))
        raise RuntimeError("every configured org failed")

    c.users.sync_users = users
    c.projects.sync_all_repos = AsyncMock()

    with pytest.raises(ReconnectRequiredError):
        await _run(c)

    c.projects.sync_all_repos.assert_not_awaited()
    assert _types(c) == [NotificationType.CONNECTOR_AUTH_ERROR]
    assert "replace its token" in c.notify.await_args.kwargs["message"]


@pytest.mark.parametrize("status", [401, 500])
async def test_a_failed_member_listing_is_an_error(status: int) -> None:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=GitHubResponse(success=False, error="no", status_code=status))
    users = UsersSync(c)

    _found, any_success = await users._discover_principals(["acme"])

    assert any_success is False
    c.logger.error.assert_called_once()


async def test_a_token_replaced_while_the_refusal_was_checked_does_not_stop_the_run() -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {}})
    in_use = {"token": "old"}
    c.external_client.get_client.return_value.get_token.side_effect = lambda: in_use["token"]

    async def confirm() -> GitHubResponse:
        in_use["token"] = "new"  # the owner signed in again while the check was in flight
        return REFUSED

    c.data_source.get_authenticated = confirm
    await _call(runtime, REFUSED)
    assert c.run_state.rejected is None


async def test_a_rate_limit_on_the_last_answer_marks_the_run() -> None:
    c, runtime = _runtime({"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "r", "access_token": "a"}})
    unavailable = GitHubResponse(success=False, error="502", status_code=502)
    limited = GitHubResponse(success=False, error="limit", status_code=403, exception_type="RateLimitExceededException")
    with patch("app.connectors.sources.github_teams.runtime.asyncio.sleep", AsyncMock()):
        await _call(runtime, *[unavailable] * (_GITHUB_MAX_RETRIES - 1), REFUSED, limited)
    assert c.run_state.rate_limited is True
    assert c.run_state.rejected is None


async def test_members_are_not_saved_when_a_later_read_finds_the_sign_in_dead() -> None:
    c = make_mock_connector()
    users = UsersSync(c)
    member = MagicMock(id=1, login="octo", email=None)
    users._resolve_target_orgs = AsyncMock(return_value=(["acme"], True))
    users._discover_principals = AsyncMock(return_value=({1: member}, True))
    users._resolve_cached_users = AsyncMock(return_value={})
    users._persist_app_users = AsyncMock()

    async def verified_domains(*_args: object) -> dict[int, str]:
        c.run_state.reject("GitHub rejected this connector's sign-in.")
        return {}

    users._resolve_via_verified_domains = verified_domains
    with pytest.raises(ReconnectRequiredError):
        await users.sync_users()
    users._persist_app_users.assert_not_awaited()


async def test_profile_reads_stop_once_the_sign_in_is_dead() -> None:
    c = make_mock_connector()
    c.run_state.reject("GitHub rejected this connector's sign-in.")
    c.runtime.ds_call = AsyncMock()
    members = {uid: MagicMock(login=f"user{uid}") for uid in range(5)}
    enriched = await UsersSync(c)._enrich_members_with_full_profile(members, set(members))
    c.runtime.ds_call.assert_not_awaited()
    assert enriched == members
