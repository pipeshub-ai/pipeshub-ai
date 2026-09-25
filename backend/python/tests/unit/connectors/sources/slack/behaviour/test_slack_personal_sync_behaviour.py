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

from app.models.entities import FileRecord, ProgressStatus
from app.models.permission import EntityType

DM_BOB, DM_DAVE, GROUP_DM, RANDOM = "D0BOB", "D0DAVE", "G0MPIM", "C0RANDOM"


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


async def body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class TestWhatThePersonSees:
    async def test_each_conversation_the_person_is_in_is_shared_with_them_alone(self, workspace, store, checkpoints) -> None:
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert set(store.record_groups) == {GENERAL, SECRET, PARTNERS, DM_BOB, GROUP_DM}
        for channel in store.record_groups:
            (grant,) = store.group_access[channel]
            assert (grant.entity_type, grant.email) == (EntityType.USER, "alice@acme.com")
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
        assert set(labels) == {GENERAL, SECRET, PARTNERS, DM_BOB, GROUP_DM}
        assert (labels[GENERAL], labels[DM_BOB]) == ("general", "DM: Bob")
        assert labels[GROUP_DM] == store.record_groups[GROUP_DM].name


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

        assert store.access_emails(DM_BOB) == {"alice@acme.com"}

    async def test_the_persons_own_token_is_preferred_over_a_bot_token(self, workspace, store, checkpoints) -> None:
        config = personal_config()
        config["auth"]["apiToken"] = BOT_TOKEN
        connector, _ = await personal_connector(store, checkpoints, config)

        await connector.run_sync()

        assert {c.token for c in workspace.calls} == {USER_TOKEN}

    @pytest.mark.xfail(strict=True, reason=(
        "The personal connector prefers any token starting 'xoxp-' and doesn't treat a rotating "
        "'xoxe.xoxp-' token as a user token, so an old classic token left in the config wins over "
        "the newer rotating one."
    ))
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


class TestSyncing:
    @pytest.mark.xfail(strict=True, reason=(
        "When a later page of history fails, the personal connector still moves the checkpoint "
        "to the newest message it saw, so the older messages on the failed pages are never synced."
    ))
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
