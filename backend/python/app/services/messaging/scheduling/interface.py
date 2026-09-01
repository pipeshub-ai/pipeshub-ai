"""Protocols and configuration for consumer-side fair scheduling.

Kept broker-agnostic and message-agnostic on purpose: everything here is
plain data plus two small Protocols, so the Kafka and Redis Streams indexing
consumers (and any downstream build composing its own key/weight logic) can
all share one contract without importing anything broker-specific.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.services.messaging.config import StreamMessage

# A fairness key addresses one level per configured key field, outermost
# first -- ``("org-7", "connector-42")``. Tuples rather than a joined string
# so the scheduler can be fair *between* levels: an org with fifty
# connectors must not get fifty times the share of an org with one.
FairnessKey = tuple[str, ...]


@runtime_checkable
class FairnessKeyExtractor(Protocol):
    """Extracts the fairness grouping key from a message.

    ``orgId`` groups by customer; ``connectorId`` groups by the individual
    connector instance, which is what separates one user's sync from
    another's inside a single org.
    """

    def extract(self, message: "StreamMessage") -> FairnessKey: ...


@runtime_checkable
class WeightProvider(Protocol):
    """Per-key DRR quantum (weight).

    Consulted on every dispatch turn, so a key's share can change at runtime
    without restarting the consumer. The default here is a flat weight for
    every key; an implementation that varies weight per key is injected
    through the messaging factory and needs no change to this module.

    ``key`` is the prefix identifying the level being weighted, so an
    implementation can give one org a larger share without also reweighting
    the connectors inside it: it is called with ``("org-7",)`` when choosing
    between orgs and ``("org-7", "connector-42")`` when choosing between that
    org's connectors.
    """

    def quantum_for(self, key: FairnessKey) -> int: ...


class EnqueueResult(Enum):
    """Outcome of :meth:`DRRScheduler.enqueue`, distinguishing the two ways a
    caller must react to a full buffer.

    ``ENTITY_FULL`` means only one key is backed up; ``BUFFER_FULL`` means the
    scheduler is at capacity across every key. Both tell the caller to stop
    reading the source it came from -- they are distinguished so the caller
    can log and meter them apart, and so a lane-aware read loop can stop only
    the lane that filled rather than every lane.
    """

    ACCEPTED = "accepted"
    ENTITY_FULL = "entity_full"
    BUFFER_FULL = "buffer_full"


@dataclass(frozen=True)
class FairSchedulerConfig:
    """Fair-scheduling knobs, read once at consumer startup from
    :class:`~app.services.messaging.config.MessagingEnvConfig`.

    Buffer sizes are deliberately modest: a buffered item holds a fully
    parsed envelope (a connector can ship a record's whole body inline), so
    the buffer is a real memory commitment, not a free queue. A full buffer
    stops reads; it never drops or re-publishes a message.
    ``max_per_entity_messages`` caps the innermost (leaf) key, so each
    connector gets its own allowance rather than sharing one per org.

    This dataclass defaults to disabled so that constructing a consumer
    without a config -- tests, direct instantiation -- keeps the pre-existing
    FIFO path. Production wiring goes through ``MessagingFactory``, which
    builds this from the environment; see
    ``MessagingEnvConfig.fair_scheduling_enabled`` for the shipped default.
    """

    enabled: bool = False
    # Outermost level first. ``orgId`` separates customers; ``connectorId``
    # separates individual users' syncs within one customer -- every user in
    # an org shares its ``orgId``, so grouping by org alone gives a
    # single-org install exactly one queue and no fairness at all.
    key_fields: tuple[str, ...] = ("orgId", "connectorId")
    default_quantum: int = 1
    max_buffered_messages: int = 2000
    max_per_entity_messages: int = 500
    # Last-resort escape for a delivery that never resolves its watermark
    # claim: without it, one such offset stalls every later commit on its
    # partition until the process restarts.
    max_dwell_seconds: float = 900.0
    # Allow several messages from one Kafka partition to be processed at
    # once. Off by default: it is safe only once the commit watermark is in
    # use (out-of-order completion within a partition is exactly what the
    # watermark exists for), and it replaces per-partition serialisation
    # with per-record serialisation, which is a real change in what the
    # consumer guarantees. See the Kafka consumer's dispatch phase.
    parallel_partitions: bool = False
