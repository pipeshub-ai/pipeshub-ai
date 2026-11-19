"""Connector-checklist behaviour of the Slack Workspace connector, over a fake Slack workspace.

Covers how the connector runs Slack calls and waits out rate limits, tells the
user about a refused token or channels it cannot read, keeps what it could not
read, and keeps record revisions and conversation streaming exact.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any

import pytest
from slack_behaviour_fakes import (
    FakeCheckpoints,
    FakeConfigService,
    FakeSlackStore,
    HttpFailure,
    RateLimited,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    CAROL,
    GENERAL,
    SECRET,
    WORKSPACE_CONNECTOR_ID,
    standard_workspace,
    workspace_config,
    workspace_connector,
)

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.sources.slack.common.token_renewal import (
    PASTED_ROTATING_TOKEN_MESSAGE,
    SlackTokenRenewal,
)
from app.connectors.sources.slack.team.connector import (
    REQUIRED_BOT_SCOPES,
    SlackConnector,
    SlackTokenRefusedError,
)
from app.models.entities import MessageRecord
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.notification.types import NotificationType
from app.utils import error_class

CONNECTOR_LOGGER = "test.slack_workspace"

# Slack's documented answers for a token, or its grant, that is no good (HTTP 200, ok: false).
REFUSED_TOKEN_CODES = (
    "invalid_auth", "not_authed", "token_revoked", "token_expired",
    "account_inactive", "team_access_not_granted", "ekm_access_denied",
)


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


def record_notices(connector: SlackConnector) -> list[dict[str, Any]]:
    notices: list[dict[str, Any]] = []

    async def _notify(**kwargs: Any) -> None:  # noqa: ANN401 - notify's keywords
        notices.append(kwargs)

    connector.notify = _notify  # type: ignore[method-assign]
    return notices


def general_messages(store: FakeSlackStore) -> list[MessageRecord]:
    return [r for r in store.messages() if r.external_record_group_id == GENERAL]


async def body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class TestSlackCalls:
    async def test_slack_calls_run_off_the_event_loop(self, workspace, store, checkpoints) -> None:
        loop_thread = threading.get_ident()
        threads: list[int] = []
        workspace.on_call("users.list", lambda _: threads.append(threading.get_ident()))
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert threads, "users.list was called"
        assert loop_thread not in threads

    async def test_a_429_that_never_clears_leaves_the_channel_checkpoint_alone(
        self, workspace, store, checkpoints, waits,
    ) -> None:
        workspace.post(GENERAL, ts_minutes_ago(10), ALICE, "throttled")
        workspace.fail("conversations.history", RateLimited(5), times=None, when=lambda p: p.get("channel") == GENERAL)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert checkpoints.value(f"slack_messages/{GENERAL}") is None
        assert checkpoints.value(f"thread_growth/{GENERAL}") is None
        # The message pass and the thread-growth pass each retry three times.
        assert len([w for w in waits if 5 <= w <= 5.25]) == 6
        assert connector.full_sync_coverage().complete is False


class TestRefusedToken:
    async def test_a_refused_token_stops_the_sync_and_tells_the_user(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)
        workspace.valid_tokens = set()

        with pytest.raises(SlackTokenRefusedError):
            await connector.run_sync()

        assert [n["type"] for n in notices] == [NotificationType.CONNECTOR_AUTH_ERROR]
        assert "Bot User OAuth Token" in notices[0]["message"]

    async def test_a_token_refused_while_reading_channels_stops_the_sync_with_one_notice(
        self, workspace, store, checkpoints,
    ) -> None:
        workspace.post(GENERAL, ts_minutes_ago(10), ALICE, "hello")
        workspace.fail("conversations.history", SlackError("token_revoked"), times=None)
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)

        with pytest.raises(SlackTokenRefusedError):
            await connector.run_sync()

        assert [n["type"] for n in notices] == [NotificationType.CONNECTOR_AUTH_ERROR]
        assert not workspace.calls_to("conversations.replies")
        assert checkpoints.value(f"slack_messages/{GENERAL}") is None

    async def test_a_refused_token_fails_setup_with_a_next_step(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)
        workspace.valid_tokens = set()

        with pytest.raises(ConnectorInitError, match="Reinstall the Slack app"):
            await connector.init()

    async def test_the_connection_test_names_the_scopes_the_token_lacks(self, workspace, store, checkpoints) -> None:
        workspace.granted_scopes = set(REQUIRED_BOT_SCOPES) - {"files:read", "users:read.email"}
        connector, _ = await workspace_connector(store, checkpoints)

        with pytest.raises(ConnectorInitError) as err:
            await connector.test_connection_and_access()

        assert "users:read.email, files:read" in str(err.value)

    async def test_the_connection_test_passes_with_the_required_scopes(self, workspace, store, checkpoints) -> None:
        workspace.granted_scopes = set(REQUIRED_BOT_SCOPES)
        connector, _ = await workspace_connector(store, checkpoints)

        assert await connector.test_connection_and_access() is True


def unstarted_connector(
    store: FakeSlackStore, checkpoints: FakeCheckpoints, config: dict[str, Any] | None = None,
) -> SlackConnector:
    """A workspace connector before its first init(), as the factory and the turn-on route build it."""
    return SlackConnector(
        logging.getLogger(CONNECTOR_LOGGER), store, checkpoints,
        FakeConfigService(WORKSPACE_CONNECTOR_ID, config or workspace_config()), WORKSPACE_CONNECTOR_ID,
        "team", "creator-1",
    )


ROTATING_ACCESS = "xoxe.xoxb-1-rotating-access"


def rotating_oauth_config() -> dict[str, Any]:
    """A workspace connector signed in through Slack OAuth with token rotation on, its token not yet due."""
    return {
        "auth": {"authType": "OAUTH", "oauthConfigId": "slack-app-1"},
        "credentials": {
            "access_token": ROTATING_ACCESS, "refresh_token": "xoxe-1-refresh", "expires_in": 43200,
            "created_at": datetime.now().isoformat(),  # noqa: DTZ005 - OAuthToken keeps naive local times
        },
    }


class FailingRefreshService:
    """The shared token refresh service, failing every refresh with ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def refresh_now(self, *_: object, **__: object) -> None:
        self.calls += 1
        raise self.error


