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
import re
import socket
import sys
import textwrap
import traceback
import weakref
from collections.abc import Iterator

from app.services.base_client import ServiceAuthRefusedError
from app.services.messaging.error_classifier import CONTENT_ERRORS, extract_status_code
from app.utils.log_redaction import (
    URL_PATTERN,
    redact_error_for_log,
    redact_secrets_in_text,
)
from app.utils.logging_fields import event_fields, log_event
from app.utils.oauth_errors import (
    CLIENT_REJECTED_ERROR_CODES,
    is_permanent_grant_rejection,
)

# The kernel module itself, not the legacy app.agent_loop_lib path: that package installs the agent loop's
# composition, and the harness worker imports this module while it must stay free of the loop stack (D-7).
from harness_core.kernel.core.exceptions import RunCancelled, TransportError

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
_LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"  # LLMNotConfiguredError.code; app.utils.llm is too heavy to import here
OUR_BUGS = (KeyError, AttributeError, TypeError, NameError, AssertionError)  # our programming errors: they stop a classifier's walk
_STATUS_CLASS = {
    401: "auth_expired", 403: "forbidden", 404: "not_found", 410: "not_found", 408: "timeout",
    409: "conflict", 429: "rate_limited", 503: "unavailable", 504: "timeout",
}
_MAX_ERROR_MESSAGE = 300
_MAX_STACK = 8000
_LOGGED_ATTR = "_pipeshub_logged"
# A record's exception once a text-free stack replaced its exc_info. Kept off the record: a filter that rewrites its
# attributes (litellm's on uvicorn.error stringifies every extra) or a handler that copies or pickles it never sees it.
# shortcut: an entry never dies when its record is reachable from its own exception's traceback (a frame that holds the
# record, such as a makeRecord local in the handling frame); no app/ path does that. Upgrade to a cycle-safe holder if one does.
_HIDDEN_EXCEPTIONS: "weakref.WeakKeyDictionary[logging.LogRecord, BaseException]" = weakref.WeakKeyDictionary()
_MAX_GROUP_WIDTH = 15  # traceback's own max_group_width and max_group_depth
_MAX_GROUP_DEPTH = 10
# A provider's machine code (content_filter, DeploymentNotFound). The server writes it, so digits, dots and hyphens are
# refused: they are what account ids, keys, tokens and request ids are made of.
_PROVIDER_CODE = re.compile(r"[A-Za-z][A-Za-z_]{1,63}")


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


def chain_link(exc: BaseException, *types: type[BaseException]) -> BaseException | None:
    """The first of ``exc`` and the links of its chain (see ``_chain``) that is one of ``types``, or None."""
    return next((link for link in _chain(exc) if isinstance(link, types)), None)


def chain_has(exc: BaseException, *types: type[BaseException]) -> bool:
    """Whether ``exc`` or a link of its chain (see ``_chain``) is one of ``types``."""
    return chain_link(exc, *types) is not None


