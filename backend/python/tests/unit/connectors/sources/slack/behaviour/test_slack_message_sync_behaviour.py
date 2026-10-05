"""Slack Workspace message sync, driven over a fake Slack workspace with the real SDK client.

The connector, ``SlackClient``, ``SlackDataSource`` and ``slack_sdk.WebClient`` are
real; Slack's HTTP answers come from ``SlackWorkspace`` and our databases are
in-memory fakes. Timestamps are relative to now because the connector's first
sync looks back 30 days from the real clock.
"""

import pytest
from slack_behaviour_fakes import (
    HttpFailure,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    GENERAL,
    SECRET,
    standard_workspace,
    workspace_connector,
)

from app.models.entities import MessageRecord, RecordGroupType


def channel_records(store, channel: str) -> list[MessageRecord]:
    return [r for r in store.messages() if r.external_record_group_id == channel]


def thread_records(store, channel: str, thread_ts: str) -> list[MessageRecord]:
    return [r for r in store.messages() if r.external_record_group_id == f"thread_{channel}_{thread_ts}"]


def block_ts(records: list[MessageRecord]) -> list[str]:
    return [b.source_id for r in records for b in r.block_containers.blocks]


def history_calls(slack, channel: str, *, growth_scan: bool = False) -> list:
    """Message-sync reads have no ``latest``; the thread-growth scan always sends one."""
    return [
        c for c in slack.calls_to("conversations.history")
        if c.params.get("channel") == channel and ("latest" in c.params) == growth_scan
    ]


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


class TestIncrementalSync:
    async def test_first_sync_stores_each_message_once_and_checkpoints_the_newest(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (300, 200, 100)]
        for ts in stamps:
            workspace.post(GENERAL, ts, ALICE, f"note {ts}")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert sorted(block_ts(channel_records(store, GENERAL))) == sorted(stamps)
        assert checkpoints.value(f"slack_messages/{GENERAL}")["last_sync_time"] == stamps[-1]

    async def test_the_next_sync_asks_only_for_newer_messages_and_writes_only_those(self, workspace, store, checkpoints) -> None:
        first = ts_minutes_ago(300)
        workspace.post(GENERAL, first, ALICE, "old news")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        written_before = len(store.written_ts())

        newer = [ts_minutes_ago(m) for m in (60, 30)]
        for ts in newer:
            workspace.post(GENERAL, ts, BOB, f"fresh {ts}")
        await connector.run_sync()

        assert history_calls(workspace, GENERAL)[-1].params["oldest"] == first
        assert sorted(store.written_ts()[written_before:]) == sorted(newer)
        assert all(n == 1 for n in store.message_ts_count().values())
        assert checkpoints.value(f"slack_messages/{GENERAL}")["last_sync_time"] == newer[-1]

    async def test_a_sync_with_nothing_new_writes_nothing(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(90), ALICE, "only message")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        batches = len(store.record_batches)

        await connector.run_sync()

        assert len(store.record_batches) == batches

    async def test_messages_older_than_the_sync_window_are_not_fetched(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(60 * 24 * 40), ALICE, "ancient")
        recent = ts_minutes_ago(60)
        workspace.post(GENERAL, recent, ALICE, "recent")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert block_ts(channel_records(store, GENERAL)) == [recent]

    async def test_messages_minutes_apart_become_one_conversation_record(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (30, 28, 27)]
        for ts, user in zip(stamps, (ALICE, BOB, ALICE)):
            workspace.post(GENERAL, ts, user, f"chat {ts}")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        (record,) = channel_records(store, GENERAL)
        assert block_ts([record]) == stamps
        assert set(record.involved_user_source_ids) == {ALICE, BOB}
        assert record.block_containers.blocks[1].data.startswith("**Bob**:")


