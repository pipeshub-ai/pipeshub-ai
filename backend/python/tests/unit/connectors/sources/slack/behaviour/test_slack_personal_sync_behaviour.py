"""The personal Slack connector, driven over a fake Slack workspace with the real SDK client.

It syncs what one person can see (their channels, DMs and group DMs) and
shares every conversation with that person only.
"""

import json

import httpx
import pytest
from fastapi import HTTPException
from slack_behaviour_fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    CAROL,
    GENERAL,
    PARTNERS,
    SECRET,
    boolean_filter,
    personal_config,
    personal_connector,
    standard_workspace,
)
from test_slack_personal_full_sync_coverage_behaviour import KeepingSlackStore

from app.models.entities import FileRecord, MessageRecord, ProgressStatus

DM_BOB, DM_DAVE, GROUP_DM, RANDOM = "D0BOB", "D0DAVE", "G0MPIM", "C0RANDOM"


@pytest.fixture
def store() -> KeepingSlackStore:
    # RANDOM refuses its history, and the connector then asks whether a full sync runs.
    return KeepingSlackStore()


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    standard_workspace(slack)
    slack.auth_user_id = ALICE
    slack.add_user("U0DAVE", "dave@acme.com", "Dave", deleted=True)
    slack.add_channel(RANDOM, "random", members=[BOB], is_member=False)
    slack.add_channel(DM_BOB, "", kind="im", members=[ALICE, BOB], dm_with=BOB)
    slack.add_channel(DM_DAVE, "", kind="im", members=[ALICE, "U0DAVE"], dm_with="U0DAVE")
    slack.add_channel(GROUP_DM, "mpdm-alice--bob--carol-1", kind="mpim", members=[ALICE, BOB, CAROL])
    return slack


def block_ts(store, group: str) -> list[str]:
    return [b.source_id for r in store.messages() if r.external_record_group_id == group for b in r.block_containers.blocks]


def block_ts_of(record: MessageRecord) -> list[str]:
    return [b.source_id for b in record.block_containers.blocks]


async def body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class TestWhatThePersonSees:
    async def test_each_conversation_the_person_is_in_is_shared_with_them_alone(self, workspace, store, checkpoints) -> None:
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert set(store.record_groups) == {GENERAL, SECRET, PARTNERS, RANDOM, DM_BOB, GROUP_DM}
        for channel in store.record_groups:
            assert store.group_access[channel] == [], "conversations inherit the app, which only the person is linked to"
            assert store.record_groups[channel].inherit_permissions is True
        assert store.record_groups[DM_BOB].name == "DM: Bob"
        assert store.record_groups[GROUP_DM].name.startswith("Group DM: ")
        assert {"Bob", "Carol"} <= set(store.record_groups[GROUP_DM].name.removeprefix("Group DM: ").split(", "))
        assert [u.email for u in store.app_users.values()] == ["alice@acme.com"]

    @pytest.mark.xfail(strict=True, reason=(
        "A group DM's name is meant to leave out the person themselves, but their own Slack "
        "handle is added back as raw text when it isn't matched, e.g. 'Group DM: alice, Bob, Carol'."
    ))
    async def test_a_group_dm_is_named_after_the_other_people_in_it(self, workspace, store, checkpoints) -> None:
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert store.record_groups[GROUP_DM].name == "Group DM: Bob, Carol"

    async def test_direct_and_group_messages_are_synced_with_their_threads(self, workspace, store, checkpoints) -> None:
        dm, group, parent = ts_minutes_ago(90), ts_minutes_ago(80), ts_minutes_ago(70)
        workspace.post(DM_BOB, dm, BOB, "lunch?")
        workspace.post(GROUP_DM, group, CAROL, "hi all")
        workspace.post(GROUP_DM, parent, BOB, "plan")
        reply = ts_minutes_ago(60)
        workspace.reply(GROUP_DM, parent, reply, ALICE, "ok")
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert block_ts(store, DM_BOB) == [dm]
        assert sorted(block_ts(store, GROUP_DM)) == sorted([group, parent])
        assert reply in block_ts(store, f"thread_{GROUP_DM}_{parent}")

    async def test_switching_off_direct_messages_keeps_them_out_of_the_index(self, workspace, store, checkpoints) -> None:
        dm, public = ts_minutes_ago(90), ts_minutes_ago(80)
        workspace.post(DM_BOB, dm, BOB, "private chat")
        workspace.post(GENERAL, public, BOB, "public chat")
        config = personal_config(filters={"indexing": {"values": {"direct_messages": boolean_filter(False)}}})
        connector, _ = await personal_connector(store, checkpoints, config)

        await connector.run_sync()

        assert store.records[dm].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert store.records[public].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value

    async def test_the_channel_type_filter_decides_what_is_read(self, workspace, store, checkpoints) -> None:
        config = personal_config(filters={"sync": {"values": {
            "channel_types": {"operator": "in", "value": ["Direct Messages"], "type": "multiselect"},
        }}})
        connector, _ = await personal_connector(store, checkpoints, config)

        await connector.run_sync()

        assert {c.params["types"] for c in workspace.calls_to("conversations.list")} == {"im"}
        assert set(store.record_groups) == {DM_BOB}

    async def test_the_channel_picker_lists_the_persons_conversations_but_not_dms_with_deactivated_people(
        self, workspace, store, checkpoints,
    ) -> None:
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        options = await connector.get_filter_options("channel_ids", limit=100)

        assert options.success is True
        labels = {o.id: o.label for o in options.options}
        assert set(labels) == {GENERAL, SECRET, PARTNERS, RANDOM, DM_BOB, GROUP_DM}
        assert (labels[GENERAL], labels[DM_BOB]) == ("general", "DM: Bob")
        assert labels[GROUP_DM] == store.record_groups[GROUP_DM].name