def first_leaf(exc: BaseException) -> BaseException:
    """The first exception in ``exc`` that isn't a group: what a task group's failure was."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def has_chain(exc: BaseException) -> bool:
    """Whether ``exc`` was raised from, or while handling, another exception: when ``cause_type`` says something."""
    return exc.__cause__ is not None or (exc.__context__ is not None and not exc.__suppress_context__)


def quotes_content(exc: BaseException) -> bool:
    """Whether a link of the chain, or a member of an exception group in it, is a content error or a validation error:
    its text, and every text above it, can quote the document's name or the message's values."""
    # The parsing service's error message quotes the record's name, and its client copies it (ParsingClientError).
    quoting = (*CONTENT_ERRORS, *_types("pydantic", "ValidationError"),
               *_types("app.services.parsing.client", "ParsingClientError"))
    return any(isinstance(link, quoting) for link in _links_and_members(exc))


def _links_and_members(exc: BaseException) -> Iterator[BaseException]:
    """Each link of ``exc``'s chain and, through exception groups, of every member's chain, once each: one visited set
    across cause, context and member edges, so a member that points back at its group cannot loop."""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        for link in _chain(pending.pop()):
            if id(link) in seen:
                continue
            seen.add(id(link))
            yield link
            if isinstance(link, BaseExceptionGroup):
                pending.extend(link.exceptions)


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
        if error_code.lower() in CLIENT_REJECTED_ERROR_CODES:
            return "misconfigured"
        if is_permanent_grant_rejection(str(link), error_code, status=status if isinstance(status, int) else None):
            return "auth_expired"
    if isinstance(link, (TimeoutError, *_types("httpx", "TimeoutException"), *_types("aiohttp", "ServerTimeoutError"),
                         *_types("openai", "APITimeoutError"))):
        return "timeout"
    if isinstance(link, (*OUR_BUGS, *_types("pydantic", "ValidationError"))):
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


def provider_code(exc: BaseException) -> str | None:
    """The provider's machine code for the failure (``content_filter``, ``context_length_exceeded``,
    ``overloaded_error``): openai's ``code``, else ``type`` (anthropic's error type), from the first link that has
    one. A value that isn't a plain word is ``"other"``: never an id, a key or message text. Never raises: callers run it
    on the failure path, so an SDK property that raises is skipped like a missing one."""
    try:
        for link in _chain(exc):
            for attr in ("code", "type"):
                try:
                    value = getattr(link, attr, None)
                except Exception:
                    continue
                if isinstance(value, str) and value:
                    plain = _PROVIDER_CODE.fullmatch(value) is not None and redact_secrets_in_text(value) == value
                    return value if plain else "other"
    except Exception:
        pass
    return None


def failure_reason(exc: BaseException, error_class: str | None = None) -> str:
    """Why ``exc`` failed, without its text: class, provider code, innermost type and HTTP status, as in
    ``bad_request: content_filter (BadRequestError, HTTP 400)``. For what leaves a log line's reach (frames, tool
    results, run results, traces): a provider's or a driver's message can quote the prompt, an email or a key."""
    try:
        status = next((s for link in _chain(exc)
                       if isinstance(s := extract_status_code(link), int) and not isinstance(s, bool)), None)  # type: ignore[arg-type]
        code = provider_code(exc)
        head = (error_class or classify_error(exc)) + (f": {code}" if code else "")
        return f"{head} ({innermost_type(exc)}" + (f", HTTP {status})" if status is not None else ")")
    except Exception:  # an SDK property read by extract_status_code or provider_code can raise
        return f"internal ({type(exc).__name__})"


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


def chain_stamped(exc: BaseException) -> bool:
    """A lower layer recorded ``exc``, or an exception it was raised ``from``: :func:`mark_error` gave a link its class (a
    client wrapper wrote ``db.query.failed``). Explicit causes only: an error raised while handling a recorded one, without
    ``from``, is a new failure (a fallback that failed in turn)."""
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        if is_stamped(link):
            return True
        seen.add(id(link))
        link = link.__cause__
    return False


def _hides_text(exc: BaseException) -> bool:
    """A link of the chain, or a member of an exception group in it, was stamped as able to quote sent values."""
    # every member, not only the first (first_leaf): the stamped one may be any of them
    return any(getattr(link, "log_hide_text", False) is True for link in _links_and_members(exc))


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


def _stack_without_messages(exc: BaseException, seen: set[int] | None = None, depth: int = 0) -> str:
    """The traceback with every exception line cut to its type (frames kept), oldest first as Python prints it, with each
    exception group's members below it. One ``seen`` set across cause, context and member edges renders each exception once."""
    seen = set() if seen is None else seen
    links: list[BaseException] = []
    for link in _chain(exc):
        if id(link) in seen:
            break
        seen.add(id(link))
        links.append(link)
    blocks: list[str] = []
    for older, newer in zip(reversed(links), [*reversed(links[:-1]), None]):
        frames = "".join(traceback.format_tb(older.__traceback__)) if older.__traceback__ else ""
        head = f"Traceback (most recent call last):\n{frames}" if frames else ""
        blocks.append(f"{head}{type(older).__qualname__}: <message omitted>\n")
        if isinstance(older, BaseExceptionGroup):
            blocks.append(_members_without_messages(older, seen, depth + 1))
        if newer is not None:
            blocks.append(_CAUSE_SEPARATOR if newer.__cause__ is older else _CONTEXT_SEPARATOR)
    return "".join(blocks)


def _members_without_messages(group: BaseExceptionGroup, seen: set[int], depth: int) -> str:
    """A group's members as Python numbers and indents them, within its width and depth caps."""
    if depth > _MAX_GROUP_DEPTH:
        return f"... (max_group_depth is {_MAX_GROUP_DEPTH})\n"
    blocks: list[str] = []
    for number, member in enumerate(group.exceptions[:_MAX_GROUP_WIDTH], 1):
        if id(member) not in seen:
            stack = _stack_without_messages(member, seen, depth)
            blocks.append(f"+---------------- {number} ----------------\n{textwrap.indent(stack, '| ', lambda _: True)}")
    if len(group.exceptions) > _MAX_GROUP_WIDTH:
        blocks.append(f"+---------------- ... ----------------\n| and {len(group.exceptions) - _MAX_GROUP_WIDTH} more exceptions\n")
    return "".join(blocks) + "+------------------------------------\n" if blocks else ""


