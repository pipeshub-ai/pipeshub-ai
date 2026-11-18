"""A model provider's rate limit as a hand-back of the message to the broker."""

from __future__ import annotations

from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt


class ProviderRateLimitRequeue(RequeueWithoutAttempt):
    """A model provider asked for a wait longer than a call may spend (its Retry-After): the record comes back after it.

    Not a failure, so no attempt is counted, and the broker holds the message for the provider's wait instead of redelivering
    it after the 15 s retry backoff (which would spend the record's attempts on a provider that said when it is back). It
    does not use the broker's redelivery budget either (Kafka, Redis Streams and PostgreSQL alike). What bounds it is the
    time the record has spent rate-limited, added up by the record handler: past ``RATE_LIMIT_MAX_WAIT_S`` it fails.
    """

    pauses_consumer = False  # one record's wait; other records keep flowing
    counts_toward_backstop = False
