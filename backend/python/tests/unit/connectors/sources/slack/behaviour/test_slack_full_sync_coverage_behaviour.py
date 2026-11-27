"""What a Slack Workspace full sync leaves linked, driven over the fake workspace.

A full sync resets every sync point, runs ``run_sync``, then the runner unlinks
each stored node the sync neither wrote nor noted kept, unless
``full_sync_coverage`` says the read was incomplete or the node is outside the
sync window (``split_unseen_by_window``). ``full_sync`` below does the same
against the fake store, so a test can name what would lose its links: what is
still in Slack must keep them, what is gone from Slack must lose them.
"""

import hashlib
import time
from dataclasses import dataclass

import pytest
from slack_behaviour_fakes import (
    FakeCheckpoints,
    FakeSlackStore,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    GENERAL,
    PARTNERS,
    SECRET,
    standard_workspace,
    workspace_config,
    workspace_connector,
)

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.slack.team.connector import SlackConnector
from app.services.graph_db.common.full_sync_unlink import (
    split_unseen_by_window,
    unseen_node_keys,
)

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
DAY_MINUTES = 60 * 24


@dataclass
class FullSyncResult:
    complete: bool
    # External ids of the stored records and record groups the runner would unlink.
    unlinked: set[str]


async def full_sync(connector: SlackConnector, store: FakeSlackStore, checkpoints: FakeCheckpoints) -> FullSyncResult:
    """Run a full sync as ``run_sync_task`` does and answer what it would unlink."""
    checkpoints.sync_points.clear()
    seen: dict[str, set[str]] = {RECORDS: set(), GROUPS: set()}
    write_records, write_groups = store.on_new_records, store.on_new_record_groups

    async def on_new_records(records_with_permissions) -> list:
        saved = await write_records(records_with_permissions)
        for record in saved:  # the processor notes a record seen when it saves it
            seen[RECORDS].add(record.id)
            if record.record_group_id:
                seen[GROUPS].add(record.record_group_id)
        return saved

    async def on_new_record_groups(groups) -> None:
        await write_groups(groups)
        for group, _ in groups:
            seen[GROUPS].add(group.id)

    def note_kept_records(connector_id: str, record_ids: list[str]) -> None:
        seen[RECORDS].update(record_ids)

    async def note_kept_record_group(connector_id: str, external_group_id: str) -> None:
        group = store.record_groups.get(external_group_id)
        if group:
            seen[GROUPS].add(group.id)

    store.on_new_records = on_new_records
    store.on_new_record_groups = on_new_record_groups
    store.note_kept_records = note_kept_records
    store.note_kept_record_group = note_kept_record_group
    store.full_sync_in_progress = True
    try:
        await connector.run_sync()
    finally:
        store.on_new_records, store.on_new_record_groups = write_records, write_groups
        del store.note_kept_records, store.note_kept_record_group
        store.full_sync_in_progress = False

    coverage = connector.full_sync_coverage()
    if not coverage.complete or not (seen[RECORDS] or seen[GROUPS]):
        return FullSyncResult(complete=coverage.complete, unlinked=set())
    records = {r.id: r for r in store.records.values()}
    groups = {g.id: g for g in store.record_groups.values()}
    unseen = unseen_node_keys({"record_keys": list(records), "record_group_keys": list(groups)}, seen)
    unlink, _ = split_unseen_by_window(
        unseen,
        {k: {"sourceCreatedAtTimestamp": r.source_created_at, "recordGroupId": r.record_group_id}
         for k, r in records.items()},
        {k: {"sourceCreatedAtTimestamp": g.source_created_at, "groupType": g.group_type.value}
         for k, g in groups.items()},
        coverage.window_start_ms,
        coverage.windowed_group_types,
    )
    by_id = {f"{RECORDS}/{k}": r.external_record_id for k, r in records.items()}
    by_id |= {f"{GROUPS}/{k}": g.external_group_id for k, g in groups.items()}
    return FullSyncResult(complete=True, unlinked={by_id[node] for node in unlink})


def link_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def window_from(days: int) -> dict:
    start_ms = int((time.time() - days * 86400) * 1000)
    return {"filters": {"sync": {"values": {
        "sync_window": {"operator": "is_after", "value": {"start": start_ms, "end": None}, "type": "datetime"},
    }}}}