class TestPagination:
    async def test_history_is_read_to_the_last_page(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (500, 400, 300, 200, 100)]
        for ts in stamps:
            workspace.post(GENERAL, ts, ALICE, f"note {ts}")
        workspace.page_size["conversations.history"] = 2
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert sorted(block_ts(channel_records(store, GENERAL))) == sorted(stamps)
        assert [c.params.get("cursor") for c in history_calls(workspace, GENERAL)] == [None, "page:2", "page:4"]

    async def test_every_page_of_the_channel_list_is_synced(self, workspace, store, checkpoints) -> None:
        workspace.page_size["conversations.list"] = 1
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert {GENERAL, SECRET, "G0PARTNER"} <= set(store.record_groups)

    async def test_thread_replies_are_read_to_the_last_page(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "question")
        replies = [ts_minutes_ago(m) for m in (290, 280, 270, 260, 250)]
        for ts in replies:
            workspace.reply(GENERAL, parent, ts, BOB, f"answer {ts}")
        workspace.page_size["conversations.replies"] = 2
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert sorted(set(block_ts(thread_records(store, GENERAL, parent))) - {parent}) == sorted(replies)


class TestThreads:
    async def test_replies_live_in_a_thread_group_under_their_channel_and_are_stored_once(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "who is on call?")
        replies = [ts_minutes_ago(290), ts_minutes_ago(200)]
        for ts in replies:
            workspace.reply(GENERAL, parent, ts, BOB, f"me {ts}")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        thread = store.record_groups[f"thread_{GENERAL}_{parent}"]
        assert thread.group_type == RecordGroupType.SLACK_THREAD
        assert thread.parent_external_group_id == GENERAL
        assert thread.inherit_permissions is True
        counts = store.message_ts_count()
        assert [counts[ts] for ts in replies] == [1, 1]
        assert block_ts(channel_records(store, GENERAL)) == [parent]
        assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}")["last_reply_ts"] == replies[-1]

    async def test_a_thread_started_after_the_last_sync_arrives_with_its_replies(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(600), ALICE, "before")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        parent = ts_minutes_ago(20)
        workspace.post(GENERAL, parent, BOB, "new thread")
        reply = ts_minutes_ago(10)
        workspace.reply(GENERAL, parent, reply, ALICE, "reply")
        await connector.run_sync()

        assert reply in block_ts(thread_records(store, GENERAL, parent))

    async def test_a_reply_posted_while_the_sync_runs_is_caught_by_the_same_run(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "status?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(290), BOB, "green")
        late = ts_minutes_ago(1)

        def reply_during_growth_scan(params: dict[str, str]) -> None:
            if "latest" in params and workspace.replies[(GENERAL, parent)][-1]["ts"] != late:
                workspace.reply(GENERAL, parent, late, BOB, "now red")

        workspace.on_call("conversations.history", reply_during_growth_scan)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.message_ts_count().get(late) == 1
        assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}")["last_reply_ts"] == late

    @pytest.mark.xfail(strict=True, reason=(
        "A reply to a thread whose first message is older than the previous sync is never "
        "synced: both the message pass and the thread-growth pass only list messages posted "
        "after their last checkpoint, so the old parent (and its new reply) is never seen again."
    ))
    async def test_a_new_reply_to_an_older_thread_arrives_on_the_next_sync(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "incident thread")
        workspace.reply(GENERAL, parent, ts_minutes_ago(290), BOB, "looking")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        follow_up = ts_minutes_ago(1)
        workspace.reply(GENERAL, parent, follow_up, BOB, "fixed")
        await connector.run_sync()

        assert store.message_ts_count().get(follow_up) == 1

    @pytest.mark.xfail(strict=True, reason=(
        "When reading a thread's replies fails, the thread's checkpoint is still moved to its "
        "latest reply and the channel checkpoint moves past the parent, so those replies are never synced."
    ))
    async def test_replies_that_could_not_be_read_arrive_on_the_next_sync(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "question")
        answer = ts_minutes_ago(290)
        workspace.reply(GENERAL, parent, answer, BOB, "answer")
        workspace.fail("conversations.replies", SlackError("internal_error"), times=None)
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace._faults.clear()
        await connector.run_sync()

        assert store.message_ts_count().get(answer) == 1


