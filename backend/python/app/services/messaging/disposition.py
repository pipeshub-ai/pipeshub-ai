"""What happens to a message when a consumer gives up on it.

The rule this module exists to enforce: a message is never abandoned silently.
Whenever a consumer discards a message it must first tell the
``AbandonedMessageSink``, so whatever the message referred to is left in a
terminal, visible state instead of sitting in a status nothing ever revisits.

Without that, a discarded record event left its record on the status it was
created with — QUEUED — which neither the stale-record scan (IN_PROGRESS only)
nor the inactive-connector sweep looks at, so the record waited for ever. The
only log line named the broker's message id, which is unresolvable once the
entry is gone, so there was no way to tell afterwards which records were lost.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.config import StreamMessage


@runtime_checkable
class AbandonedMessageSink(Protocol):
    """Notified when a consumer abandons a message for good.

    Implemented by whoever owns the state the message refers to — for record
    events that is ``RecordEventHandler``, which puts the record into a terminal
    status. Consumers depend on this protocol rather than on records, so they
    stay broker-generic and domain-free.
    """

    async def on_message_abandoned(
        self,
        message: StreamMessage | None,
        *,
        reason: str,
        attempts: int,
    ) -> None:
        """Handle a message that will never be processed.

        ``message`` is ``None`` when the envelope could not even be parsed.
        Implementations must not raise: abandonment happens on the way to an
        acknowledgement that has to go through regardless.
        """
        ...


async def notify_abandoned(
    sink: AbandonedMessageSink | None,
    logger: Logger,
    message: StreamMessage | None,
    *,
    reason: str,
    attempts: int,
) -> None:
    """Tell the sink a message is being given up on, without ever raising.

    Every caller is on its way to an acknowledgement it cannot skip — leaving
    the entry pending because the notification failed would stall the consumer,
    which is worse than the missing status write. So the failure is logged
    loudly and swallowed.
    """
    if sink is None:
        return
    try:
        await sink.on_message_abandoned(message, reason=reason, attempts=attempts)
    except Exception as e:
        logger.error(
            "Failed to record abandonment of a message (%s): %s",
            reason,
            e,
            exc_info=True,
        )


def describe_message(message: StreamMessage | None) -> str:
    """Short identifier for logs: the record the message is about, if any.

    Dead-letter logs carried only the broker's own message id, which cannot be
    resolved back to a record once the entry is gone.
    """
    if message is None:
        return "record=<unparseable>"
    payload = message.payload or {}
    record_id = payload.get("recordId")
    if record_id:
        return f"record={record_id} event={message.eventType}"
    return f"record=<none> event={message.eventType}"