def connector_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        f"{r.levelname} {r.getMessage()}" for r in caplog.records
        if r.name == CONNECTOR_LOGGER and r.levelno >= logging.WARNING
    ]


def assert_expired_sign_in(err: BaseException) -> None:
    assert isinstance(err, SlackTokenRefusedError)
    assert isinstance(err, ConnectorInitError)  # the turn-on route still shows its text as a 400
    assert error_class.classify_error(err) == "auth_expired"
    assert error_class.level_for(error_class.classify_error(err)) == logging.WARNING
    assert MessageErrorClassifier.classify_by_exception(err) == MessageErrorType.TERMINAL


class TestARefusedTokenIsAnExpiredSignIn:
    """Slack sends no HTTP status for a refused token, so the error's type must say it is not an outage."""

    @pytest.mark.parametrize("code", REFUSED_TOKEN_CODES)
    async def test_setup_fails_once_as_an_expired_sign_in_with_no_line_of_its_own(
        self, workspace, store, checkpoints, caplog, code,
    ) -> None:
        connector = unstarted_connector(store, checkpoints)
        workspace.fail("team.info", SlackError(code), times=None)

        with caplog.at_level(logging.INFO), pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert_expired_sign_in(err.value)
        if code == "token_expired":
            assert str(err.value).endswith(PASTED_ROTATING_TOKEN_MESSAGE)
        else:
            assert f"({code})" in str(err.value)
        assert err.value.__cause__ is None
        assert connector_lines(caplog) == []  # the caller that handles it logs it, once
        assert len(workspace.calls_to("team.info")) == 1

    async def test_a_token_refused_by_the_auth_test_fallback_is_the_same(
        self, workspace, store, checkpoints, caplog,
    ) -> None:
        connector = unstarted_connector(store, checkpoints)
        workspace.fail("team.info", SlackError("missing_scope"), times=None)
        workspace.fail("auth.test", SlackError("token_revoked"), times=None)

        with caplog.at_level(logging.INFO), pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert_expired_sign_in(err.value)
        assert [line.split(" ", 1)[0] for line in connector_lines(caplog)] == ["WARNING"]  # team.info's fallback notice

    async def test_a_token_refused_during_a_sync_is_an_expired_sign_in_with_no_line_of_its_own(
        self, workspace, store, checkpoints, caplog,
    ) -> None:
        connector = unstarted_connector(store, checkpoints)  # logs as CONNECTOR_LOGGER, so the no-line check bites
        assert await connector.init() is True
        notices = record_notices(connector)
        workspace.valid_tokens = set()

        with caplog.at_level(logging.INFO), pytest.raises(SlackTokenRefusedError) as err:
            await connector.run_sync()

        assert_expired_sign_in(err.value)
        assert connector_lines(caplog) == []  # sync_run.end logs it; the user is notified
        assert [n["type"] for n in notices] == [NotificationType.CONNECTOR_AUTH_ERROR]

    @pytest.mark.parametrize(
        "reply",
        [SlackError("internal_error"), SlackError("fatal_error"), HttpFailure(503), RateLimited(1)],
        ids=["internal_error", "fatal_error", "http_503", "ratelimited"],
    )
    async def test_a_slack_side_failure_still_does_not_fail_setup(
        self, workspace, store, checkpoints, reply,
    ) -> None:
        connector = unstarted_connector(store, checkpoints)
        workspace.fail("team.info", reply, times=None)
        workspace.fail("auth.test", reply, times=None)

        assert await connector.init() is True

    @pytest.mark.parametrize(
        "refresh_service",
        [None, FailingRefreshService(TimeoutError("oauth.v2.access timed out"))],
        ids=["refresh-service-stopped", "refresh-timed-out"],
    )
    async def test_a_rotating_token_whose_renewal_failed_for_now_stays_retryable(
        self, workspace, store, checkpoints, monkeypatch, refresh_service,
    ) -> None:
        monkeypatch.setattr(startup_service, "_token_refresh_service", refresh_service)
        workspace.expired_tokens.add(ROTATING_ACCESS)
        connector = unstarted_connector(store, checkpoints, rotating_oauth_config())

        with pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert not isinstance(err.value, SlackTokenRefusedError)
        assert MessageErrorClassifier.classify_by_exception(err.value) != MessageErrorType.TERMINAL

    async def test_a_rotating_token_whose_renewal_failed_for_now_does_not_stop_a_sync_as_refused(
        self, workspace, store, checkpoints, monkeypatch,
    ) -> None:
        workspace.valid_tokens.add(ROTATING_ACCESS)
        connector = unstarted_connector(store, checkpoints, rotating_oauth_config())
        assert await connector.init() is True
        notices = record_notices(connector)
        monkeypatch.setattr(startup_service, "_token_refresh_service", None)
        workspace.valid_tokens.discard(ROTATING_ACCESS)
        workspace.expired_tokens.add(ROTATING_ACCESS)

        with pytest.raises(ConnectorInitError) as err:
            await connector.run_sync()

        assert not isinstance(err.value, SlackTokenRefusedError)
        assert notices == []

    async def test_a_rotating_token_whose_refresh_token_was_rejected_is_an_expired_sign_in(
        self, workspace, store, checkpoints, monkeypatch,
    ) -> None:
        refresh_service = FailingRefreshService(RefreshTokenInvalidError("invalid_refresh_token"))
        monkeypatch.setattr(startup_service, "_token_refresh_service", refresh_service)
        workspace.expired_tokens.add(ROTATING_ACCESS)
        connector = unstarted_connector(store, checkpoints, rotating_oauth_config())

        with pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert_expired_sign_in(err.value)
        assert refresh_service.calls == 1

    async def test_a_malformed_token_is_still_a_plain_setup_error(self, workspace, store, checkpoints) -> None:
        connector, config_service = await workspace_connector(store, checkpoints)
        config_service.config = workspace_config(token="not-a-slack-token")

        with pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert not isinstance(err.value, SlackTokenRefusedError)
        assert isinstance(err.value.__cause__, ValueError)

    async def test_concurrent_renewals_each_read_their_own_outcome(self, monkeypatch) -> None:
        """A's refresh times out while B waits on the lock; B's then finds the refresh token rejected."""
        a_refreshing, release_a = asyncio.Event(), asyncio.Event()

        class _RefreshService:
            calls = 0

            async def refresh_now(self, *_: object, **__: object) -> None:
                self.calls += 1
                if self.calls == 1:
                    a_refreshing.set()
                    await release_a.wait()
                    raise TimeoutError("oauth.v2.access timed out")
                raise RefreshTokenInvalidError("invalid_refresh_token")

        refresh_service = _RefreshService()
        monkeypatch.setattr(startup_service, "_token_refresh_service", refresh_service)
        renewal = SlackTokenRenewal(
            WORKSPACE_CONNECTOR_ID, "Slack", FakeConfigService(WORKSPACE_CONNECTOR_ID, rotating_oauth_config()),
            logging.getLogger(CONNECTOR_LOGGER),
        )

        async def renew_and_read() -> bool:
            assert await renewal.renew(ROTATING_ACCESS, "token_expired") is None
            return renewal.refresh_failed_transiently

        a = asyncio.create_task(renew_and_read())
        await a_refreshing.wait()
        b = asyncio.create_task(renew_and_read())
        for _ in range(5):  # B resets its outcome and queues on the lock
            await asyncio.sleep(0)
        release_a.set()

        assert await asyncio.gather(a, b) == [True, False]
        assert refresh_service.calls == 2