def _stack_text(exc: BaseException, *, with_messages: bool) -> str:
    # URLs and secrets only: the account-id pattern would eat file and module names.
    if with_messages:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        text = _stack_without_messages(exc)
    return redact_secrets_in_text(URL_PATTERN.sub("[url]", text.rstrip("\n")))[-_MAX_STACK:]


def stack_text_without_messages(exc: BaseException) -> str:
    """``exc``'s stack with every exception line cut to its type (frames kept), as ``log_failure(include_error_text=False)``
    writes it: for a sink outside the logs (a trace) that wants where it failed, not what the error said."""
    return _stack_text(exc, with_messages=False)


def record_exception(record: logging.LogRecord) -> BaseException | None:
    """The exception a record was logged with, also when a text-free stack replaced its ``exc_info``
    (:func:`hide_record_text`, :func:`log_failure`), for a filter that reads it (uvicorn's duplicate-line filter).
    In-process only: the exception still holds the driver's text, so never format or serialize what this returns."""
    if isinstance(record.exc_info, tuple) and record.exc_info[1] is not None:
        return record.exc_info[1]
    return _HIDDEN_EXCEPTIONS.get(record)


def hide_record_text(record: logging.LogRecord) -> None:
    """For a ``logger.exception`` or ``exc_info=True`` record whose chain hides its text (a stamped driver error, a
    ``PermissionVerificationUnavailableError``): the stack :func:`log_failure` writes, as ``exc_text``, and no
    ``exc_info``. Fails closed, as :func:`log_failure` does: a chain whose stamp cannot be read is treated as hidden (the
    walk stops at the link that raised, before a stamped one further down), and a stack that cannot be built is the type
    alone. Never raises."""
    exc = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
    if exc is None:
        return
    try:
        if not _hides_text(exc):
            return
    except Exception:
        pass  # the stamp cannot be read: hide
    try:
        stack = _stack_text(exc, with_messages=False)
    except Exception:
        stack = f"{type(exc).__qualname__}: <stack omitted>"
    record.exc_info, record.exc_text = None, stack
    _HIDDEN_EXCEPTIONS[record] = exc


def log_failure(
    logger: logging.Logger, event: str, exc: BaseException, /, *,
    level: int | None = None, error_class: str | None = None, include_error_text: bool = True, stacklevel: int = 1,
    **fields: object,
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
    ``hide_text`` (a database driver's error) is always logged that way. ``stacklevel`` is ``logging``'s: a
    wrapper that logs for its caller (``harness_core.log_lines``) passes how many frames up the caller is.
    Never raises: it runs inside ``except`` blocks.
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
        filename, lineno, func, _ = logger.findCaller(stacklevel=1 + stacklevel)
        # Without error text, no exc_info: a handler or formatter that reads it would print the raw message.
        exc_info = (type(exc), exc, exc.__traceback__) if with_stack and include_error_text else None
        record = logger.makeRecord(logger.name, emitted, filename, lineno, event, (), exc_info, func, extra)
        if with_stack and exc_info is None:  # only where exc_info was dropped: a stackless line must not keep the exception alive
            _HIDDEN_EXCEPTIONS[record] = exc
        if with_stack:
            record.exc_text = _stack_text(exc, with_messages=include_error_text)  # the formatters print it instead of formatting exc_info unredacted
        logger.handle(record)
        with contextlib.suppress(AttributeError, TypeError):
            setattr(exc, _LOGGED_ATTR, max(prior or 0, emitted))
    except Exception:
        # Text-free on purpose: this path skips the masking and include_error_text.
        with contextlib.suppress(Exception):
            logger.log(level or logging.ERROR, event, extra={"error_type": type(exc).__name__}, stacklevel=1 + stacklevel)


def log_unit_end(
    logger: logging.Logger, event: str, exc: BaseException | None, /, *,
    level: int, error_class: str | None = None, include_error_text: bool = True, **fields: object,
) -> None:
    """The end line of one unit of work, at ``level``. Without an exception, the event with ``error_class`` when
    given. With one not yet logged this high, :func:`log_failure` (the stack once, at ERROR). With one already
    logged at ``level`` or higher, the event with its class and type: no text and no second stack."""
    if exc is None:
        log_event(logger, event, level=level, error_class=error_class, **fields)
        return
    cls = error_class or classify_error(exc)
    if already_logged(exc, level):
        log_event(logger, event, level=level, error_class=cls, error_type=type(exc).__name__, **fields)
        return
    log_failure(logger, event, exc, level=level, error_class=cls, include_error_text=include_error_text, **fields)
