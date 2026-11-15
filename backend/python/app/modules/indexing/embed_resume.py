"""Resuming an interrupted embedding run.

A large record can take longer to embed than one attempt is allowed to run. Its blocks are written
to the record blob before any vector is, and every vector point has an id derived from what it
holds, so a retry of the *same event* can load the stored blocks, see which points already exist
and embed only the rest, instead of parsing and embedding everything again.

What lets a retry trust the points it finds is a marker on the record node, written by the vector
store once it knows which points the record needs and before the first one is written. The marker
is a set of flat, top-level scalar fields so that a partial write leaves the others intact on every
graph provider (a nested object is merged on one provider and replaced on the others):

* ``embedVrid`` the virtual record the points were written under;
* ``embedRev`` the ``contentRev`` the blocks were parsed from;
* ``embedKey`` the embedding setup (provider, model, dimension, sparse model, point policy);
* ``embedDigest`` a digest of the sorted ids of the points the record needs;
* ``embedTotal`` how many points that is;
* ``embedDeliveryId`` the identity of the event being retried;
* ``embedStartedMs`` when the chain of attempts began;
* ``embedSeconds`` wall seconds of embedding spent by the attempts of this chain that were cut after
  making progress, and ``embedUncounted`` how many there were. They are the clock that ends a chain
  which keeps making progress, so they live on the node, not in a message or a process;
* ``embedDone`` how many points exist, for display only. Nothing reads it to decide.

A retry resumes only when it is a redelivery of the event that wrote the marker (the delivery id
matches), the bytes are the same revision, the node still names the vrid, and the digest was
written. A new event (a reindex, a connector update, an edit) has a new delivery id, so a marker
never makes it skip work. The marker is cleared when the record is indexed; one that outlives its
event is inert for that reason.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

EMBED_VRID = "embedVrid"
EMBED_REV = "embedRev"
EMBED_KEY = "embedKey"
EMBED_DIGEST = "embedDigest"
EMBED_TOTAL = "embedTotal"
EMBED_DELIVERY_ID = "embedDeliveryId"
EMBED_STARTED_MS = "embedStartedMs"
EMBED_SECONDS = "embedSeconds"
EMBED_UNCOUNTED = "embedUncounted"
EMBED_DONE = "embedDone"

EMBED_FIELDS: tuple[str, ...] = (
    EMBED_VRID,
    EMBED_REV,
    EMBED_KEY,
    EMBED_DIGEST,
    EMBED_TOTAL,
    EMBED_DELIVERY_ID,
    EMBED_STARTED_MS,
    EMBED_SECONDS,
    EMBED_UNCOUNTED,
    EMBED_DONE,
)

#: Payload keys of a point written by an attempt that carries a marker (under ``metadata``).
POINT_REV = "contentRev"
POINT_KEY = "embedKey"

#: Payload key of the event payload where each consumer puts the identity of the delivery.
DELIVERY_ID_KEY = "_delivery_id"
#: Set by stale recovery on the event it republishes: the delivery id of the interrupted run.
RESUME_DELIVERY_ID_KEY = "_resume_delivery_id"


def cleared_fields() -> dict[str, None]:
    """The write that ends a chain: every field of the marker set to null."""
    return dict.fromkeys(EMBED_FIELDS)


#: Indexing statuses that end a chain of attempts: nothing will retry the event that wrote a marker.
_ENDS_A_CHAIN = frozenset({"COMPLETED", "EMPTY", "FAILED", "PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED"})


def chain_end_fields(indexing_status: str | None) -> dict[str, None]:
    """The marker clear that belongs in a write of ``indexing_status``; empty for a status a retry follows."""
    return cleared_fields() if indexing_status in _ENDS_A_CHAIN else {}


def marker_of(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The marker fields of a record node as read, or ``{}`` when it carries none."""
    if not record or not record.get(EMBED_VRID):
        return {}
    return {name: record.get(name) for name in EMBED_FIELDS}


def digest_of(point_ids: Iterable[str]) -> str:
    """Order-insensitive digest of a set of point ids."""
    h = hashlib.sha256()
    for point_id in sorted(set(point_ids)):
        h.update(point_id.encode())
        h.update(b"\n")
    return h.hexdigest()[:32]


def stamp_delivery_id(payload: dict[str, Any], stable_message_id: str) -> None:
    """Put the identity of this delivery in the event payload, before the handler reads it.

    ``stable_message_id`` is what the consumer already uses to follow one event across its own
    re-queues. A republish by stale recovery continues an interrupted run, so it names that run's
    delivery id in ``_resume_delivery_id`` and that wins.
    """
    resumed = payload.get(RESUME_DELIVERY_ID_KEY)
    payload[DELIVERY_ID_KEY] = str(resumed) if resumed else str(stable_message_id)


def may_resume(
    marker: Mapping[str, Any],
    *,
    delivery_id: str | None,
    rev: str,
    node_vrid: str | None,
    force_reindex: bool,
) -> bool:
    """Whether this attempt continues the run that wrote ``marker``.

    Every condition fails closed: a marker without a digest was written by an attempt that died
    before it knew which points the record needs, so nothing it left can be trusted.
    """
    return bool(
        marker
        and delivery_id
        and not force_reindex
        and marker.get(EMBED_DELIVERY_ID) == delivery_id
        and marker.get(EMBED_REV) == rev
        and marker.get(EMBED_VRID)
        and marker.get(EMBED_VRID) == node_vrid
        and marker.get(EMBED_DIGEST)
    )


@dataclass
class EmbedAttempt:
    """What the vector store needs to know about the attempt it runs inside."""

    delivery_id: str | None
    rev: str
    vrid: str
    #: The marker the node carried before this attempt began (``{}`` when none).
    prior: dict[str, Any] = field(default_factory=dict)
    #: The handler skipped the parse and loaded the stored blocks.
    resume: bool = False
    #: False for record kinds whose stored blocks are not the whole content (code, SQL tables).
    eligible: bool = True
    #: Set by the vector store while it writes: the payload fields every point of this run carries.
    stamp: dict[str, str] | None = None


_attempt: ContextVar[EmbedAttempt | None] = ContextVar("embed_attempt", default=None)


def current_attempt() -> EmbedAttempt | None:
    return _attempt.get()


def begin_attempt(attempt: EmbedAttempt) -> None:
    _attempt.set(attempt)


def end_attempt() -> None:
    # Set back rather than reset by token: an async generator closed from another context cannot reset.
    _attempt.set(None)
