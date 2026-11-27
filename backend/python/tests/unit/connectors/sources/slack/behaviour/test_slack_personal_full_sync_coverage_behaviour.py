"""What a personal Slack full sync leaves linked, driven over the fake workspace.

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
    SECRET,
    personal_config,
    personal_connector,
    standard_workspace,
)

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.models.entities import Record
from app.services.graph_db.common.full_sync_unlink import (
    split_unseen_by_window,
    unseen_node_keys,
)
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
DAY_MINUTES = 60 * 24


class KeepingSlackStore(FakeSlackStore):
    """The fake store plus the processor lookups a full sync uses to keep what it cannot read.

    Kept here rather than in slack_behaviour_fakes so this module does not depend on
    the team connector's changes to the shared fake.
    """

    def __init__(self) -> None:
        super().__init__()
        self.full_sync_in_progress = False

    def full_sync_running(self, connector_id: str) -> bool:
        return self.full_sync_in_progress

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None,
        *, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        group = self.record_groups.get(external_group_id)
        if group is None:
            return []
        return await self.get_records_by_status(
            connector_id, None, limit=limit, record_group_id=group.id, after_key=after_key, visibility=visibility,
        )

    async def get_records_by_status(
        self, connector_id: str, status_filters: list[str] | None, limit: int | None = None, offset: int = 0,
        record_group_id: str | None = None, is_placeholder: bool | None = None, after_key: str | None = None,
        exclude_statuses: list[str] | None = None, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Ordered by id and paged by ``after_key``, as the graph stores answer it."""
        found = sorted(
            (
                r for r in self.records.values()
                if (record_group_id is None or r.record_group_id == record_group_id)
                and (status_filters is None or r.indexing_status in status_filters)
                and r.indexing_status not in (exclude_statuses or [])
                and (is_placeholder is None or r.is_placeholder == is_placeholder)
                and (after_key is None or r.id > after_key)
                and matches_visibility(r, visibility)
            ),
            key=lambda r: r.id,
        )[offset:]
        return found if limit is None else found[:limit]


@pytest.fixture
def store() -> KeepingSlackStore:
    return KeepingSlackStore()


@dataclass
class FullSyncResult:
    complete: bool
    # External ids of the stored records and record groups the runner would unlink.
    unlinked: set[str]
    # External ids of the stored records the sync noted kept rather than rewrote.
    kept: set[str]


async def full_sync(
    connector: SlackIndividualConnector, store: FakeSlackStore, checkpoints: FakeCheckpoints,
) -> FullSyncResult:
    """Run a full sync as ``run_sync_task`` does and answer what it would unlink."""
    checkpoints.sync_points.clear()
    seen: dict[str, set[str]] = {RECORDS: set(), GROUPS: set()}
    kept: set[str] = set()
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
        kept.update(record_ids)

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
    records = {r.id: r for r in store.records.values()}
    kept_ext = {records[k].external_record_id for k in kept if k in records}
    if not coverage.complete or not (seen[RECORDS] or seen[GROUPS]):
        return FullSyncResult(complete=coverage.complete, unlinked=set(), kept=kept_ext)
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
    return FullSyncResult(complete=True, unlinked={by_id[node] for node in unlink}, kept=kept_ext)


def assert_unlinks_only(result: FullSyncResult, really_gone: set[str]) -> None:
    wrongly = result.unlinked - really_gone
    missed = really_gone - result.unlinked
    assert not wrongly and not missed, (
        f"wrongly unlinked: {len(wrongly)} {sorted(wrongly)}; "
        f"really gone unlinked: {len(really_gone & result.unlinked)} of {len(really_gone)}"
    )


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
    standard_workspace(slack)
    slack.auth_user_id = ALICE
    return slack


def stage_channel(workspace: SlackWorkspace, channel: str, tag: str) -> None:
    """A message with a link, a thread with a reply and a file: what a channel holds."""
    note, parent, reply, shared = (ts_minutes_ago(m) for m in (300, 200, 199, 100))
    workspace.post(channel, note, ALICE, f"see https://example.com/{tag}")
    workspace.post(channel, parent, BOB, "question?")
    workspace.reply(channel, parent, reply, ALICE, f"answer: https://example.com/{tag}-answer")
    fd = workspace.add_file(f"F{tag.upper()}", f"{tag}.pdf")
    workspace.post(channel, shared, ALICE, "the doc", files=[fd])


