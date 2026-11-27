"""CPD6-34: the worker reads a change event from the bytes Node's publisher writes.

``contracts/control-plane/vectors/change-message.json`` holds the value of a ``control-plane-events`` message as
Node puts it on the broker (``change-message-vectors.test.ts`` holds Node to it). Here the real Redis Streams
consumer reads that value from the stream and ``BrokerChangeNotifier`` hands the event to its subscriber.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.control_plane.runtime.notifier import CHANGE_TOPIC
from harness_core.contracts import dump_contract
from tests.unit.control_plane.test_broker_notifier import (
    _redis_notifier,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.services.messaging.test_redis_streams_broadcast_consumer import (
    NonBlockingXreadProvider,
)

if TYPE_CHECKING:
    from app.control_plane.contracts.generated.change_event import ChangeEvent

VECTORS = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/control-plane/vectors/change-message.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
async def test_cpd6_34_the_subscriber_reads_the_event_from_nodes_bytes(vector: dict[str, Any]) -> None:
    assert vector["topic"] == CHANGE_TOPIC
    provider = NonBlockingXreadProvider(is_cluster=False)
    harness = await _redis_notifier(provider, "cp-wire")
    seen: list[ChangeEvent] = []
    _ = harness.notifier.subscribe(seen.append)
    try:
        # As Node's Redis Streams producer adds it: the key and the doubly encoded value.
        _ = await provider.get_client().xadd(CHANGE_TOPIC, {"key": vector["key"], "value": vector["brokerValue"]})
        await harness.settled()
    finally:
        await harness.notifier.stop()

    assert [dump_contract(event) for event in seen] == [vector["event"]]
