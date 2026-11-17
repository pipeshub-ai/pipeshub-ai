"""The membership publisher on the PostgreSQL broker: its check reads who holds the queue's claims."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import indexing_main
from app.services.messaging.config import MessageBrokerType


@pytest.mark.asyncio
async def test_the_postgres_broker_publishes_the_untagged_claimers_of_the_registered_groups() -> None:
    """Without it nothing is ever published, the verdict stays stale and stored-record retention never runs."""
    container = MagicMock()
    container.logger.return_value = logging.getLogger("membership-pg")
    container.kafka_consumers = []
    queue = MagicMock()
    queue.claimers = AsyncMock(return_value=["records/sp3-1", "records-old"])
    with (
        patch.object(indexing_main, "get_message_broker_type", return_value=MessageBrokerType.POSTGRES),
        patch("app.services.messaging.postgres.queue.PgQueue", return_value=queue),
        patch("app.services.messaging.membership.MembershipPublisher") as publisher,
    ):
        publisher.return_value.run = AsyncMock()
        await asyncio.wait_for(indexing_main.run_membership_publisher(container), timeout=2)

    publisher.return_value.run.assert_awaited_once()
    check = publisher.call_args.args[1]
    assert await check({"records_group": ["record-events"]}) == ["records-old"]
    queue.claimers.assert_awaited_once_with({"records_group": ["record-events"]})
