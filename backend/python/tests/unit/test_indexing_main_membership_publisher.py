"""The membership publisher as the indexing service starts it, and the consumer groups a clean entry has to cover."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import indexing_main
from app.services.messaging.config import MessageBrokerType


@pytest.mark.asyncio
async def test_the_postgres_broker_publishes_nothing_so_retention_stays_paused(caplog: pytest.LogCaptureFixture) -> None:
    """The queue keeps no durable record of who consumes it: a pod of an older build that claims and acks between two checks
    leaves no trace, and nothing in the database stops such a build from connecting. With no proof, nothing is published:
    the verdict stays stale, which holds artifact reads and pauses stored-record retention."""
    container = MagicMock()
    container.logger.return_value = logging.getLogger("membership-pg")
    with (
        patch.object(indexing_main, "get_message_broker_type", return_value=MessageBrokerType.POSTGRES),
        patch("app.services.messaging.membership.MembershipPublisher") as publisher,
        caplog.at_level(logging.INFO, logger="membership-pg"),
    ):
        await asyncio.wait_for(indexing_main.run_membership_publisher(container), timeout=2)

    publisher.assert_not_called()
    assert any("not published" in r.message and "retention" in r.message for r in caplog.records)


def test_the_expected_groups_are_every_configured_indexing_consumer_started_or_not() -> None:
    """Derived from the configuration, so a stage consumer that starts later (its topics are missing) is expected all the same."""
    record_config = SimpleNamespace(group_id="records_consumer_group", topics=["record-events.0", "record-events.1"])
    registry = SimpleNamespace(names=lambda: ("classify", "entities"), topic_for=lambda name: f"pipeline-{name}")

    assert indexing_main._indexing_groups(record_config, "record-events-priority", registry) == {
        "records_consumer_group": ["record-events.0", "record-events.1"],
        "records_priority_consumer_group": ["record-events-priority"],
        "pipeline_classify_group": ["pipeline-classify"],
        "pipeline_entities_group": ["pipeline-entities"],
    }