def stored_ids_of(store: FakeSlackStore, channel: str) -> set[str]:
    groups = {channel} | {
        g.external_group_id for g in store.record_groups.values() if g.parent_external_group_id == channel
    }
    return {r.external_record_id for r in store.records.values() if r.external_record_group_id in groups} | (
        groups - {channel}
    )


def thread_ids(store: FakeSlackStore, channel: str, parent: str) -> set[str]:
    group = f"thread_{channel}_{parent}"
    return {r.external_record_id for r in store.records.values() if r.external_record_group_id == group}


class TestLinksOutsideTheWindow:
    async def test_a_link_in_a_message_older_than_the_window_keeps_its_links(self, workspace, store, checkpoints) -> None:
        old = ts_minutes_ago(20 * DAY_MINUTES)
        old_url = "https://example.com/old-plan"
        workspace.post(GENERAL, old, ALICE, f"plan: {old_url}")
        workspace.post(GENERAL, ts_minutes_ago(60), BOB, "still here")
        gone = ts_minutes_ago(120)
        gone_url = "https://example.com/gone"
        workspace.post(GENERAL, gone, BOB, f"see {gone_url}")
        connector, config_service = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert link_id(old_url) in store.records

        config_service.config = {**personal_config(), **window_from(7)}
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, {gone, link_id(gone_url)})