def delete_message(workspace: SlackWorkspace, channel: str, ts: str) -> None:
    workspace.history[channel] = [m for m in workspace.history[channel] if m["ts"] != ts]


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


def stage_channel(workspace: SlackWorkspace, channel: str, tag: str) -> set[str]:
    """A message with a link, a thread with a reply and a file: what a channel holds.

    Returns the external ids the connector stores for them.
    """
    note, parent, reply, shared = (ts_minutes_ago(m) for m in (300, 200, 199, 100))
    url = f"https://example.com/{tag}"
    workspace.post(channel, note, ALICE, f"see {url}")
    workspace.post(channel, parent, BOB, "question?")
    workspace.reply(channel, parent, reply, ALICE, "answer")
    fd = workspace.add_file(f"F{tag.upper()}", f"{tag}.pdf")
    workspace.post(channel, shared, ALICE, "the doc", files=[fd])
    return {note, link_id(url), parent, f"thread_{channel}_{parent}", fd["id"]}


async def stored_ids_of(store: FakeSlackStore, channel: str) -> set[str]:
    return {
        r.external_record_id for r in store.records.values()
        if r.external_record_group_id in (channel, *(
            g.external_group_id for g in store.record_groups.values() if g.parent_external_group_id == channel
        ))
    } | {g.external_group_id for g in store.record_groups.values() if g.parent_external_group_id == channel}