class TestPublicChannelsThePersonHasNotJoined:
    async def test_an_unjoined_public_channel_is_read_and_stays_the_persons_alone(self, workspace, store, checkpoints) -> None:
        unjoined = ts_minutes_ago(30)
        workspace.post(RANDOM, unjoined, BOB, "open to everyone in the workspace")
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert block_ts(store, RANDOM) == [unjoined]
        assert store.group_access[RANDOM] == []
        assert store.record_groups[RANDOM].inherit_permissions is True
        assert [u.email for u in store.app_users.values()] == ["alice@acme.com"]

    async def test_a_private_channel_the_person_is_not_in_is_still_left_out(self, workspace, store, checkpoints) -> None:
        workspace.add_channel("G0OTHER", "others", kind="private", members=[BOB], is_member=False)
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert "G0OTHER" not in store.record_groups


class TestArchivedChannels:
    async def test_an_archived_channel_is_synced_and_stays_the_persons_alone(self, workspace, store, checkpoints) -> None:
        kept = ts_minutes_ago(30)
        workspace.post(SECRET, kept, BOB, "before archiving")
        workspace.channels[SECRET]["is_archived"] = True
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert not any(c.params.get("exclude_archived") for c in workspace.calls_to("conversations.list"))
        assert block_ts(store, SECRET) == [kept]
        assert store.group_access[SECRET] == []
        assert store.record_groups[SECRET].inherit_permissions is True
        assert SECRET in {o.id for o in (await connector.get_filter_options("channel_ids", limit=100)).options}


