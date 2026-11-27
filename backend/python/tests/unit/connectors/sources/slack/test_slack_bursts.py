"""The ids a Slack regroup looks up, and what it deletes."""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.slack.common.bursts import (
    SlackRegroupMixin,
    channel_burst_id,
    channel_record_ids,
    regrouped_record_ids,
    saved_records,
    unsaved_ts,
)
from app.models.entities import MessageRecord, Record, RecordType

CH = "C1"


def message_record(external_id: str, start_ts: str, end_ts: str, group: str = CH) -> MessageRecord:
    return MessageRecord(
        org_id="o", record_name="n", record_type=RecordType.MESSAGE, external_record_id=external_id,
        external_record_group_id=group, version=1, origin="CONNECTOR", connector_name="SLACK",
        connector_id="c", start_ts=start_ts, end_ts=end_ts,
        # As the connectors store a burst: its first and its last message, in ms.
        source_created_at=int(float(start_ts) * 1000), source_updated_at=int(float(end_ts) * 1000),
    )


def test_only_messages_inside_a_written_record_are_looked_up() -> None:
    msgs = [{"ts": "100.0"}, {"ts": "150.0"}, {"ts": "900.0"}]
    written = [message_record(channel_burst_id(CH, "100.0"), "100.0", "150.0")]

    ids = regrouped_record_ids(msgs, written, lambda ts: channel_record_ids(CH, ts))

    # 900.0 is in no written record: its record was not built, so what is stored stays.
    assert ids == ["100.0", "150.0", channel_burst_id(CH, "150.0")]


class _Connector(SlackRegroupMixin):
    def __init__(self, stored: dict[str, object]) -> None:
        self.connector_id = "c"
        self.logger = logging.getLogger("test.slack_bursts")
        self.data_entities_processor = MagicMock()

        async def lookup(connector_id: str, external_record_id: str) -> object:
            value = stored.get(external_record_id)
            if isinstance(value, Exception):
                raise value
            return value

        self.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        self.data_entities_processor.on_record_deleted = AsyncMock(return_value=False)


async def test_a_failed_lookup_keeps_the_record_and_others_are_still_deleted() -> None:
    stale = message_record("150.0", "150.0", "150.0")
    stale.id = "stale-key"
    connector = _Connector({"100.0": RuntimeError("store down"), "150.0": stale})

    await connector._delete_regrouped_records(["100.0", "150.0", "missing"], CH)

    connector.data_entities_processor.on_record_deleted.assert_awaited_once_with("stale-key")


async def test_a_record_that_is_not_a_message_is_not_deleted() -> None:
    file_like = Record(
        org_id="o", record_name="f", record_type=RecordType.FILE, external_record_id="150.0",
        external_record_group_id=CH, version=1, origin="CONNECTOR", connector_name="SLACK",
        connector_id="c",
    )
    connector = _Connector({"150.0": file_like})

    await connector._delete_regrouped_records(["150.0"], CH)

    connector.data_entities_processor.on_record_deleted.assert_not_awaited()


async def test_a_single_of_another_channel_with_the_same_ts_is_not_deleted() -> None:
    """A single's id is its bare ts, which Slack makes unique only within a channel."""
    other = message_record("150.0", "150.0", "150.0", group="C2")
    other.id = "other-key"
    connector = _Connector({"150.0": other})

    await connector._delete_regrouped_records(["150.0"], CH)

    connector.data_entities_processor.on_record_deleted.assert_not_awaited()


def test_only_the_records_reported_saved_count_as_saved() -> None:
    kept, skipped = message_record("100.0", "100.0", "100.0"), message_record("200.0", "200.0", "200.0")
    batch = [(kept, []), (skipped, [])]

    assert saved_records(batch, [kept]) == [kept]
    # A processor that reports nothing saved nothing a regroup may count on.
    assert saved_records(batch, None) == []


def test_messages_no_saved_record_spans_are_unsaved() -> None:
    msgs = [{"ts": "100.0"}, {"ts": "150.0"}, {"ts": "900.0"}]

    assert unsaved_ts(msgs, [message_record("100.0", "100.0", "150.0")]) == {"900.0"}


async def test_a_stored_record_spanning_an_unsaved_message_is_kept() -> None:
    """Stored A+B+C; a re-read saved A alone, and B and C have no saved record."""
    burst = message_record(channel_burst_id(CH, "100.0"), "100.0", "200.0")
    burst.id = "burst-key"
    connector = _Connector({burst.external_record_id: burst})

    await connector._delete_regrouped_records([burst.external_record_id], CH, {"150.0", "200.0"})
    connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    await connector._delete_regrouped_records([burst.external_record_id], CH, {"900.0"})
    connector.data_entities_processor.on_record_deleted.assert_awaited_once_with("burst-key")


async def test_a_stored_record_that_does_not_say_its_span_is_kept() -> None:
    burst = message_record(channel_burst_id(CH, "100.0"), "100.0", "200.0")
    burst.source_updated_at = None
    connector = _Connector({burst.external_record_id: burst})

    await connector._delete_regrouped_records([burst.external_record_id], CH)

    connector.data_entities_processor.on_record_deleted.assert_not_awaited()


async def test_a_failed_lookup_or_delete_during_a_full_sync_makes_it_unlink_nothing() -> None:
    """The record is kept but cannot be noted kept, so the full sync must not count as complete."""
    stale = message_record("150.0", "150.0", "150.0")
    for full_sync in (True, False):
        connector = _Connector({"100.0": RuntimeError("store down"), "150.0": stale})
        connector._full_sync_read_failed = False
        connector.data_entities_processor.full_sync_running = MagicMock(return_value=full_sync)
        connector.data_entities_processor.on_record_deleted = AsyncMock(side_effect=RuntimeError("store down"))

        await connector._delete_regrouped_records(["100.0"], CH)
        assert connector._full_sync_read_failed is full_sync  # a failed lookup
        connector._full_sync_read_failed = False
        await connector._delete_regrouped_records(["150.0"], CH)
        assert connector._full_sync_read_failed is full_sync  # a failed delete


async def test_a_shorter_burst_ending_in_the_same_millisecond_as_an_unsaved_tail_is_held_back() -> None:
    """Stored A+B+C+D; the re-read saved C and D nowhere and proposes A+B, which ends in the
    same millisecond as C and D. Their microseconds still put them after B."""
    first, b_end = "1700000000.000100", "1700000000.000200"
    stored = message_record(channel_burst_id(CH, first), first, "1700000000.000400")
    stored.id = "burst-key"
    shorter = message_record(channel_burst_id(CH, first), first, b_end)
    connector = _Connector({stored.external_record_id: stored})
    connector._full_sync_read_failed = False
    connector.data_entities_processor.full_sync_running = MagicMock(return_value=False)

    kept = await connector._hold_back_overwrites(
        [(shorter, [])], CH, {"1700000000.000300", "1700000000.000400"},
    )

    assert kept == []
    connector.data_entities_processor.get_record_by_external_id.assert_awaited_once()

    # Nothing unsaved after B's end in that millisecond: the shorter burst is written.
    kept = await connector._hold_back_overwrites([(shorter, [])], CH, {"1700000000.000150"})
    assert kept == [(shorter, [])]