class TestUnreadParts:
    async def test_a_channel_the_app_cannot_read_is_named_in_one_notice(self, workspace, store, checkpoints) -> None:
        workspace.channels[SECRET]["is_member"] = False
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)

        await connector.run_sync()

        assert len(notices) == 1
        assert notices[0]["type"] == NotificationType.CONNECTOR_SYNC_ERROR
        assert "#secret" in notices[0]["message"]

    async def test_an_archived_channel_the_app_is_not_in_gets_its_own_notice(self, workspace, store, checkpoints) -> None:
        workspace.channels[SECRET]["is_member"] = False
        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)

        await connector.run_sync()

        assert [n["message"] for n in notices if "#secret" in n["message"]] == [
            f"{connector.display_name}: the Slack app cannot read #secret. Invite the app to these channels "
            f"(/invite in the channel), or add the channels:join scope for public channels."
        ]
        assert [n["message"] for n in notices if "#general" in n["message"]] == [
            "These channels are archived and the Slack app is not a member: #general. Slack does not allow "
            "joining an archived channel, so their messages are not synced. Unarchive the channel and invite "
            "the app (/invite), then sync again, or ignore this notice."
        ]
        assert len(notices) == 2

    async def test_a_channel_list_that_fails_fails_the_sync_and_tells_the_user(
        self, workspace, store, checkpoints,
    ) -> None:
        workspace.fail("conversations.list", SlackError("internal_error"))
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)

        with pytest.raises(RuntimeError, match="conversations.list"):
            await connector.run_sync()

        assert any("could not be listed" in n["title"] for n in notices)

    async def test_a_failed_member_read_keeps_a_user_groups_members(self, workspace, store, checkpoints) -> None:
        workspace.usergroups.append({"id": "S0ONCALL", "name": "oncall", "handle": "oncall", "users": []})
        group = workspace.usergroups[0]
        connector, _ = await workspace_connector(store, checkpoints)
        group_users = [ALICE, BOB]

        def _members(params: dict[str, str]) -> None:
            group["users"] = group_users

        # usergroups.list leaves users out; the member listing fills them in.
        workspace.on_call("usergroups.users.list", _members)
        listed = workspace._api_usergroups_list

        def _list_without_users(params: dict[str, str]) -> dict[str, Any]:
            reply = listed(params)
            return {**reply, "usergroups": [{**g, "users": []} for g in reply["usergroups"]]}

        workspace._api_usergroups_list = _list_without_users  # type: ignore[method-assign]
        await connector.run_sync()
        stored = [u.email for u in store.user_groups["S0ONCALL"][1]]
        assert stored == ["alice@acme.com", "bob@acme.com"]

        workspace.fail("usergroups.users.list", SlackError("internal_error"))
        await connector.run_sync()

        assert [u.email for u in store.user_groups["S0ONCALL"][1]] == stored
        assert connector.full_sync_coverage().complete is False

    async def test_guests_are_stored_as_guests(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.app_users[CAROL].is_guest is True
        assert store.app_users[ALICE].is_guest is False


class TestRevisions:
    async def test_a_reread_message_keeps_its_revision_and_an_edit_changes_it(
        self, workspace, store, checkpoints,
    ) -> None:
        ts = ts_minutes_ago(30)
        msg = workspace.post(GENERAL, ts, ALICE, "first draft")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        first = store.records[ts].external_revision_id
        assert store.records[ts].version == 0

        # A full sync drops the checkpoints and reads the window again.
        checkpoints.sync_points.clear()
        await connector.run_sync()
        assert store.records[ts].external_revision_id == first

        msg["text"] = "final text"
        msg["edited"] = {"user": ALICE, "ts": ts_minutes_ago(1)}
        checkpoints.sync_points.clear()
        await connector.run_sync()
        assert store.records[ts].external_revision_id != first


class TestThreads:
    async def test_a_thread_with_no_new_replies_is_not_read_again(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(120)
        workspace.post(GENERAL, parent, ALICE, "question")
        workspace.reply(GENERAL, parent, ts_minutes_ago(110), BOB, "answer")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()
        reads = len(workspace.calls_to("conversations.replies"))
        await connector.run_sync()

        assert len(workspace.calls_to("conversations.replies")) == reads


class TestStreaming:
    async def test_a_conversation_longer_than_one_page_streams_every_message(
        self, workspace, store, checkpoints,
    ) -> None:
        stamps = [ts_minutes_ago(m) for m in (30, 29, 28)]
        for n, ts in enumerate(stamps):
            workspace.post(GENERAL, ts, ALICE, f"message {n}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (burst,) = general_messages(store)
        workspace.page_size["conversations.history"] = 1

        blocks = json.loads(await body(await connector.stream_record(burst)))["blocks"]

        assert [b["source_id"] for b in blocks] == stamps


class TestReindex:
    async def test_a_record_whose_check_fails_is_still_reindexed(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(10), ALICE, "hello")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        record = general_messages(store)[0]

        async def _fails(_rec: object) -> None:
            raise RuntimeError("lookup failed")

        connector._check_updated = _fails  # type: ignore[method-assign]
        await connector.reindex_records([record])

        assert store.reindexed == [record]
