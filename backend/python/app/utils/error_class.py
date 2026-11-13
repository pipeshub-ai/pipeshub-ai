"""Why something failed, as one closed set of classes, and the one failure log line
(contract: docs/logging.md, "Failures").

``classify_error`` answers "why?" for a log line. ``services/messaging/error_classifier``
answers "retry?" and ``agents/agent_loop/error_classification`` gives user-facing text;
where the latter is also imported, call this module qualified:
``from app.utils import error_class`` then ``error_class.classify_error(exc)``.

SDK exception types (openai, anthropic, starlette, httpx, aiohttp) are looked up in
``sys.modules`` rather than imported: an SDK that was never loaded cannot have raised.
"""

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import socket
import sys
import traceback
from collections.abc import Iterator

from app.agent_loop_lib.core.exceptions import RunCancelled, TransportError
from app.services.base_client import ServiceAuthRefusedError
from app.services.messaging.error_classifier import CONTENT_ERRORS, extract_status_code
from app.utils.log_redaction import (
    URL_PATTERN,
    redact_error_for_log,
    redact_secrets_in_text,
)
from app.utils.logging_fields import event_fields
from app.utils.oauth_errors import is_permanent_grant_rejection

ERROR_CLASSES: frozenset[str] = frozenset({
    "auth_expired", "forbidden", "not_found", "rate_limited", "timeout", "upstream_5xx",
    "bad_request", "conflict", "unavailable", "cancelled", "misconfigured", "internal",
})

LEVEL_BY_CLASS: dict[str, int] = {
    "internal": logging.ERROR,
    "misconfigured": logging.ERROR,
    "cancelled": logging.INFO,
    **dict.fromkeys(ERROR_CLASSES - {"internal", "misconfigured", "cancelled"}, logging.WARNING),
}

# An OAuth client registration (ours) that the provider rejects, not a user's grant.
_CLIENT_REJECTED_CODES = frozenset({"invalid_client", "unauthorized_client", "incorrect_client_credentials"})
_LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"  # LLMNotConfiguredError.code; app.utils.llm is too heavy to import here
_OUR_BUGS = (KeyError, AttributeError, TypeError, NameError, AssertionError)
_STATUS_CLASS = {
    401: "auth_expired", 403: "forbidden", 404: "not_found", 410: "not_found", 408: "timeout",
    409: "conflict", 429: "rate_limited", 503: "unavailable", 504: "timeout",
}
_MAX_ERROR_MESSAGE = 300
_MAX_STACK = 8000
_LOGGED_ATTR = "_pipeshub_logged"


def level_for(error_class: str) -> int:
    """The level a failure of this class is logged at; an unknown class is ERROR."""
    return LEVEL_BY_CLASS.get(error_class, logging.ERROR)


def _status_class(status: int) -> str:
    if isinstance(status, bool) or status < 400:
        return "internal"
    if status in _STATUS_CLASS:
        return _STATUS_CLASS[status]
    return "bad_request" if status < 500 else "upstream_5xx"


def _types(module: str, *names: str) -> tuple[type, ...]:
    mod = sys.modules.get(module)
    return tuple(t for t in (getattr(mod, n, None) for n in names) if isinstance(t, type))


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc``, then its cause, else its context unless suppressed (``raise … from None``)."""
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        yield link
        link = link.__cause__ if link.__cause__ is not None else (None if link.__suppress_context__ else link.__context__)


def on_chain(exc: BaseException, target: BaseException) -> bool:
    """Whether ``target`` is ``exc`` or in the chain its class and text are read from (see ``_chain``)."""
    return any(link is target for link in _chain(exc))


def chain_has(exc: BaseException, *types: type[BaseException]) -> bool:
    """Whether ``exc`` or a link of its chain (see ``_chain``) is one of ``types``."""
    return any(isinstance(link, types) for link in _chain(exc))


def first_leaf(exc: BaseException) -> BaseException:
    """The first exception in ``exc`` that isn't a group: what a task group's failure was."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def innermost_type(exc: BaseException) -> str:
    """The type name of the deepest cause (ConnectError, gaierror, SSLError): what kind of failure, without its text.
    ``__cause__``, else ``__context__`` unless suppressed; a group counts as its first leaf."""
    seen: set[int] = set()
    current = first_leaf(exc)
    while id(current) not in seen:
        seen.add(id(current))
        deeper = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if deeper is None:
            break
        current = first_leaf(deeper)
    return type(current).__name__