class TestUnreadableChannels:
    @pytest.mark.parametrize("error", ["not_in_channel", "channel_not_found"])
    async def test_a_listed_channel_whose_history_slack_refuses_keeps_its_records(
        self, workspace, store, checkpoints, error,
    ) -> None:
        stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert len(stored_ids_of(store, GENERAL)) >= 6

        workspace.fail("conversations.history", SlackError(error), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, {gone})

    async def test_channel_not_found_after_the_first_page_keeps_the_rest(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        workspace.page_size["conversations.history"] = 2
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("cursor")))
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, {gone})
        assert checkpoints.value(f"slack_messages/{GENERAL}") is None
        # Only records reaching onto the refused pages are kept; page 1 rewrote the rest.
        read_from = float(sorted((m["ts"] for m in workspace.history[GENERAL]), key=float)[-2])
        kept = [r for r in store.records.values() if r.external_record_id in result.kept]
        assert kept
        assert all(float(r.start_ts) < read_from for r in kept if getattr(r, "start_ts", None))

    async def test_a_burst_split_by_a_refused_page_is_kept_beside_its_rewritten_half(
        self, workspace, store, checkpoints,
    ) -> None:
        a, b, c = (ts_minutes_ago(m) for m in (60, 59, 30))
        earlier = ts_minutes_ago(90)
        workspace.post(GENERAL, earlier, BOB, "earlier")
        workspace.post(GENERAL, a, ALICE, "first half")
        workspace.post(GENERAL, b, BOB, "second half")
        workspace.post(GENERAL, c, ALICE, "later")
        gone = ts_minutes_ago(20)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        (a_and_b,) = [r.external_record_id for r in store.records.values()
                      if r.external_record_group_id == GENERAL and r.start_ts == a and r.end_ts == b]

        workspace.page_size["conversations.history"] = 2
        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("cursor")))
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, {gone})
        assert a_and_b in store.records
        # Page 1 (c, b) rewrote c; the a+b burst and `earlier` reach onto the refused page.
        assert result.kept == {a_and_b, earlier}

    @pytest.mark.parametrize("error", ["not_in_channel", "thread_not_found", "channel_not_found"])
    async def test_a_thread_whose_replies_slack_refuses_keeps_its_replies(
        self, workspace, store, checkpoints, error,
    ) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, "answer: https://example.com/answer",
                        files=[workspace.add_file("FANSWER", "answer.pdf")])
        gone = ts_minutes_ago(30)
        workspace.post(SECRET, gone, ALICE, "secret note")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        thread = thread_ids(store, GENERAL, parent)
        assert {link_id("https://example.com/answer"), "FANSWER"} <= thread

        workspace.fail("conversations.replies", SlackError(error), times=None)
        delete_message(workspace, SECRET, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, {gone})
        assert thread_ids(store, GENERAL, parent) == thread

    async def test_a_thread_kept_for_refused_replies_is_read_once_slack_answers(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(300), ALICE, "morning")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, "answer")
        refused = True
        workspace.fail("conversations.replies", SlackError("thread_not_found"), times=None, when=lambda p: refused)

        await full_sync(connector, store, checkpoints)

        assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}") is None

        refused = False
        await connector.run_sync()

        assert reply in store.message_ts_count()

    async def test_a_refused_channel_holding_only_thread_records_keeps_them(self, workspace, store, checkpoints) -> None:
        parent, reply = ts_minutes_ago(200), ts_minutes_ago(199)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, reply, ALICE, "answer: https://example.com/answer")
        secret_parent = ts_minutes_ago(100)
        workspace.post(SECRET, secret_parent, BOB, "secret question?")
        workspace.reply(SECRET, secret_parent, ts_minutes_ago(99), ALICE, "secret answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        # Thread growth writes a thread group and its records with no record directly in the channel.
        for key in [k for k, r in store.records.items() if r.external_record_group_id == GENERAL]:
            del store.records[key]
        thread = thread_ids(store, GENERAL, parent)
        assert thread
        secret_thread = thread_ids(store, SECRET, secret_parent) | {f"thread_{SECRET}_{secret_parent}", secret_parent}

        workspace.fail("conversations.history", SlackError("not_in_channel"), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        delete_message(workspace, SECRET, secret_parent)
        del workspace.replies[(SECRET, secret_parent)]
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        # Records of another channel are not kept with the refused one.
        assert_unlinks_only(result, secret_thread)
        assert result.kept == thread

    async def test_a_thread_read_before_slack_refused_its_channel_is_not_kept(self, workspace, store, checkpoints) -> None:
        old_parent, old_reply = ts_minutes_ago(300), ts_minutes_ago(299)
        new_parent, first, gone = ts_minutes_ago(200), ts_minutes_ago(199), ts_minutes_ago(150)
        workspace.post(GENERAL, old_parent, BOB, "old question?")
        workspace.reply(GENERAL, old_parent, old_reply, ALICE, "old answer")
        workspace.post(GENERAL, new_parent, BOB, "new question?")
        workspace.reply(GENERAL, new_parent, first, ALICE, "answer")
        workspace.reply(GENERAL, new_parent, gone, ALICE, "and more")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        for key in [k for k, r in store.records.items() if r.external_record_group_id == GENERAL]:
            del store.records[key]
        (gone_record,) = [
            r.external_record_id for r in store.messages()
            if [b.source_id for b in r.block_containers.blocks] == [gone]
        ]
        assert gone_record in thread_ids(store, GENERAL, new_parent)

        workspace.page_size["conversations.history"] = 1
        workspace.fail("conversations.history", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("channel") == GENERAL and bool(p.get("cursor")))
        workspace.replies[(GENERAL, new_parent)] = [
            m for m in workspace.replies[(GENERAL, new_parent)] if m["ts"] != gone
        ]
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        # The newer thread was read on the first page; only the older one is kept.
        assert result.kept == thread_ids(store, GENERAL, old_parent)
        assert gone_record in result.unlinked

    async def test_a_history_read_that_keeps_failing_unlinks_nothing(self, workspace, store, checkpoints) -> None:
        stage_channel(workspace, GENERAL, "general")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.history", SlackError("internal_error"), times=None,
                       when=lambda p: p.get("channel") == GENERAL)
        result = await full_sync(connector, store, checkpoints)

        assert not result.complete
        assert result.unlinked == set()

    async def test_channels_kept_on_one_run_are_forgotten_on_the_next(self, workspace, store, checkpoints) -> None:
        workspace.post(GENERAL, ts_minutes_ago(300), ALICE, "morning")
        workspace.post(GENERAL, ts_minutes_ago(200), ALICE, "noon")
        workspace.page_size["conversations.history"] = 1
        refused = True
        workspace.fail("conversations.history", SlackError("not_in_channel"), times=None,
                       when=lambda p: refused and p.get("channel") == GENERAL and bool(p.get("cursor")))
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert connector._kept_channel_ids == {GENERAL}
        assert GENERAL in connector._kept_channel_read_from

        refused = False
        await connector.run_sync()

        assert connector._kept_channel_ids == set()
        assert connector._kept_channel_read_from == {}


def pending_threads(connector: SlackIndividualConnector, channel: str) -> list[str]:
    """The parent ts of the channel's threads waiting for a retry of their replies."""
    value = connector.config_service.others.get(connector._pending_threads_path(channel))
    return [] if value is None else value["thread_ts"]


def rewind_thread_growth(checkpoints: FakeCheckpoints, channel: str) -> None:
    """The personal scan reads only parents posted since its last run, so rewind it to see an older one."""
    (key,) = [k for k in checkpoints.sync_points if k.endswith(f"thread_growth/{channel}")]
    checkpoints.sync_points[key] = {"last_check_time": 1000}


def in_thread(store: FakeSlackStore, channel: str, parent: str) -> bool:
    group = f"thread_{channel}_{parent}"
    return any(r.external_record_group_id == group for r in store.records.values())


class TestRefusedThreadsAreRetried:
    """A thread whose replies Slack refused is read again on later syncs, however old its parent."""

    async def test_an_old_thread_refused_once_is_read_on_the_next_incremental_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(3 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(3 * DAY_MINUTES - 1), ALICE, "answer")
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        connector, _ = await personal_connector(store, checkpoints)

        await connector.run_sync()

        assert not in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == [parent]
        # The refusal does not hold the channel's scans back.
        assert checkpoints.value(f"slack_messages/{GENERAL}") is not None
        assert checkpoints.value(f"thread_growth/{GENERAL}") is not None

        refused = False
        await connector.run_sync()

        assert in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_outside_the_window_survives_a_full_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(3 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(3 * DAY_MINUTES - 1), ALICE, "answer")
        workspace.post(GENERAL, ts_minutes_ago(60), ALICE, "recent")
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        connector, config_service = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        # The parent is now older than the window, so the full sync's history read never reaches it.
        config_service.config = {**personal_config(), **window_from(1)}
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert pending_threads(connector, GENERAL) == [parent]
        # The message and reply cursors are still read afresh.
        assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}") is None

        refused = False
        await connector.run_sync()

        assert in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == []

    async def test_a_new_thread_refused_in_the_thread_growth_scan_is_read_on_a_later_sync(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, BOB, "question?")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        (key,) = [k for k in checkpoints.sync_points if k.endswith(f"thread_growth/{GENERAL}")]
        checkpoints.sync_points[key] = {"last_check_time": 1000}

        # The parent is behind the message checkpoint, so only the thread-growth scan sees the reply.
        workspace.reply(GENERAL, parent, ts_minutes_ago(5), ALICE, "answer")
        refused = True
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None, when=lambda p: refused)
        await connector.run_sync()

        assert not in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == [parent]
        assert checkpoints.sync_points[key]["last_check_time"] > 1000

        refused = False
        await connector.run_sync()

        assert in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_read_again_with_its_parent_leaves_the_pending_set(
        self, workspace, store, checkpoints,
    ) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(299), ALICE, "answer")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=2)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        # Without the message checkpoint the channel's history, parent included, is read again.
        (key,) = [k for k in checkpoints.sync_points if k.endswith(f"slack_messages/{GENERAL}")]
        del checkpoints.sync_points[key]
        await connector.run_sync()

        assert in_thread(store, GENERAL, parent)
        assert pending_threads(connector, GENERAL) == []

    async def test_a_pending_thread_whose_parent_is_gone_is_dropped(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(3 * DAY_MINUTES)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(3 * DAY_MINUTES - 1), ALICE, "answer")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=2)
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        assert pending_threads(connector, GENERAL) == [parent]

        delete_message(workspace, GENERAL, parent)
        del workspace.replies[(GENERAL, parent)]
        await connector.run_sync()

        assert pending_threads(connector, GENERAL) == []

    async def test_a_thread_growth_scan_slack_refuses_does_not_advance_its_checkpoint(
        self, workspace, store, checkpoints,
    ) -> None:
        workspace.post(GENERAL, ts_minutes_ago(300), ALICE, "morning")
        connector, _ = await personal_connector(store, checkpoints)
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
        connector, _ = await personal_connector(store, checkpoints)

        for _ in range(3):
            await connector.run_sync()
            pending = pending_threads(connector, GENERAL)
            assert len(pending) == 2
            assert set(pending) <= set(parents)

    @pytest.mark.parametrize("error", ["channel_not_found", "thread_not_found"])
    @pytest.mark.parametrize("parent_read_again", [False, True], ids=["thread-growth-scan", "channel-history"])
    async def test_a_new_reply_refused_on_a_thread_already_read_is_read_once_slack_answers(
        self, workspace, store, checkpoints, parent_read_again, error,
    ) -> None:
        parent, first = ts_minutes_ago(300), ts_minutes_ago(299)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, first, ALICE, "answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        thread_key = f"slack_thread/{GENERAL}_{parent}"
        assert checkpoints.value(thread_key)["last_reply_ts"] == first

        later = ts_minutes_ago(5)
        workspace.reply(GENERAL, parent, later, BOB, "one more thing")
        refused = True
        workspace.fail("conversations.replies", SlackError(error), times=None, when=lambda p: refused)
        if parent_read_again:
            # Without the message checkpoint the parent comes back through the channel history.
            (key,) = [k for k in checkpoints.sync_points if k.endswith(f"slack_messages/{GENERAL}")]
            del checkpoints.sync_points[key]
        else:
            rewind_thread_growth(checkpoints, GENERAL)
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

    async def test_new_replies_refused_on_two_syncs_leave_one_pending_entry(
        self, workspace, store, checkpoints,
    ) -> None:
        parent, first = ts_minutes_ago(300), ts_minutes_ago(299)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, first, ALICE, "answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        workspace.reply(GENERAL, parent, ts_minutes_ago(5), BOB, "one more thing")
        workspace.fail("conversations.replies", SlackError("not_in_channel"), times=None)
        for _ in range(2):
            rewind_thread_growth(checkpoints, GENERAL)
            await connector.run_sync()
            assert pending_threads(connector, GENERAL) == [parent]
            assert checkpoints.value(f"slack_thread/{GENERAL}_{parent}")["last_reply_ts"] == first

    async def test_another_thread_in_the_channel_still_moves_its_checkpoint(
        self, workspace, store, checkpoints,
    ) -> None:
        refused_parent, refused_first = ts_minutes_ago(300), ts_minutes_ago(299)
        open_parent, open_first = ts_minutes_ago(200), ts_minutes_ago(199)
        for parent, first in ((refused_parent, refused_first), (open_parent, open_first)):
            workspace.post(GENERAL, parent, BOB, "question?")
            workspace.reply(GENERAL, parent, first, ALICE, "answer")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()

        refused_later, open_later = ts_minutes_ago(6), ts_minutes_ago(5)
        workspace.reply(GENERAL, refused_parent, refused_later, BOB, "one more thing")
        workspace.reply(GENERAL, open_parent, open_later, BOB, "and here")
        workspace.fail("conversations.replies", SlackError("channel_not_found"), times=None,
                       when=lambda p: p.get("ts") == refused_parent)
        rewind_thread_growth(checkpoints, GENERAL)
        await connector.run_sync()

        written = store.message_ts_count()
        assert refused_later not in written
        assert written.get(open_later) == 1
        assert checkpoints.value(f"slack_thread/{GENERAL}_{refused_parent}")["last_reply_ts"] == refused_first
        assert checkpoints.value(f"slack_thread/{GENERAL}_{open_parent}")["last_reply_ts"] == open_later
        assert pending_threads(connector, GENERAL) == [refused_parent]


class TestGoneFromSlack:
    async def test_a_deleted_channel_deleted_thread_and_deleted_message_lose_their_links(
        self, workspace, store, checkpoints,
    ) -> None:
        stage_channel(workspace, SECRET, "secret")
        parent = ts_minutes_ago(250)
        workspace.post(GENERAL, parent, BOB, "question?")
        workspace.reply(GENERAL, parent, ts_minutes_ago(249), ALICE, "answer")
        gone = ts_minutes_ago(30)
        workspace.post(GENERAL, gone, ALICE, "general note")
        workspace.post(GENERAL, ts_minutes_ago(10), ALICE, "still here")
        connector, _ = await personal_connector(store, checkpoints)
        await connector.run_sync()
        secret = stored_ids_of(store, SECRET) | {SECRET}
        thread = thread_ids(store, GENERAL, parent) | {f"thread_{GENERAL}_{parent}", parent}

        del workspace.channels[SECRET]
        delete_message(workspace, GENERAL, parent)
        del workspace.replies[(GENERAL, parent)]
        delete_message(workspace, GENERAL, gone)
        result = await full_sync(connector, store, checkpoints)

        assert result.complete
        assert_unlinks_only(result, secret | thread | {gone})
