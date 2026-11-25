"""The hand-back of a refresh event whose purge another delivery is still running."""
from __future__ import annotations

from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt


class RefreshPurgeHeld(RequeueWithoutAttempt):
    """Another delivery of the same refresh event is purging and has not finished.

    Not an attempt and not a failure: the record is handed back after a delay and the purge ends with its holder or,
    for a dead holder, with its lease (120 s without renewal), after which the redelivery purges itself.
    """

    counts_toward_backstop = False
    pauses_consumer = False
