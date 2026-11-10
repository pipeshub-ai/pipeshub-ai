"""One delivery of one broker message: its log context and its one ``message.end`` line
(contract: docs/logging.md, "Messaging consumers").

- The consume loop wraps each delivery in :func:`delivery`, which binds ``topic`` and ``message_id``.
- The parse step calls :func:`bind_envelope` (the producer's request id and claimed org, unverified),
  then :func:`bind_opened` once the envelope is verified (the OrgContext, ``event_type``, the payload's
  ``record_id`` and ``connector_id``). Both raise outside a delivery: the OrgContext is the handler's
  trust boundary, so a caller that forgot the delivery must not run the handler with no org.
- Inner layers :func:`note` what they learned; only the decision sites call :func:`end`, after the
  broker call returns. A delivery that exits without ``end`` is ended as a retry (``cancelled`` on
  cancellation): an un-acked entry stays pending and an uncommitted offset is read again.
- The indexing consumers use :func:`bound_message`: the same binding, no end line (theirs is the
  indexing summary).
"""

import asyncio
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core import org_context
from app.core.envelope import ctx_of
from app.utils.error_class import classify_error, level_for, log_failure, logged_level
from app.utils.logging_fields import log_event, log_fields
from app.utils.request_context import bind_context, context_from_envelope, reset_context

EVENT_END = "message.end"


@dataclass(slots=True)
class _Delivery:
    """One open delivery. A task started inside it copies a context that points at this same object, so
    only the consumer's decision sites call :func:`end`: anything else may only :func:`note`."""

    logger: logging.Logger
    stack: ExitStack
    facts: dict[str, object]
    started: float = field(default_factory=time.perf_counter)
    ended: bool = False


_current: ContextVar[_Delivery | None] = ContextVar("message_delivery", default=None)


@contextmanager
def delivery(logger: logging.Logger, *, topic: str, message_id: str, attempt: int | None = None) -> Iterator[None]:
    """One delivery of one message. ``attempt`` is this delivery's number when the caller knows it
    (1 for a new read); a decision site can say it later with :func:`note` or :func:`end`."""
    with ExitStack() as stack:
        stack.enter_context(log_fields(topic=topic, message_id=message_id))
        current = _Delivery(logger, stack, {"attempt": attempt})
        token = _current.set(current)
        try:
            yield
        except asyncio.CancelledError as e:
            _end(current, "retry", reason="cancelled", exc=e)
            raise
        except BaseException as e:
            _end(current, "retry", reason="exception", exc=e)
            raise
        else:
            _end(current, "retry", reason=current.facts.get("reason") or "exception")
        finally:
            _current.reset(token)


def bind_envelope(envelope: dict[str, Any]) -> None:
    """The producer's request id and claimed org, for the rest of the delivery. Unverified: a
    correlation id, and the org lines show until :func:`bind_opened` binds the verified one."""
    _open().stack.callback(reset_context, bind_context(context_from_envelope(envelope)))


def bind_opened(org: org_context.OrgContext | None, message: Mapping[str, object]) -> None:
    """After verification: the OrgContext (None stays unbound and fails closed) and the message's ids."""
    current = _open()
    current.stack.enter_context(org_context.bind_optional(org))
    current.stack.enter_context(log_fields(**_ids(message.get("eventType"), message.get("payload"))))


def note(**facts: object) -> None:
    """What an inner layer learned, for the end line: ``reason``, ``code``, ``size_bytes``, ``attempt``,
    ``exc``, or ``outcome="skipped"`` for an ack that did no work. No-op outside a delivery."""
    current = _current.get()
    if current is not None and not current.ended:
        current.facts.update(facts)


def end(disposition: str, **facts: object) -> None:
    """Write ``message.end`` once: ``disposition`` is ``ack``, ``retry`` or ``dead_letter``. Later
    calls, and calls outside a delivery, do nothing."""
    current = _current.get()
    if current is not None:
        _end(current, disposition, **facts)


@contextmanager
def bound_message(message: Any) -> Iterator[None]:  # noqa: ANN401 - a parsed StreamMessage, or None
    """The same root, org and ids as a delivery binds, for a block with no ``message.end``."""
    if message is None:
        yield
        return
    token = bind_context(context_from_envelope({"requestId": message.requestId, "orgId": message.orgId}))
    try:
        with org_context.bind_optional(ctx_of(message)), log_fields(**_ids(message.eventType, message.payload)):
            yield
    finally:
        reset_context(token)


def _open() -> _Delivery:
    current = _current.get()
    if current is None or current.ended:
        raise RuntimeError("message_log: bind inside an open message_log.delivery()")
    return current


def _ids(event_type: object, payload: object) -> dict[str, str | None]:
    payload = payload if isinstance(payload, Mapping) else {}
    return {
        "event_type": _str(event_type),
        "record_id": _str(payload.get("recordId")),
        "connector_id": _str(payload.get("connectorId")),
    }


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _level(disposition: str, error_class: str | None) -> int:
    if disposition == "ack" or error_class == "cancelled":
        return logging.INFO
    if disposition == "dead_letter":
        return logging.ERROR  # acknowledged and dropped: nothing else records it
    return max(logging.WARNING, level_for(error_class)) if error_class else logging.WARNING


def _quotes_input(exc: BaseException) -> bool:
    """A pydantic error anywhere in the chain: its text quotes the payload's values."""
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        if isinstance(link, ValidationError):
            return True
        seen.add(id(link))
        link = link.__cause__ or link.__context__
    return False


def _end(current: _Delivery, disposition: str, **overrides: object) -> None:
    if current.ended:
        return
    current.ended = True
    facts = {**current.facts, **overrides}
    current.facts.clear()  # a task that copied the context must not keep the exception alive
    exc = facts.pop("exc", None)
    skipped = facts.pop("outcome", None)
    fields = {
        "outcome": (skipped or "success") if disposition == "ack" else "failure",
        "disposition": disposition,
        **facts,
        "duration_ms": round((time.perf_counter() - current.started) * 1000),
    }
    if not isinstance(exc, BaseException):
        log_event(current.logger, EVENT_END, level=_level(disposition, None), **fields)
        return
    cls = classify_error(exc)
    level = _level(disposition, cls)
    prior = logged_level(exc)
    if prior is not None and prior >= level:
        # The handler logged it (with any stack) at this level or higher: keep this line's level, add no text.
        log_event(current.logger, EVENT_END, level=level, error_class=cls, error_type=type(exc).__name__, **fields)
        return
    log_failure(
        current.logger, EVENT_END, exc, level=level, error_class=cls,
        include_error_text=fields.get("reason") != "unparseable" and not _quotes_input(exc), **fields,
    )
