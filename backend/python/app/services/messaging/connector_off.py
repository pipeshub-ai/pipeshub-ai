"""Settling, at read time, record events of connectors that are turned off or gone.

The record handler skips a newRecord/updateRecord/reindexRecord whose connector
is inactive (writing AUTO_INDEX_OFF) or deleted (writing nothing). When such a
connector has a large backlog, every one of those events still took a buffer
slot, a dispatch slot and an index permit just to be skipped. The consumers
hand each batch they read to a ``ConnectorOffFilter`` first, and acknowledge
whatever it settled without buffering it.

The consumers depend on this protocol only, so they stay free of graph code;
the implementation lives with the indexing service
(``app.modules.indexing.connector_off_events``).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from logging import Logger

    from app.services.messaging.config import StreamMessage


@dataclass
class ConnectorOffResult:
    """Which messages of a batch were settled, by their position in it.

    A settled message has had every write the handler would have made for it
    already made, so the consumer only has to acknowledge it.
    """

    settled: frozenset[int] = frozenset()
    # (connectorId, "off" | "removed") -> count.
    by_connector: Counter[tuple[str, str]] = field(default_factory=Counter)


def describe_connector_off(messages: Sequence[StreamMessage]) -> str:
    """``connector=count`` for the per-pass summary line."""
    counts = Counter(str(m.payload.get("connectorId")) for m in messages)
    return ", ".join(f"{connector_id}={count}" for connector_id, count in sorted(counts.items()))


@runtime_checkable
class ConnectorOffFilter(Protocol):
    async def settle(self, messages: Sequence[StreamMessage]) -> ConnectorOffResult:
        """Settle the messages the record handler would skip only because their
        connector is off or gone.

        Must not raise, and must settle nothing it is unsure of: a connector or
        record that cannot be read leaves its messages to the normal path.
        """
        ...


async def settle_connector_off(
    connector_off_filter: ConnectorOffFilter | None,
    messages: Sequence[StreamMessage | None],
    logger: Logger,
) -> ConnectorOffResult:
    """Run the filter over the parseable messages of a batch, never raising.

    Positions in the result refer to ``messages``, unparseable ones included.
    """
    if connector_off_filter is None:
        return ConnectorOffResult()
    positions = [i for i, message in enumerate(messages) if message is not None]
    if not positions:
        return ConnectorOffResult()
    try:
        result = await connector_off_filter.settle([messages[i] for i in positions])  # type: ignore[misc]
    except Exception as e:
        logger.warning(
            "Could not check this batch for events of turned-off connectors; "
            "it goes through the normal path: %s", e,
        )
        return ConnectorOffResult()
    return ConnectorOffResult(
        settled=frozenset(positions[i] for i in result.settled if 0 <= i < len(positions)),
        by_connector=result.by_connector,
    )
