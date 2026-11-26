"""The full-sync keep logic both Slack connectors share (slack/common/full_sync_keep.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.slack.common import full_sync_keep
from app.connectors.sources.slack.common.full_sync_keep import SlackFullSyncKeepMixin
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector


class _Host(SlackFullSyncKeepMixin):
    """The connector state the mixin reads, with nothing else."""

    def __init__(self) -> None:
        self.connector_id = "conn-1"
        self.logger = MagicMock()
        self.config_service = MagicMock()
        self.config_service.get_config = AsyncMock(return_value=None)
        self.config_service.set_config = AsyncMock(return_value=True)
        self.config_service.delete_config = AsyncMock(return_value=True)
        self.data_entities_processor = MagicMock()
        self.data_entities_processor.full_sync_running = MagicMock(return_value=True)
        self.data_entities_processor.get_records_by_status = AsyncMock(return_value=[])
        self.data_entities_processor.get_records_in_record_group = AsyncMock(return_value=[])
        self.data_entities_processor.note_kept_record_group = AsyncMock()
        self.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)
        self.rate_limiter = MagicMock()
        self.rate_limiter.acquire = AsyncMock()
        self.messages_sync_point = MagicMock()
        self.messages_sync_point.update_sync_point = AsyncMock()
        self.audit_log_sync_point = MagicMock()
        self.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        self.audit_log_sync_point.update_sync_point = AsyncMock()
        self.ds = MagicMock()
        self._fresh_datasource = AsyncMock(return_value=self.ds)
        self._process_thread = AsyncMock(return_value=True)
        self._process_thread_incremental = AsyncMock(return_value=True)
        self._handle_new_thread = AsyncMock(return_value=True)
        self._kept_channel_ids: set[str] = set()
        self._kept_channel_read_from: dict[str, str] = {}
        self._full_sync_read_failed = False


def _rec(rid: str, group: str, *, start_ts: str | None = None, created: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=rid, external_record_group_id=group, start_ts=start_ts, source_created_at=created)


def _resp(success: bool = True, data: dict | None = None, error: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(success=success, data=data or {}, error=error)


def _deferred(ts: str, channel: str = "C1") -> SimpleNamespace:
    return SimpleNamespace(msg={"ts": ts, "reply_count": 1}, ctx=SimpleNamespace(channel_id=channel), rg_id="rg")


class TestConnectorsKeepTheirDifferences:
    def test_both_connectors_use_the_shared_logic(self) -> None:
        assert issubclass(SlackConnector, SlackFullSyncKeepMixin)
        assert issubclass(SlackIndividualConnector, SlackFullSyncKeepMixin)

    def test_only_the_individual_connector_deletes_an_emptied_pending_list(self) -> None:
        assert SlackIndividualConnector._delete_empty_pending_threads is True
        assert SlackConnector._delete_empty_pending_threads is False

    def test_each_connector_reads_replies_with_its_own_page_size(self) -> None:
        assert SlackIndividualConnector._replies_page_size == 200
        assert SlackConnector._replies_page_size == 999

    @pytest.mark.parametrize(
        ("bots_indexed", "is_bot", "skipped"),
        [(False, True, True), (True, True, False), (False, False, False)],
    )
    def test_team_stops_retrying_a_bot_parent_only_while_bots_are_not_indexed(
        self, bots_indexed: bool, is_bot: bool, skipped: bool,
    ) -> None:
        host = SimpleNamespace(
            indexing_filters=MagicMock(is_enabled=MagicMock(return_value=bots_indexed)),
            _is_bot_message=MagicMock(return_value=is_bot),
            logger=MagicMock(),
        )
        assert SlackConnector._pending_parent_skipped(host, "C1", "5.0", {"ts": "5.0"}) is skipped

    def test_individual_retries_every_parent(self) -> None:
        assert SlackIndividualConnector._pending_parent_skipped is SlackFullSyncKeepMixin._pending_parent_skipped


class TestReadThisRun:
    def test_nothing_read_in_the_channel(self) -> None:
        assert _Host()._read_this_run(_rec("r", "C1", start_ts="9.0")) is False

    def test_thread_by_its_parent_ts(self) -> None:
        h = _Host()
        h._kept_channel_read_from["C1"] = "5.0"
        assert h._read_this_run(_rec("r", "thread_C1_6.0")) is True
        assert h._read_this_run(_rec("r", "thread_C1_4.0")) is False

    def test_burst_by_its_oldest_message(self) -> None:
        h = _Host()
        h._kept_channel_read_from["C1"] = "5.0"
        assert h._read_this_run(_rec("r", "C1", start_ts="5.0")) is True
        assert h._read_this_run(_rec("r", "C1", start_ts="4.9")) is False

    def test_other_records_by_creation_time_in_whole_milliseconds(self) -> None:
        h = _Host()
        h._kept_channel_read_from["C1"] = "1700000000.123456"
        assert h._read_this_run(_rec("r", "C1", created=1700000000123)) is True
        assert h._read_this_run(_rec("r", "C1", created=1700000000122)) is False
        assert h._read_this_run(_rec("r", "C1")) is False


class TestKeepUnreadChannels:
    async def test_notes_kept_only_unread_records_of_refused_channels(self) -> None:
        h = _Host()
        h._kept_channel_ids = {"C1"}
        h._kept_channel_read_from["C1"] = "5.0"
        p = h.data_entities_processor
        p.get_records_by_status.return_value = [
            _rec("old", "C1", start_ts="1.0"),
            _rec("reread", "C1", start_ts="6.0"),
            _rec("reply", "thread_C1_2.0"),
            _rec("other", "C2", start_ts="1.0"),
        ]
        await h._keep_unread_channels()
        p.note_kept_records.assert_called_once_with("conn-1", ["old", "reply"])
        p.note_kept_record_group.assert_awaited_once_with("conn-1", "thread_C1_2.0")
        assert h._full_sync_read_failed is False

    async def test_nothing_outside_a_full_sync(self) -> None:
        h = _Host()
        h._kept_channel_ids = {"C1"}
        h.data_entities_processor.full_sync_running.return_value = False
        await h._keep_unread_channels()
        h.data_entities_processor.get_records_by_status.assert_not_awaited()

    async def test_a_failed_lookup_marks_the_read_incomplete(self) -> None:
        h = _Host()
        h._kept_channel_ids = {"C1"}
        h.data_entities_processor.get_records_by_status.side_effect = RuntimeError("down")
        await h._keep_unread_channels()
        assert h._full_sync_read_failed is True


class TestKeepUnreadThread:
    async def test_notes_the_thread_records_kept(self) -> None:
        h = _Host()
        h.data_entities_processor.get_records_in_record_group.return_value = [_rec("a", "thread_C1_1.0")]
        await h._keep_unread_thread("thread_C1_1.0")
        h.data_entities_processor.note_kept_records.assert_called_once_with("conn-1", ["a"])

    async def test_a_failed_lookup_marks_the_read_incomplete_and_raises(self) -> None:
        h = _Host()
        h.data_entities_processor.get_records_in_record_group.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await h._keep_unread_thread("thread_C1_1.0")
        assert h._full_sync_read_failed is True


class TestPendingThreads:
    async def test_read_returns_the_stored_list(self) -> None:
        h = _Host()
        h.config_service.get_config.return_value = {"thread_ts": [1.5, "2.0"]}
        assert await h._read_pending_threads("C1") == ["1.5", "2.0"]
        assert h.config_service.get_config.call_args.args[0] == (
            "/services/connectors/conn-1/slack_pending_threads/C1"
        )
        assert h.config_service.get_config.call_args.kwargs["raise_on_error"] is True

    async def test_write_drops_the_oldest_past_the_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(full_sync_keep, "MAX_PENDING_THREADS_PER_CHANNEL", 2)
        h = _Host()
        await h._write_pending_threads("C1", ["1", "2", "3"])
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": ["2", "3"]}

    async def test_an_emptied_list_is_stored_empty_by_default(self) -> None:
        h = _Host()
        await h._write_pending_threads("C1", [])
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": []}
        h.config_service.delete_config.assert_not_awaited()

    async def test_an_emptied_list_deletes_the_key_when_the_connector_says_so(self) -> None:
        h = _Host()
        h._delete_empty_pending_threads = True
        await h._write_pending_threads("C1", [])
        h.config_service.set_config.assert_not_awaited()
        h.config_service.delete_config.assert_awaited_once()

    async def test_a_failed_save_raises(self) -> None:
        h = _Host()
        h.config_service.set_config.return_value = False
        with pytest.raises(RuntimeError):
            await h._write_pending_threads("C1", ["1"])

    async def test_add_appends_once(self) -> None:
        h = _Host()
        h.config_service.get_config.return_value = {"thread_ts": ["1"]}
        await h._add_pending_thread("C1", "1")
        h.config_service.set_config.assert_not_awaited()
        await h._add_pending_thread("C1", "2")
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": ["1", "2"]}


class TestRetryPendingThread:
    async def test_a_parent_without_replies_is_done(self) -> None:
        h = _Host()
        h.ds.conversations_history = AsyncMock(return_value=_resp(data={"messages": []}))
        assert await h._retry_pending_thread("C1", "5.0") is True
        h._handle_new_thread.assert_not_awaited()

    async def test_a_parent_the_connector_skips_is_done(self) -> None:
        h = _Host()
        h.ds.conversations_history = AsyncMock(
            return_value=_resp(data={"messages": [{"ts": "5.0", "reply_count": 2}]})
        )
        h._pending_parent_skipped = MagicMock(return_value=True)
        assert await h._retry_pending_thread("C1", "5.0") is True
        h._handle_new_thread.assert_not_awaited()

    async def test_a_parent_with_replies_is_read_again(self) -> None:
        h = _Host()
        parent = {"ts": "5.0", "reply_count": 2}
        h.ds.conversations_history = AsyncMock(return_value=_resp(data={"messages": [parent]}))
        assert await h._retry_pending_thread("C1", "5.0") is True
        h._handle_new_thread.assert_awaited_once_with(parent, "C1", "5.0")

    async def test_a_refused_history_read_stays_pending(self) -> None:
        h = _Host()
        h.ds.conversations_history = AsyncMock(return_value=_resp(success=False, error="ratelimited"))
        assert await h._retry_pending_thread("C1", "5.0") is False

    async def test_a_retry_that_raises_stays_pending_and_marks_the_read_incomplete(self) -> None:
        h = _Host()
        h.ds.conversations_history = AsyncMock(side_effect=RuntimeError("down"))
        assert await h._retry_pending_thread("C1", "5.0") is False
        assert h._full_sync_read_failed is True

    async def test_a_partial_read_marks_the_read_incomplete(self) -> None:
        h = _Host()
        h.ds.conversations_history = AsyncMock(
            return_value=_resp(data={"messages": [{"ts": "5.0", "reply_count": 2}]})
        )
        h._handle_new_thread.return_value = False
        assert await h._retry_pending_thread("C1", "5.0") is False
        assert h._full_sync_read_failed is True


class TestFetchThreadRepliesRaw:
    def _ctx(self) -> SimpleNamespace:
        return SimpleNamespace(rate_limiter=MagicMock(acquire=AsyncMock()))

    async def test_a_refused_first_page_is_unknown_not_empty(self) -> None:
        h = _Host()
        h.ds.conversations_replies = AsyncMock(return_value=_resp(success=False, error="thread_not_found"))
        assert await h._fetch_thread_replies_raw("C1", "5.0", self._ctx()) == ([], None)

    async def test_a_refused_later_page_is_a_partial_read(self) -> None:
        h = _Host()
        h.ds.conversations_replies = AsyncMock(side_effect=[
            _resp(data={"messages": [{"ts": "5.0"}, {"ts": "6.0"}], "response_metadata": {"next_cursor": "c"}}),
            _resp(success=False, error="thread_not_found"),
        ])
        assert await h._fetch_thread_replies_raw("C1", "5.0", self._ctx()) == ([{"ts": "6.0"}], False)

    async def test_pages_with_the_connector_page_size(self) -> None:
        h = _Host()
        h._replies_page_size = 7
        h.ds.conversations_replies = AsyncMock(return_value=_resp(data={"messages": []}))
        await h._fetch_thread_replies_raw("C1", "5.0", self._ctx(), oldest="4.0")
        kwargs = h.ds.conversations_replies.call_args.kwargs
        assert kwargs["limit"] == 7
        assert kwargs["oldest"] == "4.0"


class TestFinishChannelMessages:
    async def test_refused_replies_go_pending_and_the_checkpoint_moves(self) -> None:
        h = _Host()
        h._process_thread.return_value = None
        await h._finish_channel_messages(
            "C1", "key", [_deferred("5.0")],
            newest_ts="9.0", oldest_read="1.0", history_error=None, total_msgs=3,
        )
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": ["5.0"]}
        h.audit_log_sync_point.update_sync_point.assert_not_awaited()
        h.messages_sync_point.update_sync_point.assert_awaited_once_with("key", {"last_sync_time": "9.0"})

    async def test_a_read_thread_saves_its_reply_checkpoint_and_leaves_pending(self) -> None:
        h = _Host()
        h.config_service.get_config.return_value = {"thread_ts": ["5.0"]}
        dt = _deferred("5.0")
        dt.msg["latest_reply"] = "7.0"
        await h._finish_channel_messages(
            "C1", "key", [dt], newest_ts="9.0", oldest_read="1.0", history_error=None, total_msgs=1,
        )
        h.audit_log_sync_point.update_sync_point.assert_awaited_once()
        assert h.audit_log_sync_point.update_sync_point.call_args.args[1] == {"last_reply_ts": "7.0"}
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": []}

    async def test_a_refused_channel_holds_its_checkpoint_and_records_how_far_it_read(self) -> None:
        h = _Host()
        h._kept_channel_ids = {"C1"}
        h.config_service.get_config.return_value = {"thread_ts": ["3.0"]}
        await h._finish_channel_messages(
            "C1", "key", [], newest_ts="9.0", oldest_read="4.0", history_error=None, total_msgs=2,
        )
        h.messages_sync_point.update_sync_point.assert_not_awaited()
        assert h._kept_channel_read_from == {"C1": "4.0"}
        # Pending threads are not retried in a channel Slack refused.
        h.ds.conversations_history.assert_not_called()
        assert h._full_sync_read_failed is False

    async def test_a_failed_history_page_marks_the_read_incomplete(self) -> None:
        h = _Host()
        await h._finish_channel_messages(
            "C1", "key", [], newest_ts="9.0", oldest_read="4.0", history_error="ratelimited", total_msgs=2,
        )
        h.messages_sync_point.update_sync_point.assert_not_awaited()
        assert h._full_sync_read_failed is True

    async def test_pending_threads_are_retried_and_removed_when_read(self) -> None:
        h = _Host()
        h.config_service.get_config.return_value = {"thread_ts": ["3.0"]}
        h._retry_pending_thread = AsyncMock(return_value=True)
        await h._finish_channel_messages(
            "C1", "key", [], newest_ts=None, oldest_read=None, history_error=None, total_msgs=0,
        )
        h._retry_pending_thread.assert_awaited_once_with("C1", "3.0")
        assert h.config_service.set_config.call_args.args[1] == {"thread_ts": []}

    async def test_a_thread_that_raises_holds_the_checkpoint(self) -> None:
        h = _Host()
        h._process_thread.side_effect = RuntimeError("boom")
        await h._finish_channel_messages(
            "C1", "key", [_deferred("5.0")],
            newest_ts="9.0", oldest_read="1.0", history_error=None, total_msgs=1,
        )
        h.messages_sync_point.update_sync_point.assert_not_awaited()
        assert h._full_sync_read_failed is True

    async def test_pending_threads_wait_while_the_history_failed(self) -> None:
        h = _Host()
        h.config_service.get_config.return_value = {"thread_ts": ["3.0"]}
        h._retry_pending_thread = AsyncMock(return_value=True)
        await h._finish_channel_messages(
            "C1", "key", [], newest_ts="9.0", oldest_read="4.0", history_error="ratelimited", total_msgs=2,
        )
        h._retry_pending_thread.assert_not_awaited()
        h.config_service.set_config.assert_not_awaited()

    async def test_a_thread_with_a_reply_checkpoint_and_group_is_read_incrementally(self) -> None:
        h = _Host()
        h.audit_log_sync_point.read_sync_point.return_value = {"last_reply_ts": "6.0"}
        h.data_entities_processor.get_record_group_by_external_id.return_value = SimpleNamespace(id="rg-5")
        dt = _deferred("5.0")
        await h._finish_channel_messages(
            "C1", "key", [dt], newest_ts="9.0", oldest_read="1.0", history_error=None, total_msgs=1,
        )
        h._process_thread.assert_not_awaited()
        h._process_thread_incremental.assert_awaited_once_with(dt.msg, dt.ctx, "rg-5", "6.0")

    async def test_a_reply_checkpoint_without_its_group_reads_the_thread_in_full(self) -> None:
        h = _Host()
        h.audit_log_sync_point.read_sync_point.return_value = {"last_reply_ts": "6.0"}
        dt = _deferred("5.0")
        await h._finish_channel_messages(
            "C1", "key", [dt], newest_ts="9.0", oldest_read="1.0", history_error=None, total_msgs=1,
        )
        h._process_thread_incremental.assert_not_awaited()
        h._process_thread.assert_awaited_once_with(dt.msg, dt.ctx, "rg")