class TestAccountAndTokens:
    async def test_the_sync_refuses_to_run_when_the_persons_email_is_unknown(self, workspace, store, checkpoints) -> None:
        del workspace.users[0]["profile"]["email"]
        connector, _ = await personal_connector(store, checkpoints)

        with pytest.raises(RuntimeError, match="email"):
            await connector.run_sync()
        assert store.record_groups == {}

    async def test_the_connector_owners_email_is_used_when_slack_hides_it(self, workspace, store, checkpoints) -> None:
        del workspace.users[0]["profile"]["email"]
        store.creator_email = "alice@acme.com"
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert [u.email for u in store.app_users.values()] == ["alice@acme.com"]
        assert DM_BOB in store.record_groups
        assert store.group_access[DM_BOB] == []

    async def test_the_persons_own_token_is_preferred_over_a_bot_token(self, workspace, store, checkpoints) -> None:
        config = personal_config()
        config["auth"]["apiToken"] = BOT_TOKEN
        connector, _ = await personal_connector(store, checkpoints, config)

        await connector.run_sync()

        assert {c.token for c in workspace.calls} == {USER_TOKEN}

    async def test_a_rotating_user_token_is_preferred_over_an_old_classic_one(self, workspace, store, checkpoints) -> None:
        rotating = "xoxe.xoxp-new"
        workspace.valid_tokens = {rotating, "xoxp-old"}
        config = personal_config(token=rotating)
        config["auth"]["apiToken"] = "xoxp-old"
        connector, _ = await personal_connector(store, checkpoints, config)

        await connector.run_sync()

        assert {c.token for c in workspace.calls} == {rotating}

    async def test_a_rotating_user_token_connects_and_syncs(self, workspace, store, checkpoints) -> None:
        rotating = "xoxe.xoxp-1-rotating-access-token"
        workspace.valid_tokens = {rotating}
        dm = ts_minutes_ago(10)
        workspace.post(DM_BOB, dm, BOB, "hi")
        connector, _ = await personal_connector(store, checkpoints, personal_config(token=rotating))

        await connector.run_sync()

        assert {c.token for c in workspace.calls} == {rotating}
        assert dm in store.records


CHANNELS_ONLY_SCOPES = {"channels:history", "channels:read", "groups:history", "groups:read", "users:read"}


