"""Credential refresh log lines, one vocabulary for the connector, toolset and MCP refresh services
(contract: docs/logging.md, "Credential refresh").

- The choke point (holds the credential lock, calls the token endpoint) wraps every attempt in
  :func:`refresh_attempt`: ``credential_refresh.start`` (DEBUG) and exactly one ``credential_refresh.end``.
- Entry points (scan item, timer, on-demand) bind :func:`refresh_scope` once; a timer firing also
  takes :func:`fresh_root`.
- Outer handlers log :func:`log_check_failed`, never an ``end``; the failure the choke point already
  logged drops to DEBUG there.
- Strike handlers log only their decision (:func:`log_strike`, :func:`log_deauthenticated`).
- Every other failure line uses :func:`log_refresh_failure`, never plain ``log_failure``: no text the
  token endpoint answered reaches a line or a stack.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import (
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    nullcontext,
)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.core import org_context
from app.services.messaging.error_classifier import extract_status_code
from app.utils.error_class import classify_error, level_for, log_failure
from app.utils.logging_fields import current_log_fields, log_event, log_fields, log_once
from app.utils.request_context import (
    get_context,
    new_system_root,
    reset_context,
    set_context,
)

EVENT_SCHEDULED = "credential_refresh.scheduled"
EVENT_NOT_SCHEDULED = "credential_refresh.not_scheduled"
EVENT_START = "credential_refresh.start"
EVENT_END = "credential_refresh.end"
EVENT_CHECK_FAILED = "credential_refresh.check_failed"
EVENT_STRIKE = "credential_refresh.strike"
EVENT_DEAUTHENTICATED = "credential_refresh.deauthenticated"
EVENT_DEAUTHENTICATE_FAILED = "credential_refresh.deauthenticate_failed"
EVENT_APP_DISABLED_EVENT_FAILED = "credential_refresh.app_disabled_event_failed"
EVENT_SKIPPED = "credential_refresh.skipped"

JOB = "credential_refresh"
_LOGGED_ATTR = "_credential_refresh_logged"
MISCONFIGURED_REPEAT_S = 3600


class Kind(StrEnum):
    CONNECTOR = "connector"
    TOOLSET = "toolset"
    MCP = "mcp"


class Trigger(StrEnum):
    SCHEDULED = "scheduled"
    ON_DEMAND = "on_demand"
    SCAN = "scan"


# The `refresh_reason` tag our exceptions carry → error_class. classify_error's "misconfigured"
# (the provider rejected our OAuth client) is never overridden: it needs a human either way.
ERROR_CLASS_BY_REASON = {
    "invalid_grant": "auth_expired", "config_missing": "misconfigured",
    # MCP (app/agents/mcp/token_refresh.py, oauth_client.py)
    "no_refresh_token": "auth_expired", "invalid_token_record": "auth_expired", "no_token_url": "auth_expired",
    "no_credential": "not_found", "credential_removed": "conflict", "lock_contention": "unavailable",
    "url_blocked": "misconfigured", "host_unresolved": "unavailable",
}


# The keywords the end lines pass themselves: an Attempt.fields key with one of these names would collide with them.
_END_LINE_KEYS = frozenset({
    "outcome", "reason", "duration_ms", "error_class", "error_type", "error_message", "http_status", "oauth_error",
    "level", "include_error_text",
})


class _EndFields(dict[str, object]):
    """Rejects a key the end line writes itself where it is set: in ``refresh_attempt``'s ``except`` the
    collision would raise instead, replacing the attempt's real error and writing no ``end``."""

    def __setitem__(self, key: str, value: object) -> None:
        if key in _END_LINE_KEYS:
            raise ValueError(f"Attempt.fields cannot set {key!r}: the credential_refresh.end line writes it")
        super().__setitem__(key, value)

    def update(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        # shortcut: setdefault and |= skip the check; route them here if a caller starts using them.
        for key, value in dict(*args, **kwargs).items():
            self[key] = value


# The keywords the end lines pass themselves: an Attempt.fields key with one of these names would collide with them.
_END_LINE_KEYS = frozenset({
    "outcome", "reason", "duration_ms", "error_class", "error_type", "error_message", "http_status", "oauth_error",
    "level", "include_error_text",
})


class _EndFields(dict[str, object]):
    """Rejects a key the end line writes itself where it is set: in ``refresh_attempt``'s ``except`` the
    collision would raise instead, replacing the attempt's real error and writing no ``end``."""

    def __setitem__(self, key: str, value: object) -> None:
        if key in _END_LINE_KEYS:
            raise ValueError(f"Attempt.fields cannot set {key!r}: the credential_refresh.end line writes it")
        super().__setitem__(key, value)

    def update(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        # shortcut: setdefault and |= skip the check; route them here if a caller starts using them.
        for key, value in dict(*args, **kwargs).items():
            self[key] = value


@dataclass
class Attempt:
    reason: str | None = None  # why a success did not call the token endpoint ("adopted")
    fields: dict[str, object] = field(default_factory=_EndFields)  # facts about this attempt, on its end line either way


def org_of(doc: Mapping[str, object] | None) -> str | None:
    """The org a stored app or config document belongs to."""
    org = doc.get("orgId") if isinstance(doc, Mapping) else None
    return org if isinstance(org, str) and org else None


@contextmanager
def refresh_scope(kind: Kind, *, trigger: Trigger | None = None, org_id: str | None = None,
                  **ids: object) -> Iterator[None]:
    """Bind which credential and why on every line in the block. ``trigger=None`` keeps the trigger
    of an enclosing refresh scope, else ``on_demand`` (never a sync's own ``trigger``). ``org_id`` is
    bound as a job only when no org is bound: an ambient org (a request, a sync run, a service token) wins."""
    if trigger is None and "refresh_kind" not in current_log_fields():
        trigger = Trigger.ON_DEMAND
    ambient = org_context.peek()
    with log_fields(refresh_kind=kind, trigger=trigger, **ids), _job_org(
        org_id if ambient is None or ambient.org_id is None else None
    ):
        yield


@contextmanager
def fresh_root() -> Iterator[None]:
    """A timer firing is its own unit of work: a new ``sys-credential_refresh-`` root, with
    ``scheduled_by`` naming the root that armed it. An inherited org is kept, as a job, so the user
    of the request that armed it is not."""
    armed_by = get_context()
    inherited = org_context.peek()
    org = inherited.org_id if inherited and inherited.org_id != org_context.SYSTEM_ORG_ID else None
    token = set_context(new_system_root(JOB))
    try:
        with log_fields(scheduled_by=armed_by.root_id if armed_by else None), _job_org(org):
            yield
    finally:
        reset_context(token)


def _job_org(org_id: str | None) -> AbstractContextManager[object]:
    return org_context.for_org(org_id, job=JOB) if org_id else nullcontext()


@asynccontextmanager
async def refresh_attempt(logger: logging.Logger, *, once_key: str) -> AsyncIterator[Attempt]:
    """One attempt: ``start`` at DEBUG, then exactly one ``end`` (INFO on success, the failure's
    level otherwise). The exception is re-raised unchanged. ``once_key`` names the credential
    (``connector:<id>``): a misconfigured one is ERROR once per hour, DEBUG in between."""
    log_event(logger, EVENT_START, level=logging.DEBUG)
    attempt = Attempt()
    start = time.monotonic()
    try:
        yield attempt
    except (Exception, asyncio.CancelledError) as e:
        _log_failed(logger, EVENT_END, e, once_key, duration_ms=_ms_since(start), **attempt.fields)
        raise
    log_event(logger, EVENT_END, outcome="success", reason=attempt.reason, duration_ms=_ms_since(start), **attempt.fields)


def log_check_failed(logger: logging.Logger, exc: BaseException, *, once_key: str) -> None:
    """An outer handler's failure line: DEBUG when the attempt's ``end`` already logged ``exc``,
    the full line for errors raised outside the attempt (loading the token, scheduling)."""
    _log_failed(logger, EVENT_CHECK_FAILED, exc, once_key)


def log_scheduled(logger: logging.Logger, *, next_refresh_at: datetime, delay_s: float, changed: bool) -> None:
    """INFO when the refresh time is new; DEBUG when a scan re-arms the same one."""
    log_event(
        logger, EVENT_SCHEDULED, level=logging.INFO if changed else logging.DEBUG,
        next_refresh_at=next_refresh_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        delay_s=round(delay_s),
    )


def log_skipped(logger: logging.Logger, *, reason: str) -> None:
    """An attempt that did not start, e.g. another refresh of the same credential holds its lock."""
    log_event(logger, EVENT_SKIPPED, level=logging.WARNING, outcome="skipped", reason=reason)


def log_strike(
    logger: logging.Logger, *, strike: int, max_strikes: int, cause: BaseException, counted: bool = True,
) -> None:
    """A rejected refresh token: ``counted=False`` when it was already rotated (a lost race). The cause's
    class and OAuth code (never its text) are on the line: the ``end`` that said it may be a DEBUG repeat."""
    cls, _ = _classified(cause)
    log_event(logger, EVENT_STRIKE, strike=f"{strike}/{max_strikes}", counted=counted,
              error_class=cls, oauth_error=_first(cause, _oauth_error))


def log_deauthenticated(
    logger: logging.Logger, *, reason: str, strikes: int, path: str | None = None, **fields: object,
) -> None:
    """``path`` only where one kind has more than one (MCP: background or route); ``trigger`` already says how."""
    log_event(logger, EVENT_DEAUTHENTICATED, level=logging.WARNING, reason=reason, strikes=strikes, path=path, **fields)


def log_refresh_failure(
    logger: logging.Logger, event: str, exc: BaseException, /, *, level: int | None = None,
    error_class: str | None = None, **fields: object,
) -> None:
    """``log_failure`` for every failure line of a refresh service. When anything in the chain came
    from the token endpoint, its text is left out of the line and the stack: it can quote the refresh
    token, the token URL or the redirect URI in prose the redactor cannot recognise. Its status and
    OAuth code are fields instead.

    A failure this module already logged (the attempt's ``end``, even at a level that was not emitted)
    is DEBUG here unless a higher level is asked for, as in ``log_failure``, so a caller of
    ``refresh_now`` can log what it catches without a second line. Never raises."""
    try:
        handled_at = max(level_for(error_class or classify_error(exc)), level or 0)
        want = level if level is not None else handled_at
        prior = _logged_level(exc)
        emit = want if prior is None or want > prior else logging.DEBUG
        if isinstance(exc, RecursionError):
            # No stack and no text: formatting a RecursionError's frames can recurse again.
            log_event(logger, event, level=emit, **{
                **fields, "outcome": "failure", "error_class": "internal", "error_type": "RecursionError",
                "reason": "recursion",
            })
        else:
            log_failure(
                logger, event, exc, level=emit, error_class=error_class,
                http_status=_first(exc, _status), oauth_error=_first(exc, _oauth_error),
                include_error_text=_first(exc, _from_token_endpoint) is None, **fields,
            )
        # log_failure marks only what it emitted, so a misconfigured repeat logged at a disabled DEBUG would come back at ERROR.
        with contextlib.suppress(AttributeError, TypeError):
            setattr(exc, _LOGGED_ATTR, max(prior or 0, handled_at))
    except Exception:
        # Text-free on purpose, as log_failure's own fallback: this runs inside except blocks.
        with contextlib.suppress(Exception):
            logger.log(level or logging.ERROR, event, extra={"error_type": type(exc).__name__}, stacklevel=2)


def _logged_level(exc: BaseException) -> int | None:
    """The highest level this module handled ``exc`` (or one it was raised ``from``) at; explicit causes only, as in log_failure."""
    levels: list[int] = []
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        value = getattr(link, _LOGGED_ATTR, None)
        if isinstance(value, int) and not isinstance(value, bool):
            levels.append(value)
        link = link.__cause__
    return max(levels) if levels else None


def refresh_error_class(exc: BaseException) -> str | None:
    """The error_class a refresh ``end`` gives ``exc`` when its chain carries a refresh tag; None otherwise."""
    return _classified(exc)[0] if _reason(exc) else None


def _classified(exc: BaseException) -> tuple[str, str | None]:
    """(error_class, reason): the tag refines classify_error, except that our rejected client stays misconfigured."""
    reason = _reason(exc)
    cls = classify_error(exc)
    tagged = ERROR_CLASS_BY_REASON.get(reason)
    if cls == "misconfigured" and tagged not in (None, cls):
        return cls, None  # the provider rejected our client, not the grant the tag names
    return tagged or cls, reason


def _log_failed(logger: logging.Logger, event: str, exc: BaseException, once_key: str, **fields: object) -> None:
    cls, reason = _classified(exc)
    quiet = cls == "misconfigured" and not log_once(f"{JOB}:{once_key}", MISCONFIGURED_REPEAT_S)
    log_refresh_failure(logger, event, exc, level=logging.DEBUG if quiet else None, error_class=cls, reason=reason, **fields)


def _first(exc: BaseException, read: Callable[[BaseException], object]) -> object:
    """The first value ``read`` finds along the chain: the cause, else the context unless suppressed."""
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        value = read(link)
        if value is not None:
            return value
        link = link.__cause__ if link.__cause__ is not None else (None if link.__suppress_context__ else link.__context__)
    return None


def _str_attr(link: BaseException, name: str) -> str | None:
    value = getattr(link, name, None)
    return value if isinstance(value, str) and value else None


def _reason(exc: BaseException) -> str | None:
    return _first(exc, lambda link: _str_attr(link, "refresh_reason"))  # type: ignore[return-value]


def _oauth_error(link: BaseException) -> str | None:
    return _str_attr(link, "error_code")


def _from_token_endpoint(link: BaseException) -> bool | None:
    tagged_rejection = _str_attr(link, "refresh_reason") == "invalid_grant"  # its message embeds the endpoint's answer
    return True if _status(link) is not None or _oauth_error(link) or tagged_rejection else None


def _status(link: BaseException) -> int | None:
    status = extract_status_code(link)  # type: ignore[arg-type]
    # An HTTP status only: our own errors default theirs to 0 (FetchError) when no answer came.
    return status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None


def _ms_since(start: float) -> int:
    return round((time.monotonic() - start) * 1000)
