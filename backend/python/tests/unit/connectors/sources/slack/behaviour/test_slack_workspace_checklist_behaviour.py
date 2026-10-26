"""Connector-checklist behaviour of the Slack Workspace connector, over a fake Slack workspace.

Covers how the connector runs Slack calls and waits out rate limits, tells the
user about a refused token or channels it cannot read, keeps what it could not
read, and keeps record revisions and conversation streaming exact.
"""

import json
import threading
from typing import Any

import pytest
from slack_behaviour_fakes import (
    FakeSlackStore,
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
    standard_workspace,
    workspace_connector,
)

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.slack.team.connector import (
    REQUIRED_BOT_SCOPES,
    SlackConnector,
    SlackTokenRefusedError,
)
from app.models.entities import MessageRecord
from app.services.notification.types import NotificationType


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


class TestUnreadParts:
    async def test_a_channel_the_app_cannot_read_is_named_in_one_notice(self, workspace, store, checkpoints) -> None:
        workspace.channels[SECRET]["is_member"] = False
        connector, _ = await workspace_connector(store, checkpoints)
        notices = record_notices(connector)

        await connector.run_sync()

        assert len(notices) == 1
        assert notices[0]["type"] == NotificationType.CONNECTOR_SYNC_ERROR
        assert "#secret" in notices[0]["message"]

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