class TestLinksOutsideTheWindow:
    async def test_a_link_in_a_message_older_than_the_window_keeps_its_links(self, workspace, store, checkpoints) -> None:
        old = ts_minutes_ago(20 * DAY_MINUTES)
        old_url = "https://example.com/old-plan"
        workspace.post(GENERAL, old, ALICE, f"plan: {old_url}")
        kept = ts_minutes_ago(60)
        workspace.post(GENERAL, kept, BOB, "see https://example.com/kept")
        gone = ts_minutes_ago(120)
        gone_url = "https://example.com/gone"
        workspace.post(GENERAL, gone, BOB, f"see {gone_url}")
        connector, config_service = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert link_id(old_url) in store.records

        # The window moved past the old message; one message was deleted in Slack.
        config_service.config = {**workspace_config(), **window_from(7)}
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        really_gone = {gone, link_id(gone_url)}
        assert result.unlinked & really_gone == really_gone
        assert result.unlinked - really_gone == set()

    async def test_a_link_in_a_deleted_recent_message_loses_its_links(self, workspace, store, checkpoints) -> None:
        gone = ts_minutes_ago(120)
        gone_url = "https://example.com/gone"
        workspace.post(GENERAL, gone, BOB, f"see {gone_url}")
        kept = ts_minutes_ago(30)
        workspace.post(GENERAL, kept, ALICE, "still here")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert link_id(gone_url) in store.records

        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.unlinked == {gone, link_id(gone_url)}

    async def test_a_link_in_a_thread_reply_older_than_the_window_stays_kept(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, reply = ts_minutes_ago(20 * DAY_MINUTES), ts_minutes_ago(20 * DAY_MINUTES - 1)
        url = "https://example.com/old-thread-doc"
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, f"answer: {url}")
        connector, config_service = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        thread = {r.external_record_id for r in store.records.values()
                  if r.external_record_group_id == f"thread_{GENERAL}_{parent}"}
        assert link_id(url) in thread

        config_service.config = {**workspace_config(), **window_from(7)}
        result = await full_sync(connector, store, checkpoints)

        assert result.unlinked & (thread | {f"thread_{GENERAL}_{parent}"}) == set()

    async def test_a_link_in_a_burst_inside_the_window_is_rewritten_with_its_burst(
        self, workspace, store, checkpoints,
    ) -> None:
        first, second = ts_minutes_ago(60), ts_minutes_ago(58)
        url = "https://example.com/burst-doc"
        workspace.post(GENERAL, first, ALICE, "look at this")
        workspace.post(GENERAL, second, BOB, f"here: {url}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        written: set[str] = set()
        write_records = store.on_new_records

        async def on_new_records(records_with_permissions) -> list:
            saved = await write_records(records_with_permissions)
            written.update(r.external_record_id for r, _ in records_with_permissions)
            return saved

        store.on_new_records = on_new_records
        try:
            result = await full_sync(connector, store, checkpoints)
        finally:
            store.on_new_records = write_records

        link = store.records[link_id(url)]
        burst = store.records[link.parent_external_record_id]
        assert link_id(url) in written
        assert link.source_created_at == burst.source_created_at == int(float(first) * 1000)
        assert result.unlinked == set()


class TestUnreadableChannels:
    async def test_an_archived_channel_the_app_is_not_in_keeps_its_messages(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert held <= await stored_ids_of(store, GENERAL)

        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert gone in result.unlinked
        assert result.unlinked - {gone} == set()

    async def test_a_private_channel_the_app_was_removed_from_keeps_its_messages(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, SECRET, "secret")
        gone = ts_minutes_ago(30)
        workspace.post(GENERAL, gone, ALICE, "general note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert held <= await stored_ids_of(store, SECRET)

        workspace.channels[SECRET]["is_member"] = False
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert gone in result.unlinked
        assert result.unlinked - {gone} == set()

    async def test_a_public_channel_the_app_cannot_join_keeps_its_messages(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert held <= await stored_ids_of(store, GENERAL)

        workspace.channels[GENERAL]["is_member"] = False
        workspace.fail("conversations.join", SlackError("missing_scope"), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert gone in result.unlinked
        assert result.unlinked - {gone} == set()

    async def test_a_channel_still_refused_after_joining_keeps_its_messages(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert held <= await stored_ids_of(store, GENERAL)

        workspace.fail("conversations.history", SlackError("not_in_channel"), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert workspace.calls_to("conversations.join")
        assert GENERAL in connector._kept_channel_ids
        assert result.complete
        assert result.unlinked == {gone}

    async def test_the_scan_of_an_unreadable_channel_reads_every_page(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        # Ids that sort before every stored one push GENERAL's records past the first scan page.
        template = store.records[gone]
        fillers = {f"filler-{n}" for n in range(1500)}
        for n, external_id in enumerate(sorted(fillers)):
            store.records[external_id] = template.model_copy(
                update={"id": f"0000000-filler-{n:05d}", "external_record_id": external_id},
            )

        scans: list[dict] = []
        get_records_by_status = store.get_records_by_status

        async def spy(*args: object, **kwargs: object) -> list:
            if kwargs.get("record_group_id") is None:
                scans.append(kwargs)
            return await get_records_by_status(*args, **kwargs)

        store.get_records_by_status = spy
        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert [s["after_key"] is not None for s in scans] == [False, True]
        assert result.complete
        assert result.unlinked == fillers | {gone}
        assert not held & result.unlinked

    async def test_a_listed_channel_whose_history_answers_channel_not_found_keeps_its_messages(
        self, workspace, store, checkpoints,
    ) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert held <= await stored_ids_of(store, GENERAL)

        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert gone in result.unlinked
        assert result.unlinked - {gone} == set()

    @pytest.mark.parametrize("error", ["not_in_channel", "thread_not_found", "channel_not_found"])
    async def test_a_thread_whose_replies_slack_refuses_keeps_its_replies(
        self, workspace, store, checkpoints, error,
    ) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        url = "https://example.com/answer"
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, f"answer: {url}")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        thread = {r.external_record_id for r in store.records.values()
                  if r.external_record_group_id == f"thread_{GENERAL}_{parent}"}
        assert thread

        workspace.fail("conversations.replies", SlackError(error), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert gone in result.unlinked
        assert result.unlinked - {gone} == set()
        assert {r.external_record_id for r in store.records.values()
                if r.external_record_group_id == f"thread_{GENERAL}_{parent}"} == thread

    async def test_a_thread_kept_for_refused_replies_is_read_once_slack_answers(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(300), ALICE, "morning")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        parent = ts_minutes_ago(200)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(199), ALICE, "answer")
        group = f"thread_{GENERAL}_{parent}"
        refused = True
        workspace.fail("conversations.replies", SlackError("thread_not_found"), times=None, when=lambda p: refused)

        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}") is None
        assert checkpoints.value(f"slack_messages/{GENERAL}") is not None
        assert not any(r.external_record_group_id == group for r in store.records.values())

        refused = False
        await connector.run_sync()

        assert any(r.external_record_group_id == group for r in store.records.values())

    async def test_a_thread_deleted_from_slack_loses_its_links(self, workspace, store, checkpoints) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, "answer")
        workspace.post(GENERAL, ts_minutes_ago(30), ALICE, "still here")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        group = f"thread_{GENERAL}_{parent}"
        thread = {r.external_record_id for r in store.records.values() if r.external_record_group_id == group}
        assert thread

        delete_message(workspace, GENERAL, parent)
        del workspace.replies[(GENERAL, parent)]
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == thread | {group, parent}

    async def test_thread_replies_that_fail_after_the_first_page_unlink_nothing(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(200)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(199), ALICE, "answer")
        workspace.reply(GENERAL, parent, ts_minutes_ago(198), BOB, "thanks")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        workspace.page_size["conversations.replies"] = 1
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.replies", SlackError("internal_error"), times=None,
                       when=lambda p: bool(p.get("cursor")))
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert not result.complete
        assert result.unlinked == set()

    async def test_a_failed_lookup_of_an_unreadable_thread_unlinks_nothing(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(200)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(199), ALICE, "answer")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        get_records_in_record_group = store.get_records_in_record_group

        async def failing(connector_id: str, external_group_id: str, *args: object, **kwargs: object) -> list:
            if external_group_id.startswith("thread_"):
                raise RuntimeError("graph unavailable")
            return await get_records_in_record_group(connector_id, external_group_id, *args, **kwargs)

        store.get_records_in_record_group = failing
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert not result.complete
        assert result.unlinked == set()

    async def test_a_history_read_that_keeps_failing_unlinks_nothing(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.history", SlackError("internal_error"), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        result = await full_sync(connector, store, checkpoints)

        assert not result.complete
        assert result.unlinked == set()
        assert GENERAL not in connector._kept_channel_ids

    async def test_a_thread_whose_parent_sits_mid_burst_in_an_unreadable_channel_keeps_its_replies(
        self, workspace, store, checkpoints,
    ) -> None:
        first, parent, third = ts_minutes_ago(100), ts_minutes_ago(99), ts_minutes_ago(98)
        workspace.post(GENERAL, first, ALICE, "morning")
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.post(GENERAL, third, ALICE, "anyone?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(97), ALICE, "answer: https://example.com/answer",
                        files=[workspace.add_file("FANSWER", "answer.pdf")])
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        thread = {r.external_record_id for r in store.records.values()
                  if r.external_record_group_id == f"thread_{GENERAL}_{parent}"}
        assert {link_id("https://example.com/answer"), "FANSWER"} <= thread

        workspace.channels[GENERAL]["is_member"] = False
        workspace.fail("conversations.join", SlackError("missing_scope"), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}

    async def test_the_scan_keeps_only_the_unreadable_channels_records(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        kept, gone, parent = ts_minutes_ago(10), ts_minutes_ago(30), ts_minutes_ago(200)
        workspace.post(SECRET, kept, ALICE, "still here")
        workspace.post(SECRET, gone, ALICE, "secret note")
        workspace.post(SECRET, parent, BOB, "question?")
        workspace.reply(SECRET, parent, ts_minutes_ago(199), ALICE, "answer")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        secret_ids = await stored_ids_of(store, SECRET)
        assert f"thread_{SECRET}_{parent}" in secret_ids

        workspace.channels[GENERAL]["is_member"] = False
        workspace.fail("conversations.join", SlackError("missing_scope"), times=None)
        workspace.history[SECRET] = [m for m in workspace.history[SECRET] if m["ts"] == kept]
        del workspace.replies[(SECRET, parent)]
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == secret_ids - {kept}

    async def test_channel_not_found_after_the_first_page_keeps_the_rest(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        workspace.page_size["conversations.history"] = 2
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("cursor")))
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}
        assert checkpoints.value(f"slack_messages/{GENERAL}") is None

    async def test_a_burst_split_by_a_refused_page_is_kept_beside_its_rewritten_half(
        self, workspace, store, checkpoints,
    ) -> None:
        """A and B were one burst; B is read again on page 1 and page 2 (A) is refused. The
        stored A+B burst is kept so A stays searchable; B is in it and in its own record
        until a full sync reads the channel to the end."""
        parent, a, b, c = (ts_minutes_ago(m) for m in (90, 60, 59, 30))
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(25), ALICE, "late answer")
        workspace.post(GENERAL, a, ALICE, "first half")
        workspace.post(GENERAL, b, BOB, "second half")
        workspace.post(GENERAL, c, ALICE, "later")
        gone = ts_minutes_ago(20)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (a_and_b,) = [r.external_record_id for r in store.records.values()
                      if r.external_record_group_id == GENERAL and r.start_ts == a and r.end_ts == b]
        thread = {r.external_record_id for r in store.records.values()
                  if r.external_record_group_id == f"thread_{GENERAL}_{parent}"}
        assert len(thread) == 2

        workspace.page_size["conversations.history"] = 2
        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("cursor")))
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}
        assert a_and_b in store.records
        assert b in store.records

    async def test_a_burst_wholly_on_the_read_pages_is_not_kept_beside_its_rewrite(
        self, workspace, store, checkpoints,
    ) -> None:
        """A and B were one burst; now B ends page 1 and A starts page 2, both read, and page
        3 is refused. Each is rewritten on its own, so the stored A+B burst is obsolete and
        is deleted by the same sync."""
        older, q, a, b, e = (ts_minutes_ago(m) for m in (150, 80, 60, 59, 10))
        workspace.post(GENERAL, older, BOB, "long ago")
        workspace.post(GENERAL, q, BOB, "earlier")
        workspace.post(GENERAL, a, ALICE, "first half")
        workspace.post(GENERAL, b, BOB, "second half")
        workspace.post(GENERAL, e, ALICE, "later")
        gone = ts_minutes_ago(20)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (a_and_b,) = [r.external_record_id for r in store.records.values()
                      if r.external_record_group_id == GENERAL and r.start_ts == a and r.end_ts == b]

        workspace.page_size["conversations.history"] = 2
        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and p.get("cursor") == "page:4")
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}
        assert a_and_b in store.deleted
        assert a_and_b not in store.records
        assert {a, b, older} <= set(store.records)

    async def test_an_unreadable_channel_with_nothing_stored_keeps_nothing_of_other_channels(
        self, workspace, store, checkpoints,
    ) -> None:
        workspace.channels[PARTNERS].update(is_archived=True, is_member=False)
        gone = ts_minutes_ago(30)
        workspace.post(GENERAL, gone, ALICE, "hello")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert PARTNERS in connector._kept_channel_ids
        assert result.complete
        assert result.unlinked == {gone}

    async def test_a_thread_of_an_unreadable_channel_with_no_record_directly_in_it_is_kept(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, "answer")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        # The thread-growth scan can write a thread whose channel holds no record of its own.
        for external_id in [k for k, r in store.records.items() if r.external_record_group_id == GENERAL]:
            del store.records[external_id]
        thread = await stored_ids_of(store, GENERAL)
        assert f"thread_{GENERAL}_{parent}" in thread
        assert thread & set(store.records)

        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}

    async def test_a_failed_scan_unlinks_nothing(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        get_records_by_status = store.get_records_by_status

        async def failing(*args: object, **kwargs: object) -> list:
            if kwargs.get("record_group_id") is None:
                raise RuntimeError("graph unavailable")
            return await get_records_by_status(*args, **kwargs)

        store.get_records_by_status = failing
        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert not result.complete
        assert result.unlinked == set()

    async def test_a_channel_readable_again_on_the_next_full_sync_loses_its_deleted_messages(
        self, workspace, store, checkpoints,
    ) -> None:
        stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(GENERAL, gone, ALICE, "soon deleted")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        assert (await full_sync(connector, store, checkpoints)).unlinked == set()

        workspace.channels[GENERAL].update(is_archived=False, is_member=True)
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert GENERAL not in connector._kept_channel_ids
        assert result.complete
        assert result.unlinked == {gone}

    @pytest.mark.parametrize("trash_all", [True, False], ids=["all-trashed", "one-trashed"])
    async def test_trashed_records_of_an_unreadable_channel_stay_kept(
        self, workspace, store, checkpoints, trash_all,
    ) -> None:
        held = stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        trashed = sorted(await stored_ids_of(store, GENERAL) & set(store.records))
        for external_id in trashed if trash_all else trashed[:1]:
            store.records[external_id] = store.records[external_id].model_copy(update={"is_deleted": True})

        workspace.channels[GENERAL].update(is_archived=True, is_member=False)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}
        assert not held & result.unlinked

    async def test_a_trashed_record_of_a_thread_whose_replies_are_refused_stays_kept(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        url = "https://example.com/answer"
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, f"answer: {url}")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        store.records[link_id(url)] = store.records[link_id(url)].model_copy(update={"is_deleted": True})

        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == {gone}