class TestRegroupedMessages:
    """A re-read groups messages into 5-minute records again; a message is never left
    in an earlier record beside the one that now holds it."""

    async def test_a_message_read_again_with_a_newer_one_minutes_later_is_stored_once(self, workspace, store, checkpoints) -> None:
        first, reply, second = ts_minutes_ago(10), ts_minutes_ago(9.5), ts_minutes_ago(8)
        workspace.post(GENERAL, first, BOB, "deploying")
        workspace.reply(GENERAL, first, reply, ALICE, "ok")
        # An unread thread holds the channel checkpoint back, so the next sync reads `first` again.
        workspace.fail("conversations.replies", SlackError("internal_error"), times=None)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert block_ts(store, GENERAL) == [first]

        workspace._faults.clear()
        workspace.post(GENERAL, second, ALICE, "done")
        await connector.run_sync()

        assert [r.start_ts for r in store.messages() if r.external_record_group_id == GENERAL] == [first]
        assert block_ts(store, GENERAL) == [first, second]

    async def test_a_thread_read_in_full_again_keeps_each_reply_once(self, workspace, store, checkpoints) -> None:
        parent, early, late = ts_minutes_ago(30), ts_minutes_ago(29), ts_minutes_ago(28)
        thread = f"thread_{GENERAL}_{parent}"
        workspace.post(GENERAL, parent, BOB, "question")
        workspace.reply(GENERAL, parent, early, ALICE, "answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        (before,) = [r for r in store.messages() if r.external_record_group_id == thread]
        workspace.reply(GENERAL, parent, late, BOB, "thanks")
        await connector.run_sync()

        checkpoints.sync_points.clear()
        await connector.run_sync()

        (after,) = [r for r in store.messages() if r.external_record_group_id == thread]
        assert block_ts(store, thread) == [parent, early, late]
        # Same record, new messages: its revision changes so it is indexed again.
        assert after.external_record_id == before.external_record_id
        assert after.external_revision_id != before.external_revision_id

    async def test_a_burst_read_again_with_a_newer_message_keeps_its_record(self, workspace, store, checkpoints) -> None:
        first, reply, second, third = (ts_minutes_ago(m) for m in (10, 9.8, 9.5, 8))
        workspace.post(GENERAL, first, BOB, "deploying")
        workspace.reply(GENERAL, first, reply, ALICE, "ok")
        workspace.post(GENERAL, second, ALICE, "watching")
        # An unread thread holds the channel checkpoint back, so the next sync reads the burst again.
        workspace.fail("conversations.replies", SlackError("internal_error"), times=None)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        (before,) = [r for r in store.messages() if r.external_record_group_id == GENERAL]

        workspace._faults.clear()
        workspace.post(GENERAL, third, BOB, "done")
        await connector.run_sync()

        (after,) = [r for r in store.messages() if r.external_record_group_id == GENERAL]
        assert block_ts(store, GENERAL) == [first, second, third]
        assert after.external_record_id == before.external_record_id

    async def test_a_reply_regrouped_into_an_earlier_burst_is_stored_once(self, workspace, store, checkpoints) -> None:
        # 4.5 minutes from each side, so the time between reading the clock for each ts cannot split the burst.
        parent, answer, late, between = (ts_minutes_ago(m) for m in (30, 29, 20, 24.5))
        thread = f"thread_{GENERAL}_{parent}"
        workspace.post(GENERAL, parent, BOB, "question")
        workspace.reply(GENERAL, parent, answer, ALICE, "answer")
        workspace.reply(GENERAL, parent, late, BOB, "later")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert len([r for r in store.messages() if r.external_record_group_id == thread]) == 2

        # Fills the gap, so the thread is now one burst.
        workspace.reply(GENERAL, parent, between, ALICE, "in between")
        checkpoints.sync_points.clear()
        await connector.run_sync()

        assert len([r for r in store.messages() if r.external_record_group_id == thread]) == 1
        assert block_ts(store, thread) == [parent, answer, between, late]

    async def test_a_regrouped_burst_the_schema_rejects_keeps_the_records_it_would_replace(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, answer, late, between = (ts_minutes_ago(m) for m in (30, 29, 20, 24.5))
        thread = f"thread_{GENERAL}_{parent}"
        workspace.post(GENERAL, parent, BOB, "question")
        workspace.reply(GENERAL, parent, answer, ALICE, "answer")
        workspace.reply(GENERAL, parent, late, BOB, "later")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        workspace.reply(GENERAL, parent, between, ALICE, "in between")
        # The one burst the thread now is: written, it would replace the record holding `late`.
        store.leaves_unwritten = lambda r: isinstance(r, MessageRecord) and between in block_ts_of(r)
        checkpoints.sync_points.clear()
        await connector.run_sync()

        assert block_ts(store, thread) == [parent, answer, late]

    async def test_a_burst_whose_later_messages_went_unsaved_on_another_page_is_kept(
        self, workspace, store, checkpoints,
    ) -> None:
        first, second, third = (ts_minutes_ago(m) for m in (10, 9, 8))
        for ts, who in ((first, BOB), (second, ALICE), (third, BOB)):
            workspace.post(GENERAL, ts, who, f"message {ts}")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert block_ts(store, GENERAL) == [first, second, third]

        # Read newest-first two at a time: the page holding the later two saves nothing, the
        # page holding the first saves it alone, named as the stored burst's first message.
        workspace.page_size["conversations.history"] = 2
        store.leaves_unwritten = lambda r: isinstance(r, MessageRecord) and second in block_ts_of(r)
        checkpoints.sync_points.clear()
        await connector.run_sync()

        assert {second, third} <= set(block_ts(store, GENERAL))

    async def test_a_burst_is_not_overwritten_by_a_shorter_read_while_its_tail_is_saved_nowhere(
        self, workspace, store, checkpoints,
    ) -> None:
        stamps = [ts_minutes_ago(m) for m in (10, 9, 8, 7)]
        for ts in stamps:
            workspace.post(GENERAL, ts, BOB, f"message {ts}")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert block_ts(store, GENERAL) == stamps

        # Read newest-first two at a time: the page holding the last two saves nothing, and the
        # older page builds the first two under the stored burst's id.
        workspace.page_size["conversations.history"] = 2
        store.leaves_unwritten = lambda r: isinstance(r, MessageRecord) and stamps[2] in block_ts_of(r)
        checkpoints.sync_points.clear()
        await connector.run_sync()

        assert block_ts(store, GENERAL) == stamps

    async def test_a_thread_whose_new_replies_are_read_only_in_part_keeps_what_an_earlier_part_stored(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, answer = ts_minutes_ago(300), ts_minutes_ago(290)
        new = [ts_minutes_ago(m) for m in (5, 4, 3, 2)]
        thread = f"thread_{GENERAL}_{parent}"
        workspace.post(GENERAL, parent, BOB, "question")
        workspace.reply(GENERAL, parent, answer, ALICE, "answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        for i, ts in enumerate(new):
            workspace.reply(GENERAL, parent, ts, ALICE if i % 2 else BOB, f"reply {i}")
        workspace.fail("conversations.replies", SlackError("internal_error"), times=None,
                       when=lambda p: bool(p.get("cursor")))

        async def read_the_new_replies() -> None:
            # Only the thread's sync point stays: the channel is read again, and with it the
            # replies after the thread's sync point.
            checkpoints.sync_points = {k: v for k, v in checkpoints.sync_points.items() if "slack_thread/" in k}
            await connector.run_sync()

        stored = []
        for read_first in (4, 3):  # the parent and three new replies, then two
            workspace.page_size["conversations.replies"] = read_first
            await read_the_new_replies()
            stored.append(set(block_ts(store, thread)))
        assert stored[0] <= stored[1]

        workspace._faults.clear()
        workspace.page_size.pop("conversations.replies")
        await read_the_new_replies()
        assert block_ts(store, thread) == [parent, answer, *new]

    @pytest.mark.parametrize("read_first", [3, 4], ids=["cut_inside_a_burst", "cut_after_its_first_reply"])
    async def test_a_thread_read_only_in_part_keeps_the_replies_it_did_not_read(
        self, workspace, store, checkpoints, read_first,
    ) -> None:
        parent, answer, before, follow, last = (ts_minutes_ago(m) for m in (60, 59, 12, 10, 9))
        thread = f"thread_{GENERAL}_{parent}"
        workspace.post(GENERAL, parent, BOB, "question")
        workspace.reply(GENERAL, parent, answer, ALICE, "answer")
        workspace.reply(GENERAL, parent, follow, ALICE, "follow-up")
        workspace.reply(GENERAL, parent, last, BOB, "last word")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert block_ts(store, thread) == [parent, answer, follow, last]

        if read_first == 4:
            # Joins the follow-up's burst, so the read below ends one reply into it.
            workspace.reply(GENERAL, parent, before, BOB, "one more")
        workspace.page_size["conversations.replies"] = read_first
        workspace.fail("conversations.replies", SlackError("internal_error"), times=None,
                       when=lambda p: bool(p.get("cursor")))
        checkpoints.sync_points.clear()
        await connector.run_sync()

        assert block_ts(store, thread).count(last) == 1


class TestTokenScopes:
    async def test_a_token_without_dm_scopes_still_syncs_its_channels(self, workspace, store, checkpoints, caplog) -> None:
        workspace.granted_scopes = set(CHANNELS_ONLY_SCOPES)
        posted = ts_minutes_ago(10)
        workspace.post(GENERAL, posted, BOB, "hello")
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert set(store.record_groups) == {GENERAL, SECRET, PARTNERS, RANDOM}
        assert posted in store.records
        assert [c.params["types"] for c in workspace.calls_to("conversations.list")] == [
            "public_channel,private_channel",
        ]
        assert "im:read, mpim:read" in caplog.text

    async def test_a_token_that_can_list_none_of_the_selected_types_fails_the_sync(self, workspace, store, checkpoints) -> None:
        workspace.granted_scopes = set(CHANNELS_ONLY_SCOPES)
        dms_only = {"sync": {"values": {"channel_types": {
            "operator": "in", "value": ["Direct Messages", "Group Direct Messages"], "type": "multiselect",
        }}}}
        connector, _ = await personal_connector(store, checkpoints, personal_config(filters=dms_only))

        with pytest.raises(RuntimeError, match="im:read, mpim:read"):
            await connector.run_sync()
        assert workspace.calls_to("conversations.list") == []

    async def test_a_refused_channel_listing_fails_the_sync_and_names_the_scope(self, workspace, store, checkpoints) -> None:
        workspace.granted_scopes = set(CHANNELS_ONLY_SCOPES)
        workspace.scopes_header = False
        connector, _ = await personal_connector(store, checkpoints)

        with pytest.raises(RuntimeError, match=r"missing_scope.*Needed scope: im:read"):
            await connector.run_sync()
        assert store.record_groups == {}


class TestSyncing:
    async def test_a_failed_later_page_does_not_lose_older_messages(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (400, 300, 200, 100)]
        for ts in stamps:
            workspace.post(DM_BOB, ts, BOB, f"note {ts}")
        workspace.page_size["conversations.history"] = 2
        workspace.fail("conversations.history", SlackError("internal_error"), when=lambda p: p.get("cursor") == "page:2")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        await connector.run_sync()

        assert sorted(block_ts(store, DM_BOB)) == sorted(stamps)

    async def test_the_next_sync_reads_only_newer_messages(self, workspace, store, checkpoints) -> None:
        first = ts_minutes_ago(300)
        workspace.post(DM_BOB, first, BOB, "old")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        newer = ts_minutes_ago(5)
        workspace.post(DM_BOB, newer, BOB, "new")
        written = len(store.written_ts())
        await connector.run_sync()

        assert store.written_ts()[written:] == [newer]

    async def test_a_reply_with_a_file_posted_while_syncing_is_caught_by_the_same_run(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(DM_BOB, parent, BOB, "send the doc")
        workspace.reply(DM_BOB, parent, ts_minutes_ago(290), ALICE, "later")
        fd = workspace.add_file("F0DOC", "doc.pdf", b"doc")
        late = ts_minutes_ago(1)

        def reply_during_growth_scan(params: dict[str, str]) -> None:
            if "latest" in params and workspace.replies[(DM_BOB, parent)][-1]["ts"] != late:
                workspace.reply(DM_BOB, parent, late, ALICE, "here", files=[fd])

        workspace.on_call("conversations.history", reply_during_growth_scan)
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        thread = f"thread_{DM_BOB}_{parent}"
        assert late in block_ts(store, thread)
        doc = store.records["F0DOC"]
        assert isinstance(doc, FileRecord)
        assert doc.external_record_group_id == thread


class TestOpening:
    async def test_a_dm_conversation_and_a_thread_are_rebuilt_from_slack(self, workspace, store, checkpoints) -> None:
        first, second, parent = ts_minutes_ago(100), ts_minutes_ago(99), ts_minutes_ago(60)
        fd = workspace.add_file("F0PIC", "pic.png", mimetype="image/png", filetype="png")
        workspace.post(DM_BOB, first, BOB, "look")
        workspace.post(DM_BOB, second, BOB, "this", files=[fd])
        workspace.post(DM_BOB, parent, ALICE, "thread")
        replies = [ts_minutes_ago(50), ts_minutes_ago(49)]
        for ts in replies:
            workspace.reply(DM_BOB, parent, ts, BOB, f"r {ts}", files=[fd] if ts == replies[0] else [])
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        (burst,) = [r for r in store.messages() if r.start_ts == first]
        (thread_burst,) = [r for r in store.messages() if r.start_ts == replies[0]]

        burst_blocks = json.loads(await body(await connector.stream_record(burst)))["blocks"]
        thread_blocks = json.loads(await body(await connector.stream_record(thread_burst)))["blocks"]

        assert [b["source_id"] for b in burst_blocks] == [first, second]
        assert [b["source_id"] for b in thread_blocks] == replies

    async def test_opening_a_file_streams_it_and_explains_failures(self, workspace, store, checkpoints) -> None:
        fd = workspace.add_file("F0OPEN", "open.txt", b"contents", mimetype="text/plain", filetype="text")
        workspace.post(DM_BOB, ts_minutes_ago(30), BOB, "file", files=[fd])
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        record = store.records["F0OPEN"]

        assert await body(await connector.stream_record(record)) == b"contents"

        workspace.downloads[fd["url_private_download"]] = httpx.Response(429, headers={"retry-after": "9"})
        with pytest.raises(HTTPException) as limited:
            await body(await connector.stream_record(record))
        assert (limited.value.status_code, limited.value.headers["Retry-After"]) == (429, "9")

        workspace.downloads[fd["url_private_download"]] = httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>")
        with pytest.raises(HTTPException) as signed_out:
            await body(await connector.stream_record(record))
        assert signed_out.value.status_code == 409