def _classify_link(link: BaseException) -> str | None:
    stamped = getattr(link, "log_error_class", None)  # set by mark_error, or declared by an exception type
    if stamped in ERROR_CLASSES:
        return stamped
    if isinstance(link, (asyncio.CancelledError, concurrent.futures.CancelledError, RunCancelled)):
        return "cancelled"
    if (
        isinstance(link, ServiceAuthRefusedError)
        or getattr(link, "code", None) == _LLM_NOT_CONFIGURED
        or isinstance(link, _types("openai", "AuthenticationError", "PermissionDeniedError"))
        or isinstance(link, _types("anthropic", "AuthenticationError", "PermissionDeniedError"))
    ):
        return "misconfigured"
    status = extract_status_code(link)  # type: ignore[arg-type]
    error_code = getattr(link, "error_code", None)
    if isinstance(error_code, str) and error_code:
        if error_code.lower() in _CLIENT_REJECTED_CODES:
            return "misconfigured"
        if is_permanent_grant_rejection(str(link), error_code, status=status if isinstance(status, int) else None):
            return "auth_expired"
    if isinstance(link, (TimeoutError, *_types("httpx", "TimeoutException"), *_types("aiohttp", "ServerTimeoutError"),
                         *_types("openai", "APITimeoutError"))):
        return "timeout"
    if isinstance(link, (*_OUR_BUGS, *_types("pydantic", "ValidationError"))):
        return "internal"
    # Our own HTTPException 5xx wraps our own failure: keep walking, it is not an upstream answer.
    if (
        isinstance(status, int) and not isinstance(status, bool)
        and not (status >= 500 and isinstance(link, _types("starlette.exceptions", "HTTPException")))
    ):
        return _status_class(status)
    if isinstance(link, (ConnectionError, socket.gaierror, *_types("httpx", "TransportError"),
                         *_types("aiohttp", "ClientConnectionError"), *_types("openai", "APIConnectionError"))):
        return "unavailable"
    if isinstance(link, (*CONTENT_ERRORS, json.JSONDecodeError, UnicodeDecodeError)):
        return "bad_request"
    return None


def classify_error(exc_or_status: BaseException | int) -> str:
    """One of :data:`ERROR_CLASSES`. A status code maps by the table; an exception by the
    first decisive link of its chain, outermost first. Never raises."""
    try:
        if not isinstance(exc_or_status, BaseException):
            return _status_class(exc_or_status)
        links = list(_chain(exc_or_status))
        for link in links:
            found = _classify_link(link)
            if found is not None:
                return found
        if any(isinstance(link, TransportError) and link.retryable for link in links):
            return "unavailable"
    except Exception:
        pass
    return "internal"


def mark_error(exc: BaseException, error_class: str | None = None, *, hide_text: bool = False) -> None:
    """Record on ``exc`` the class a lower layer (a database client) found, and whether its text can quote what was sent.
    :func:`classify_error` then answers that class and :func:`log_failure` leaves the text out, for every handler that logs the
    chain. Sets only ``log_error_class`` and ``log_hide_text``. Without ``error_class`` the class is left to
    :func:`classify_error` and only ``hide_text`` is stamped. The first class stamped wins; hiding can be added later. Never raises."""
    with contextlib.suppress(AttributeError, TypeError):
        if error_class is not None and not is_stamped(exc):
            exc.log_error_class = error_class  # type: ignore[attr-defined]
        if hide_text:
            exc.log_hide_text = True  # type: ignore[attr-defined]


def is_stamped(exc: BaseException) -> bool:
    """:func:`mark_error` already gave this instance a class: a lower layer recorded it (a class-level ``log_error_class``,
    declared by an exception type, does not count)."""
    return "log_error_class" in getattr(exc, "__dict__", {})


def _hides_text(exc: BaseException) -> bool:
    """A link of the chain, or a member of an exception group in it, was stamped as able to quote sent values."""
    for link in _chain(exc):
        if getattr(link, "log_hide_text", False) is True:
            return True
        # every member, not only the first (first_leaf): the stamped one may be any of them
        if isinstance(link, BaseExceptionGroup) and any(_hides_text(member) for member in link.exceptions):
            return True
    return False


def _chain_text(exc: BaseException) -> str:
    return "; caused by ".join(f"{type(link).__name__}: {link}" for link in _chain(exc))


def _prior_level(exc: BaseException) -> int | None:
    levels: list[int] = []
    link: BaseException | None = exc
    seen: set[int] = set()
    while link is not None and id(link) not in seen:  # explicit causes only: a cleanup error is a new failure
        seen.add(id(link))
        level = getattr(link, _LOGGED_ATTR, None)
        if isinstance(level, int):
            levels.append(level)
        link = link.__cause__
    return max(levels) if levels else None


