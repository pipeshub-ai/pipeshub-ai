"""The membership publisher on a broker whose consumers register no group."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from app import indexing_main
from app.services.messaging.config import MessageBrokerType


@pytest.mark.asyncio
async def test_the_postgres_broker_publishes_nothing_and_does_not_loop(caplog: pytest.LogCaptureFixture) -> None:
    """Its consumers register no group: the publisher says so once and ends, instead of warning every period."""
    container = MagicMock()
    container.logger.return_value = logging.getLogger("membership-pg")
    with (
        patch.object(indexing_main, "get_message_broker_type", return_value=MessageBrokerType.POSTGRES),
        patch("app.services.messaging.membership.MembershipPublisher") as publisher,
        caplog.at_level(logging.INFO, logger="membership-pg"),
    ):
        await asyncio.wait_for(indexing_main.run_membership_publisher(container), timeout=2)

    publisher.assert_not_called()
    assert any("not published" in r.message for r in caplog.records)
