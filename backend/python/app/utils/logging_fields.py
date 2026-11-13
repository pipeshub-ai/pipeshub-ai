"""Fields on every log line, and one-line events (contract: docs/logging.md, "Context and fields").

Ids are bound once where work enters, with :func:`log_fields` or :func:`bind_log_fields`,
and the record factory in ``app.utils.logger`` puts them on every line logged in that
context. Org and user come from ``app.core.org_context`` and the model call context,
never from here.

Standard library only: ``app.utils.logger`` imports this module.
"""

import contextlib
import contextvars
import logging
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType

CONTEXT_FIELDS: tuple[str, ...] = ("org_id", "user_id", "ctx_kind", "ctx_source")
"""Set by the record factory from the bound contexts; never bound here, and dropped from ``extra=``."""

LOG_FIELD_NAMES: frozenset[str] = frozenset({
    "conversation_id", "agent_id", "run_id", "parent_run_id", "agent_run_id", "attempt", "connector_id", "connector_type",
    "record_id", "kb_id", "tool", "tool_call_id", "mcp_server_id", "toolset_id", "job",
    "refresh_kind", "trigger", "provider", "scheduled_by", "owner_id", "topic", "message_id", "event_type",
    # The user a step acts on, never an email (docs/logging.md, "Personal data"): a PipesHub user id, or an opaque provider id.
    "target_user_id", "source_user_id",
})
"""The ids :func:`bind_log_fields` accepts. A module that needs another id adds it here and to docs/logging.md."""

JSON_STANDARD_KEYS: frozenset[str] = frozenset({
    "timestamp", "level", "logger", "filename", "line", "message", "request_id", "stack",
})
"""The keys ``JsonFormatter`` writes itself; a field with one of these names is dropped in text and JSON alike."""

RESERVED_RECORD_ATTRS: frozenset[str] = (
    frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    | {"message", "asctime", "request_id", "task", "trace", "color_message", "organization_id_hash", "event_name"}
    | JSON_STANDARD_KEYS
)
"""Record attributes that are not fields: the standard ones, the record factory's, uvicorn's
``color_message``, the EE provider's ``organization_id_hash`` and ``event_name``."""

_EMPTY: Mapping[str, object] = MappingProxyType({})
_bound: contextvars.ContextVar[Mapping[str, object]] = contextvars.ContextVar("log_fields", default=_EMPTY)


def bind_log_fields(**fields: object) -> contextvars.Token[Mapping[str, object]]:
    """Add ids to every later line in this context, and in tasks and ``to_thread`` calls started
    from it (not ``run_in_executor`` or ``ThreadPoolExecutor.submit``). ``None`` values are
    skipped. A key outside :data:`LOG_FIELD_NAMES` raises ``ValueError``."""
    unknown = sorted(fields.keys() - LOG_FIELD_NAMES)
    if unknown:
        owned = [k for k in unknown if k in CONTEXT_FIELDS]
        hint = f"; {', '.join(owned)} come(s) from app.core.org_context" if owned else ""
        raise ValueError(f"not a bindable log field: {', '.join(unknown)}{hint}")
    added = {k: v for k, v in fields.items() if v is not None}
    # A new mapping each time: a task that copied the context never sees a later bind.
    return _bound.set(MappingProxyType({**_bound.get(), **added}))


def reset_log_fields(token: contextvars.Token[Mapping[str, object]]) -> None:
    # A token made in another context (an async generator closed elsewhere): nothing of ours to undo here.
    with contextlib.suppress(ValueError):
        _bound.reset(token)


@contextmanager
def log_fields(**fields: object) -> Iterator[None]:
    """:func:`bind_log_fields` for a block. Not across ``yield`` in an async generator: the
    binding would show on the consumer's lines between steps."""
    token = bind_log_fields(**fields)
    try:
        yield
    finally:
        reset_log_fields(token)


def current_log_fields() -> Mapping[str, object]:
    return _bound.get()


def event_fields(event: str, fields: Mapping[str, object]) -> dict[str, object]:
    """``extra=`` for an event line: ``event_name`` (read by the EE formatter), then the fields
    without ``None`` values, context-owned names or record attribute names, so ``makeRecord``
    never raises over a field name."""
    return {"event_name": event} | {
        key: value for key, value in fields.items()
        if value is not None and key not in RESERVED_RECORD_ATTRS and key not in CONTEXT_FIELDS
    }


def log_event(logger: logging.Logger, event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
    """One line whose message is ``event`` (dotted lowercase, e.g. ``sync_run.end``) and whose
    fields are ``fields``. ``[file:line]`` is the caller's."""
    if logger.isEnabledFor(level):
        logger.log(level, event, extra=event_fields(event, fields), stacklevel=2)


_log_once_last: dict[str, float] = {}


def log_once(key: str, interval_s: float = math.inf) -> bool:
    """True the first time ``key`` is seen, then again only once ``interval_s`` seconds have
    passed. Keys are process-wide: prefix them with the module, and keep their number bounded
    (no record or user ids in them)."""
    now = time.monotonic()
    last = _log_once_last.get(key)
    if last is not None and now - last < interval_s:
        return False
    _log_once_last[key] = now
    return True
