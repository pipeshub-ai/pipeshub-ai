"""The ids a Slack regroup looks up, and what it deletes."""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.slack.common.bursts import (
    SlackRegroupMixin,
    channel_burst_id,
    channel_record_ids,
    regrouped_record_ids,
)
from app.models.entities import MessageRecord, Record, RecordType

CH = "C1"


def message_record(external_id: str, start_ts: str, end_ts: str, group: str = CH) -> MessageRecord:
    return MessageRecord(
        org_id="o", record_name="n", record_type=RecordType.MESSAGE, external_record_id=external_id,
        external_record_group_id=group, version=1, origin="CONNECTOR", connector_name="SLACK",
        connector_id="c", start_ts=start_ts, end_ts=end_ts,
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