class TestCheckpoints:
    @pytest.mark.xfail(strict=True, reason=(
        "When a later page of channel history fails, the checkpoint still moves to the newest "
        "message of the first page, so the older messages on the failed pages are never synced."
    ))
    async def test_a_failed_later_page_does_not_move_the_checkpoint_past_unread_messages(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (400, 300, 200, 100)]
        for ts in stamps:
            workspace.post(GENERAL, ts, ALICE, f"note {ts}")
        workspace.page_size["conversations.history"] = 2
        workspace.fail("conversations.history", HttpFailure(500), when=lambda p: p.get("cursor") == "page:2")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        await connector.run_sync()

        assert sorted(block_ts(channel_records(store, GENERAL))) == sorted(stamps)

    async def test_a_failed_first_page_leaves_no_checkpoint_and_the_next_sync_catches_up(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (300, 100)]
        for ts in stamps:
            workspace.post(GENERAL, ts, ALICE, f"note {ts}")
        workspace.fail("conversations.history", SlackError("internal_error"), when=lambda p: p.get("channel") == GENERAL)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()
        assert checkpoints.value(f"slack_messages/{GENERAL}") is None
        assert channel_records(store, GENERAL) == []

        await connector.run_sync()
        assert sorted(block_ts(channel_records(store, GENERAL))) == sorted(stamps)


class TestPartialFailures:
    async def test_one_unreadable_channel_does_not_stop_the_others(self, workspace, store, checkpoints) -> None:
        general_ts, secret_ts = ts_minutes_ago(100), ts_minutes_ago(90)
        workspace.post(GENERAL, general_ts, ALICE, "public")
        workspace.post(SECRET, secret_ts, BOB, "private")
        workspace.fail("conversations.history", HttpFailure(500), times=None, when=lambda p: p.get("channel") == SECRET)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert block_ts(channel_records(store, GENERAL)) == [general_ts]
        assert channel_records(store, SECRET) == []
        assert checkpoints.value(f"slack_messages/{SECRET}") is None

    async def test_one_failing_thread_does_not_stop_the_other_threads(self, workspace, store, checkpoints) -> None:
        broken, healthy = ts_minutes_ago(300), ts_minutes_ago(200)
        for parent in (broken, healthy):
            workspace.post(GENERAL, parent, ALICE, f"thread {parent}")
        workspace.reply(GENERAL, broken, ts_minutes_ago(290), BOB, "lost")
        healthy_reply = ts_minutes_ago(190)
        workspace.reply(GENERAL, healthy, healthy_reply, BOB, "kept")
        workspace.fail("conversations.replies", HttpFailure(500), times=None, when=lambda p: p.get("ts") == broken)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.message_ts_count().get(healthy_reply) == 1

    async def test_a_public_channel_the_bot_is_not_in_is_joined_and_synced(self, workspace, store, checkpoints) -> None:
        workspace.channels[GENERAL]["is_member"] = False
        ts = ts_minutes_ago(50)
        workspace.post(GENERAL, ts, ALICE, "hello bot")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert [c.params["channel"] for c in workspace.calls_to("conversations.join")] == [GENERAL]
        assert block_ts(channel_records(store, GENERAL)) == [ts]

    async def test_a_private_channel_the_bot_cannot_join_is_skipped(self, workspace, store, checkpoints) -> None:
        workspace.channels[SECRET]["is_member"] = False
        workspace.post(SECRET, ts_minutes_ago(50), ALICE, "hidden")
        general_ts = ts_minutes_ago(40)
        workspace.post(GENERAL, general_ts, ALICE, "visible")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert channel_records(store, SECRET) == []
        assert checkpoints.value(f"slack_messages/{SECRET}") is None
        assert block_ts(channel_records(store, GENERAL)) == [general_ts]
