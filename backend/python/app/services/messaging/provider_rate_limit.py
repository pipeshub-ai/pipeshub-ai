"""A model provider's rate limit as a hand-back of the message to the broker."""

from __future__ import annotations

from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt


class ProviderRateLimitRequeue(RequeueWithoutAttempt):
    """A model provider asked for a wait longer than a call may spend (its Retry-After): the record comes back after it.

    Not a failure, so no attempt is counted, and the broker holds the message for the provider's wait instead of redelivering
    it after the 15 s retry backoff (which would spend the record's attempts on a provider that said when it is back). It
    counts toward the delivery backstop, which bounds how often it can return.
    """

    pauses_consumer = False  # one record's wait; other records keep flowing
