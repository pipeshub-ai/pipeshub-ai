"""KafkaAdmin closes its admin client on every exit path, a failed start included.

The indexing service retries stage-topic creation in the background while it fails, so a
client left open by a failed start would accumulate for as long as the broker refuses.
"""

import logging
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka.errors import KafkaConnectionError

from app.services.messaging.kafka.admin import KafkaAdmin

if TYPE_CHECKING:
    from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig


def _client(start_error: BaseException | None = None) -> MagicMock:
    client = MagicMock()
    client.start = AsyncMock(side_effect=start_error)
    client.close = AsyncMock()
    client.list_topics = AsyncMock(return_value=["pipeline.classify"])
    client.create_topics = AsyncMock()
    return client


def _admin() -> KafkaAdmin:
    return KafkaAdmin(logging.getLogger("test"), cast("KafkaProducerConfig", MagicMock()), partitions=1)


@pytest.mark.asyncio
async def test_a_start_that_fails_still_closes_the_client() -> None:
    client = _client(KafkaConnectionError("Unable to bootstrap from localhost:9092"))
    with patch.object(KafkaAdmin, "_client", return_value=client):
        with pytest.raises(KafkaConnectionError):
            await _admin().ensure_topics_exist(["pipeline.classify"])
        with pytest.raises(KafkaConnectionError):
            await _admin().list_topics()

    assert client.close.await_count == 2


@pytest.mark.asyncio
async def test_existing_topics_are_not_created_again() -> None:
    client = _client()
    with patch.object(KafkaAdmin, "_client", return_value=client):
        await _admin().ensure_topics_exist(["pipeline.classify"])

    client.create_topics.assert_not_awaited()
    client.close.assert_awaited_once()