def pending_threads(connector: SlackConnector, channel: str) -> list[str] | None:
    """The parent ts of the channel's threads waiting for a retry of their replies."""
    value = connector.config_service.others.get(
        f"/services/connectors/{connector.connector_id}/slack_pending_threads/{channel}"
    )
    return None if value is None else value["thread_ts"]


class TestRefusedThreadsAreRetried:
    """A thread whose replies Slack refused is read again on later syncs, however old its parent."""

    async def test_an_old_thread_refused_once_is_read_on_the_next_incremental_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(3 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(3 * DAY_MINUTES - 1), ALICE, "answer")
        group = f"thread_{GENERAL}_{parent}"
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert not any(r.external_record_group_id == group for r in store.records.values())
        assert pending_threads(connector, GENERAL) == [parent]
        # The refusal does not hold the channel's scans back.
        assert checkpoints.value(f"slack_messages/{GENERAL}") is not None
        assert checkpoints.value(f"thread_growth/{GENERAL}") is not None

        refused = False
        await connector.run_sync()

        assert any(r.external_record_group_id == group for r in store.records.values())
        assert pending_threads(connector, GENERAL) == []

    async def test_a_new_thread_refused_in_the_thread_growth_scan_is_read_on_a_later_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, BOB, "question?")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (key,) = [k for k in checkpoints.sync_points if k.endswith(f"thread_growth/{GENERAL}")]
        checkpoints.sync_points[key] = {"last_check_time": 1000}

        # The parent is behind the message checkpoint, so only the thread-growth scan sees the reply.
        workspace.reply(GENERAL, parent, ts_minutes_ago(5), ALICE, "answer")
        group = f"thread_{GENERAL}_{parent}"
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        await connector.run_sync()

        assert not any(r.external_record_group_id == group for r in store.records.values())
        assert pending_threads(connector, GENERAL) == [parent]
        assert checkpoints.sync_points[key]["last_check_time"] > 1000

        refused = False
        await connector.run_sync()

        assert any(r.external_record_group_id == group for r in store.records.values())
        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_read_again_with_its_parent_leaves_the_pending_set(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(299), ALICE, "answer")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=1)
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        # Without the message checkpoint the channel's history, parent included, is read again.
        (key,) = [k for k in checkpoints.sync_points if k.endswith(f"slack_messages/{GENERAL}")]
        del checkpoints.sync_points[key]
        await connector.run_sync()

        assert any(r.external_record_group_id == f"thread_{GENERAL}_{parent}" for r in store.records.values())
        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_whose_parent_is_gone_is_dropped(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(3 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(3 * DAY_MINUTES - 1), ALICE, "answer")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=2)
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        delete_message(workspace, GENERAL, parent)
        del workspace.replies[(GENERAL, parent)]
        await connector.run_sync()

        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_outside_the_window_survives_a_full_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(10 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(10 * DAY_MINUTES - 1), ALICE, "answer")
        group = f"thread_{GENERAL}_{parent}"
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        connector, config_service = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        # The parent is now older than the window, so a full sync does not read it again.
        config_service.config = {**workspace_config(), **window_from(7)}
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert pending_threads(connector, GENERAL) == [parent]

        refused = False
        await connector.run_sync()

        assert any(r.external_record_group_id == group for r in store.records.values())
        assert pending_threads(connector, GENERAL) == []

    async def test_a_thread_growth_scan_slack_refuses_does_not_advance_its_checkpoint(
        self, workspace, store, checkpoints,
    ) -> None:
        workspace.post(GENERAL, ts_minutes_ago(300), ALICE, "morning")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (key,) = [k for k in checkpoints.sync_points if k.endswith(f"thread_growth/{GENERAL}")]
        checkpoints.sync_points[key] = {"last_check_time": 1000}

        # Only the thread-growth scan passes `latest`.
        workspace.fail("conversations.history", SlackError("not_in_channel"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("latest")))
        await connector.run_sync()

        assert checkpoints.sync_points[key]["last_check_time"] == 1000

    async def test_threads_slack_keeps_refusing_do_not_grow_the_pending_set_past_its_cap(
        self, workspace, store, checkpoints, monkeypatch,
    ) -> None:
        monkeypatch.setattr("app.connectors.sources.slack.common.full_sync_keep.MAX_PENDING_THREADS_PER_CHANNEL", 2)
        parents = [ts_minutes_ago(3 * DAY_MINUTES - 10 * i) for i in range(3)]
        for parent in parents:
            workspace.post(GENERAL, parent, BOB, "question?")
            workspace.reply(GENERAL, parent, f"{float(parent) + 1:.6f}", ALICE, "answer")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None)
        connector, _ = await workspace_connector(store, checkpoints)

        for _ in range(3):
            await connector.run_sync()
            pending = pending_threads(connector, GENERAL)
            assert len(pending) == 2
            assert set(pending) <= set(parents)

    @pytest.mark.parametrize("parent_read_again", [False, True], ids=["thread-growth-scan", "channel-history"])
    async def test_a_new_reply_refused_on_a_thread_already_read_is_read_once_slack_answers(
        self, workspace, store, checkpoints, parent_read_again,
    ) -> None:
        parent, first = ts_minutes_ago(300), ts_minutes_ago(299)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, first, ALICE, "answer")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        thread_key = f"slack_thread/{GENERAL}_{parent}"
        assert checkpoints.value(thread_key)["last_reply_ts"] == first

        later = ts_minutes_ago(5)
        workspace.reply(GENERAL, parent, later, BOB, "one more thing")
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        if parent_read_again:
            # Without the message checkpoint the parent comes back through the channel history.
            (key,) = [k for k in checkpoints.sync_points if k.endswith(f"slack_messages/{GENERAL}")]
            del checkpoints.sync_points[key]
        await connector.run_sync()

        assert later not in store.message_ts_count()
        assert checkpoints.value(thread_key)["last_reply_ts"] == first
        assert pending_threads(connector, GENERAL) == [parent]
        # A refused thread is kept, not incomplete: the run and the channel checkpoint still move.
        assert connector.full_sync_coverage().complete
        if parent_read_again:
            assert [k for k in checkpoints.sync_points if k.endswith(f"slack_messages/{GENERAL}")]

        refused = False
        await connector.run_sync()

        assert store.message_ts_count().get(later) == 1
        assert checkpoints.value(thread_key)["last_reply_ts"] == later
        assert pending_threads(connector, GENERAL) == []