def logged_level(exc: BaseException) -> int | None:
    """The highest level :func:`log_failure` already wrote ``exc``, or an exception it was raised
    ``from``, at; None if never. For a per-unit line that must not be demoted when several units
    fail with one shared exception."""
    return _prior_level(exc)


def already_logged(exc: BaseException, level: int) -> bool:
    """Whether :func:`log_failure` already wrote ``exc`` (or one it was raised ``from``) at ``level`` or above.
    A per-unit line (one tool call, one request, one server's listing) then logs with ``log_event`` at its own
    level: no second stack, no text, never demoted."""
    prior = _prior_level(exc)
    return prior is not None and prior >= level


_CAUSE_SEPARATOR = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT_SEPARATOR = "\nDuring handling of the above exception, another exception occurred:\n\n"


def _stack_without_messages(exc: BaseException) -> str:
    """The traceback with every exception line cut to its type (frames kept), oldest first as Python prints it."""
    blocks: list[str] = []
    links = list(_chain(exc))
    for older, newer in zip(reversed(links), [*reversed(links[:-1]), None]):
        frames = "".join(traceback.format_tb(older.__traceback__)) if older.__traceback__ else ""
        head = f"Traceback (most recent call last):\n{frames}" if frames else ""
        blocks.append(f"{head}{type(older).__qualname__}: <message omitted>\n")
        if newer is not None:
            blocks.append(_CAUSE_SEPARATOR if newer.__cause__ is older else _CONTEXT_SEPARATOR)
    return "".join(blocks)


def _stack_text(exc: BaseException, *, with_messages: bool) -> str:
    # URLs and secrets only: the account-id pattern would eat file and module names.
    if with_messages:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        text = _stack_without_messages(exc)
    return redact_secrets_in_text(URL_PATTERN.sub("[url]", text.rstrip("\n")))[-_MAX_STACK:]


def log_failure(
    logger: logging.Logger, event: str, exc: BaseException, /, *,
    level: int | None = None, error_class: str | None = None, include_error_text: bool = True, **fields: object,
) -> None:
    """The one failure line, at the layer that handles ``exc``.

    Adds ``error_class`` (an explicit one wins), ``error_type``, ``error_message`` (message and
    cause chain, redacted) and ``outcome="failure"`` (also when ``outcome=None`` is passed). The level is :func:`level_for` unless given.
    The stack is written once, on the first line at ERROR or above. A later call for the same
    exception, or one raised ``from`` it, logs at DEBUG unless it asks for a higher level than
    the one already written. ``include_error_text=False`` leaves ``error_message`` out, for errors
    whose text can echo what was sent (tool arguments in an MCP or OpenAPI server's answer); the
    stack written at ERROR then keeps its frames but shows each exception as ``Type: <message omitted>``, and the record
    carries that stack as ``exc_text`` only, with no ``exc_info``. A chain that :func:`mark_error` stamped with
    ``hide_text`` (a database driver's error) is always logged that way. Never raises: it runs inside ``except`` blocks.
    """
    try:
        include_error_text = include_error_text and not _hides_text(exc)
        cls = error_class or classify_error(exc)
        want = level if level is not None else level_for(cls)
        prior = _prior_level(exc)
        emitted = want if prior is None or want > prior else logging.DEBUG
        if not logger.isEnabledFor(emitted):
            return
        with_stack = emitted >= logging.ERROR and (prior is None or prior < logging.ERROR)
        extra = event_fields(event, {
            **fields,
            "outcome": fields.get("outcome") or "failure",
            "error_class": cls,
            "error_type": type(exc).__name__,
            "error_message": (
                redact_error_for_log(_chain_text(exc), max_len=_MAX_ERROR_MESSAGE) if include_error_text else None
            ),
        })
        filename, lineno, func, _ = logger.findCaller(stacklevel=2)
        # Without error text, no exc_info: a handler or formatter that reads it would print the raw message.
        exc_info = (type(exc), exc, exc.__traceback__) if with_stack and include_error_text else None
        record = logger.makeRecord(logger.name, emitted, filename, lineno, event, (), exc_info, func, extra)
        if with_stack:
            record.exc_text = _stack_text(exc, with_messages=include_error_text)  # the formatters print it instead of formatting exc_info unredacted
        logger.handle(record)
        with contextlib.suppress(AttributeError, TypeError):
            setattr(exc, _LOGGED_ATTR, max(prior or 0, emitted))
    except Exception:
        # Text-free on purpose: this path skips the masking and include_error_text.
        with contextlib.suppress(Exception):
            logger.log(level or logging.ERROR, event, extra={"error_type": type(exc).__name__}, stacklevel=2)
