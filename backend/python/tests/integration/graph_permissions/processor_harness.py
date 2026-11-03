"""Drive the real write path against a live database.

The point of this harness is that scenarios are built by
``DataSourceEntitiesProcessor`` — the same code a connector sync runs — rather
than by hand. A hand-built fixture can encode a graph the write path would
never produce, so it proves the query is self-consistent, not that it works on
real synced data.

Two substitutions are needed and only two:

* ``initialize()`` is skipped. It builds a Kafka/Redis producer from config,
  and the graph writes never read it back — it only emits record-events. The
  unit tests bypass it the same way, by assigning ``org_id`` and
  ``messaging_producer`` after construction.
* The producer is a recorder. Tests can assert on what would have been
  published without a broker running.
"""

from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore


class RecordingProducer:
    """Stands in for IMessagingProducer, keeping what was sent."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    async def initialize(self) -> None:
        return None

    async def send_message(self, topic: str, payload: object, key: str | None = None) -> bool:
        self.messages.append((topic, payload))
        return True

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        for entry in messages:
            self.messages.append((topic, entry))
        return [True] * len(messages)

    def event_types(self) -> list[str]:
        types = []
        for _topic, payload in self.messages:
            body = payload[1] if isinstance(payload, tuple) else payload
            if isinstance(body, dict) and "eventType" in body:
                types.append(body["eventType"])
        return types


def build_processor(provider, org_id: str) -> tuple[DataSourceEntitiesProcessor, RecordingProducer]:
    """A processor writing through `provider` into the live graph."""
    logger = MagicMock()
    config_service = AsyncMock()
    data_store = GraphDataStore(logger, provider)

    processor = DataSourceEntitiesProcessor(logger, data_store, config_service)
    # Deliberately not initialize(): that constructs a real broker producer.
    processor.org_id = org_id
    producer = RecordingProducer()
    processor.messaging_producer = producer
    return processor, producer