class TestPrivateChannelWhoseMembersCannotBeRead:
    """Its stored grants are kept, so nothing new of it is written until its members can be read."""

    async def test_new_messages_and_replies_wait_until_the_members_can_be_read(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, first = ts_minutes_ago(300), ts_minutes_ago(299)
        workspace.post(SECRET, parent, BOB, "question?")
        workspace.reply(SECRET, parent, first, ALICE, "answer")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        stored = await stored_ids_of(store, SECRET)
        message_checkpoint = checkpoints.value(f"slack_messages/{SECRET}")
        thread_key = f"slack_thread/{SECRET}_{parent}"

        workspace.members[SECRET].remove(BOB)
        note, later = ts_minutes_ago(30), ts_minutes_ago(10)
        workspace.post(SECRET, note, ALICE, "after bob left")
        workspace.reply(SECRET, parent, later, ALICE, "also after bob left")
        refused = True
        workspace.fail("conversations.members", SlackError("missing_scope"), times=None,
                       when=lambda p: refused and p.get("channel") == SECRET)
        await connector.run_sync()

        written = store.message_ts_count()
        assert note not in written and later not in written
        assert checkpoints.value(f"slack_messages/{SECRET}") == message_checkpoint
        assert checkpoints.value(thread_key)["last_reply_ts"] == first
        assert await stored_ids_of(store, SECRET) == stored
        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}
        assert connector.full_sync_coverage().complete

        refused = False
        await connector.run_sync()

        written = store.message_ts_count()
        assert written.get(note) == 1 and written.get(later) == 1
        assert store.access_emails(SECRET) == {"alice@acme.com"}
        new = [r for r in store.messages() if {b.source_id for b in r.block_containers.blocks} & {note, later}]
        assert {r.external_record_group_id for r in new} == {SECRET, f"thread_{SECRET}_{parent}"}
        assert all(r.inherit_permissions for r in new)
        assert checkpoints.value(thread_key)["last_reply_ts"] == later

    async def test_a_full_sync_keeps_what_is_stored_and_is_complete(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, SECRET, "secret")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        stored = await stored_ids_of(store, SECRET)

        note = ts_minutes_ago(10)
        workspace.post(SECRET, note, ALICE, "new")
        workspace.fail("conversations.members", SlackError("missing_scope"), times=None,
                       when=lambda p: p.get("channel") == SECRET)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == set()
        assert note not in store.message_ts_count()
        assert await stored_ids_of(store, SECRET) == stored


class TestGoneFromSlack:
    async def test_a_deleted_channel_and_deleted_messages_lose_their_links(self, workspace, store, checkpoints) -> None:
        held = stage_channel(workspace, SECRET, "secret")
        gone = ts_minutes_ago(30)
        workspace.post(GENERAL, gone, ALICE, "general note")
        kept = ts_minutes_ago(20)
        workspace.post(GENERAL, kept, ALICE, "still here")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        secret_ids = await stored_ids_of(store, SECRET)
        assert held <= secret_ids

        del workspace.channels[SECRET]
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        really_gone = secret_ids | {SECRET, gone}
        assert result.unlinked & really_gone == really_gone
        assert result.unlinked - really_gone == set()

    async def test_a_deleted_channel_is_unlinked_while_another_channel_is_unreadable(
        self, workspace, store, checkpoints,
    ) -> None:
        stage_channel(workspace, SECRET, "secret")
        stage_channel(workspace, GENERAL, "general")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        secret_ids = await stored_ids_of(store, SECRET)

        del workspace.channels[SECRET]
        workspace.channels[GENERAL]["is_member"] = False
        workspace.fail("conversations.join", SlackError("missing_scope"), times=None)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert result.unlinked == secret_ids | {SECRET}

    async def test_a_channel_left_out_by_the_channel_filter_still_loses_its_links(
        self, workspace, store, checkpoints,
    ) -> None:
        stage_channel(workspace, SECRET, "secret")
        stage_channel(workspace, GENERAL, "general")
        connector, config_service = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        secret_ids = await stored_ids_of(store, SECRET)

        config_service.config = workspace_config(filters={"sync": {"values": {
            "channel_ids": {"operator": "not_in", "value": [SECRET], "type": "list"},
        }}})
        before = len(workspace.calls)
        result = await full_sync(connector, store, checkpoints)

        assert not any(c.method == "conversations.history" and c.params.get("channel") == SECRET
                       for c in workspace.calls[before:])
        assert SECRET not in connector._kept_channel_ids
        assert result.complete
        assert result.unlinked == secret_ids | {SECRET}


class TestRegroupKeepsWhatItCannotReplace:
    """A full sync that re-reads a burst but saves only part of what it holds keeps the stored
    burst, and its links, out of the unlink."""

    @staticmethod
    async def stored_burst(workspace, store, checkpoints, count: int) -> tuple[SlackConnector, list[str], str, str]:
        stamps = [ts_minutes_ago(m) for m in range(10, 10 - count, -1)]
        url = "https://example.com/burst-plan"
        workspace.post(GENERAL, stamps[0], ALICE, f"plan: {url}")
        for ts in stamps[1:]:
            workspace.post(GENERAL, ts, BOB, f"message {ts}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()
        (burst,) = [r.external_record_id for r in store.messages()
                    if r.external_record_group_id == GENERAL and r.start_ts == stamps[0] and r.end_ts == stamps[-1]]
        return connector, stamps, burst, link_id(url)

    async def test_a_burst_kept_for_a_message_saved_nowhere_keeps_its_links(self, workspace, store, checkpoints) -> None:
        """A+B+C; read two a page, newest first: the B,C page is left unwritten, then A is saved alone."""
        connector, (a, b, c), burst, link = await self.stored_burst(workspace, store, checkpoints, 3)
        workspace.page_size["conversations.history"] = 2
        store.leaves_unwritten = lambda r: getattr(r, "start_ts", None) == b

        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert burst in store.records
        assert not {burst, link} & result.unlinked

    async def test_a_burst_not_overwritten_by_a_shorter_one_keeps_its_links(self, workspace, store, checkpoints) -> None:
        """A+B+C+D; read two a page, newest first: the C,D page is left unwritten, so A+B, under
        the same id, does not replace it."""
        connector, (a, b, c, d), burst, link = await self.stored_burst(workspace, store, checkpoints, 4)
        workspace.page_size["conversations.history"] = 2
        store.leaves_unwritten = lambda r: getattr(r, "start_ts", None) == c

        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert (store.records[burst].start_ts, store.records[burst].end_ts) == (a, d)
        assert not {burst, link} & result.unlinked
