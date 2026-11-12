"""The process's one logging pipeline (contract: docs/logging.md).

Every logger writes through one handler on the root logger, to stdout, with one
formatter: text (fields as `` | k=v``) or JSON (``LOG_FORMAT=json``), both
redacted. ``LOG_LEVEL`` and ``LOG_LEVELS`` set the levels.
"""

import asyncio
import functools
import json
import logging
import math
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone

import httpx2
from httpx import URL

from app.core import org_context
from app.core.org_context import OrgContext
from app.services.model_identity.call_context import ModelCallContext, peek_call_context
from app.utils.log_redaction import (
    REDACTED,
    redact_secrets_in_text,
    refresh_env_secrets,
)
from app.utils.logging_fields import (
    CONTEXT_FIELDS,
    RESERVED_RECORD_ATTRS,
    current_log_fields,
    log_once,
)
from app.utils.request_context import (
    NO_CONTEXT,
    RequestContext,
    current_display_id,
    get_context,
    sanitize_root_id,
)
from app.utils.sensitive_names import is_credential_name
from app.utils.structured_logging import StructuredLoggingProvider, get_provider
from app.utils.url_redaction import redact_sensitive_query_params, redact_url

# ``%(trace)s`` expands to ``[req:<id> thr:<thread> task:<task>] `` only when a
# context is in flight, so startup/background lines stay clean.
TEXT_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "%(trace)s[%(filename)s:%(lineno)d] - %(message)s"
)


_CALL_CONTEXT_IDS = ("conversation_id", "agent_id", "record_id", "connector_id")
# Runs on every line with an org; the org ids in one process are few.
_sanitized_org = functools.lru_cache(maxsize=256)(sanitize_root_id)


def _identity(rc: RequestContext | None) -> tuple[OrgContext | None, ModelCallContext | None, str | None, str | None]:
    """The bound OrgContext and call context, and the org and user of the work in flight.

    Org and user come from one source, so they never mix two scopes: OrgContext when bound
    (a system one has neither), else the model call's principal, else the request context
    (org only).
    """
    oc = org_context.peek()
    call = peek_call_context()
    if oc is not None:
        return oc, call, oc.org_id, oc.user_id
    if call is not None:
        principal = call.principal
        return oc, call, principal.org_id or None, principal.subject_id if principal.is_user else None
    return oc, call, (rc.org_id if rc else None), None


def _stamp_context(record: logging.LogRecord, rc: RequestContext | None) -> None:
    """Identity as the provider's ``log_identity`` shows it, then the OrgContext kind and
    source (not for users), the call context's ids and the bound fields, in ``record._log_ctx``;
    and the provider's ``hash_org_id`` as ``record.organization_id_hash``.

    Must not raise: makeRecord runs outside any handler's error handling, often in an except
    block. Each hook is guarded on its own, so a failing one drops only what it makes.
    """
    try:
        oc, call, org, user = _identity(rc)
    except Exception as exc:
        _note_context_failure(exc)
        oc, call, org, user = None, None, None, None
    bound = current_log_fields()
    if oc is None and call is None and not org and not bound:
        return
    try:
        provider = get_provider()
        fields: dict[str, object] = {}
        if org:
            try:
                org_hash = provider.hash_org_id(_sanitized_org(org) or org)
                if org_hash:
                    record.organization_id_hash = org_hash  # an attribute, as the EE formatter reads it
            except Exception as exc:
                _note_context_failure(exc)
        ids = {k: v for k, v in (("org_id", org), ("user_id", user)) if v}
        if ids:
            try:
                fields.update(provider.log_identity(ids))  # type: ignore[arg-type]
            except Exception as exc:
                _note_context_failure(exc)
        if oc is not None and oc.kind != "user":
            fields["ctx_kind"] = oc.kind
            fields["ctx_source"] = oc.source
        if call is not None:
            for key in _CALL_CONTEXT_IDS:
                value = getattr(call, key)
                if value:
                    fields[key] = value
            fields["workload"] = call.workload.value
    except Exception as exc:
        _note_context_failure(exc)
        fields = {}
    fields.update(bound)
    if fields:
        record._log_ctx = fields  # underscored: an extra= key of the same name must not collide


def _note_context_failure(exc: Exception) -> None:
    if log_once("logger:context_fields_failed"):
        sys.stderr.write(f"app.utils.logger: log context not added ({type(exc).__name__})\n")


def _make_record_factory(
    base_factory: Callable[..., logging.LogRecord],
) -> Callable[..., logging.LogRecord]:
    # base_factory is captured in the closure, not a module global, so reloading
    # this module cannot rebind it to ourselves and cause infinite recursion.
    def _record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)  # type: ignore[arg-type]

        ctx = get_context()
        _stamp_context(record, ctx)
        if ctx is None:
            record.request_id = ""
            record.task = NO_CONTEXT
            record.trace = ""
            return record

        try:
            task = asyncio.current_task()
            task_name = task.get_name() if task is not None else NO_CONTEXT
        except RuntimeError:
            task_name = NO_CONTEXT

        display_id = current_display_id()
        record.request_id = display_id
        record.task = task_name
        record.trace = f"[req:{display_id} thr:{record.threadName} task:{task_name}] "
        return record

    _record_factory._pipeshub_trace = True  # type: ignore[attr-defined]
    _record_factory._pipeshub_base = base_factory  # type: ignore[attr-defined]
    return _record_factory


# On reload, reuse the original base our installed factory already captured
# rather than wrapping the installed factory (which would recurse).
_existing_factory = logging.getLogRecordFactory()
if getattr(_existing_factory, "_pipeshub_trace", False):
    _base_record_factory = getattr(
        _existing_factory, "_pipeshub_base", logging.LogRecord
    )
else:
    _base_record_factory = _existing_factory

_record_factory = _make_record_factory(_base_record_factory)
if not getattr(_existing_factory, "_pipeshub_trace", False):
    logging.setLogRecordFactory(_record_factory)


class RedactingFormatter(logging.Formatter):
    """Masks credentials in the finished line (see app/utils/log_redaction.py).

    Redacting the formatted string, not ``record.msg``, covers f-strings,
    ``%``-args, ``exc_info`` tracebacks and ``stack_info`` alike.
    """

    # Checked by attribute, not isinstance, so a reload of this module does
    # not wrap the formatters it already installed a second time.
    _pipeshub_redacting = True

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets_in_text(super().format(record))


class _RedactingWrapper(logging.Formatter):
    """Redacts what another formatter produced, for handlers this module did
    not create (uvicorn's), whose own formatting is kept."""

    _pipeshub_redacting = True

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self.inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets_in_text(self.inner.format(record))


def _redact_all_handlers() -> None:
    """Make every handler in the process redact its output, re-reading the
    secret env values first, which may have been loaded since import."""
    refresh_env_secrets()
    loggers = [logging.getLogger()]
    loggers += [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    for lg in loggers:
        for handler in lg.handlers:
            if not getattr(handler.formatter, "_pipeshub_redacting", False):
                handler.setFormatter(_RedactingWrapper(handler.formatter or logging.Formatter()))


_UNREDACTED_TYPES = (int, float, bool, type(None))
_is_credential_key = functools.lru_cache(maxsize=1024)(is_credential_name)


def log_record_fields(record: logging.LogRecord) -> dict[str, object]:
    """The record's fields: the bound context's (identity first, then the call context's
    ids, then bound ids), then its ``extra=`` fields in the order passed. An ``extra=``
    value replaces a bound one in place; it never sets or replaces org_id, user_id,
    ctx_kind or ctx_source. Numbers, booleans and None keep their type, except that a number
    under a credential name that the text pair rule masks becomes ``[REDACTED]``; anything else
    becomes a string, and a string under a credential name (``token``, ``apiKey``) is masked."""
    merged: dict[str, object] = dict(getattr(record, "_log_ctx", None) or {})
    for key, value in record.__dict__.items():
        if key in RESERVED_RECORD_ATTRS or key.startswith("_") or key in CONTEXT_FIELDS:
            continue
        merged[key] = value
    fields: dict[str, object] = {}
    for key, value in merged.items():
        if key in RESERVED_RECORD_ATTRS:
            continue
        if isinstance(value, _UNREDACTED_TYPES) and not _is_masked_number(key, value):
            fields[key] = value
        else:
            fields[key] = REDACTED if _is_credential_key(key) else str(value)
    return fields


def _is_masked_number(key: str, value: object) -> bool:
    """A number the text line masks as a pair (``password=73915528``), so every format masks it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _is_credential_key(key):
        return False
    pair = f"{key}={_text_value(value)}"
    return redact_secrets_in_text(pair) != pair


# Shared with Node (docs/logging.md, "Field values in text"): bare when it
# matches, else a JSON string.
_BARE_VALUE = re.compile(r"[A-Za-z0-9._:@/-]+")


def _text_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text if _BARE_VALUE.fullmatch(text) else json.dumps(text, ensure_ascii=False)


class TextFormatter(RedactingFormatter):
    """The text line, with the record's fields after `` | `` and, when
    ``color`` is set, WARNING in yellow and ERROR in red."""

    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"

    COLORS = {
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def __init__(self, fmt: str = TEXT_FORMAT, *, color: bool = False) -> None:
        super().__init__(fmt)
        self.color = color

    def formatMessage(self, record: logging.LogRecord) -> str:
        # Here rather than in format(): the fields then come before the traceback.
        line = super().formatMessage(record)
        pairs = [f"{k}={_text_value(v)}" for k, v in log_record_fields(record).items() if v is not None]
        return f"{line} | {' '.join(pairs)}" if pairs else line

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        color = self.COLORS.get(record.levelno) if self.color else None
        return f"{color}{formatted}{self.RESET}" if color else formatted


def _json_value(value: object) -> object:
    if isinstance(value, str):
        return redact_secrets_in_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # NaN and Infinity are not JSON
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Every string is redacted before it is
    serialised, so an escaped newline cannot hide a private key."""

    _pipeshub_redacting = True

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname.lower(),  # the same names as Node's JSON (docs/logging.md)
            "logger": record.name,
            "filename": record.filename,
            "line": record.lineno,
            "message": redact_secrets_in_text(record.getMessage()),
            "request_id": getattr(record, "request_id", "") or None,
        }
        for key, value in log_record_fields(record).items():
            out.setdefault(key, _json_value(value))
        stack = self._stack(record)
        if stack:
            out["stack"] = redact_secrets_in_text(stack)
        return json.dumps(out, ensure_ascii=False, default=str)

    def _stack(self, record: logging.LogRecord) -> str:
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        parts = [record.exc_text] if record.exc_text else []
        if record.stack_info:
            parts.append(self.formatStack(record.stack_info))
        return "\n".join(parts)


_HTTPX_RENDERED_STATUS = re.compile(r'HTTP Request: \S+ (\S+) "HTTP/[\d.]+ (\d{3}) ')


class HttpxSuccessFilter(logging.Filter):
    """Drop httpx's per-request INFO log for successful responses.

    httpx logs *every* request at INFO ("HTTP Request: POST <url> \"HTTP/1.1 200
    OK\""), which at indexing volumes buries everything else. Raising the
    logger to WARNING would silence failures too, since httpx logs those at INFO
    as well — so filter on the status code instead and keep 3xx/4xx/5xx.

    The status is read from ``record.args`` rather than the formatted message:
    httpx passes it as an int, so this does not depend on message wording. Only
    when another filter has already rendered the message and cleared ``args``
    (litellm's) is it read from the text.

    Lines that are kept have their request URL redacted: the query string of a
    signed URL, or the userinfo of any URL, is a credential.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, (tuple, list)):
            # Another filter on this logger (litellm's SecretRedactionFilter) may already have rendered the
            # message and cleared args: read the status from the text instead.
            rendered = _HTTPX_RENDERED_STATUS.match(record.msg) if isinstance(record.msg, str) else None
            if rendered is None:
                return True
            if 200 <= int(rendered.group(2)) < 300:
                return False
            start, end = rendered.span(1)  # the kept line's URL, redacted as the args path does
            record.msg = record.msg[:start] + redact_url(rendered.group(1)) + record.msg[end:]
            return True
        # args are (method, url, http_version, status_code, reason_phrase); scan
        # for the status rather than indexing, so a signature change downgrades
        # to "log it" instead of raising.
        for arg in record.args:
            if isinstance(arg, bool):
                continue
            if isinstance(arg, int) and 100 <= arg <= 599:
                if 200 <= arg < 300:
                    return False
                break
        record.args = tuple(
            redact_url(str(arg)) if isinstance(arg, (URL, httpx2.URL)) else arg for arg in record.args
        )
        return True


class HealthCheckFilter(logging.Filter):
    """Suppress successful uvicorn access log entries for /health* endpoints.

    Failed health checks (non-2xx) are still logged so genuine service problems
    remain visible. Only 2xx responses are suppressed since those are routine
    process_monitor polling noise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn access log args: (client_addr, method, path, http_version, status_code)
        if isinstance(record.args, (tuple, list)) and len(record.args) >= 5:
            try:
                path = str(record.args[2])
                status_code = int(record.args[4])
                return not (path.startswith("/health") and 200 <= status_code < 300)
            except (ValueError, TypeError):
                pass
        # Fallback for any non-standard formatting
        msg = record.getMessage()
        return not ("/health" in msg and '" 2' in msg)


class AccessLogRedactionFilter(logging.Filter):
    """Redact credentials from the request path uvicorn writes to its access log.

    Signed download URLs carry their JWT in ``?token=``, so logging the raw path
    would hand a working download link to anyone who can read the logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn access log args: (client_addr, method, path, http_version, status_code)
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            args[2] = redact_sensitive_query_params(str(args[2]))
            record.args = tuple(args)
        return True


# Ensure log directory exists
# Created by create_logger only when the provider keeps file logging, so a
# read-only container filesystem never has to be written.
log_dir = "logs"

# Force UTF-8 for stdout/stderr in Windows
if sys.platform == "win32":
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_ENV_KEYS = ("LOG_LEVEL", "LOG_LEVELS", "LOG_FORMAT", "DATA_STORE")
# The env values last applied ("env"), so an unchanged env costs one tuple compare.
_synced: dict[str, tuple[str, ...]] = {}
# create_logger names outside the ``app`` tree, which LOG_LEVEL also reaches.
_SERVICE_LOGGERS: set[str] = set()


def _level_named(name: str) -> int | None:
    return logging.getLevelNamesMapping().get(name.strip().upper())


@functools.lru_cache(maxsize=16)
def _parse_levels(log_level: str, log_levels: str) -> tuple[int, dict[str, int]]:
    """``LOG_LEVEL`` and the ``LOG_LEVELS`` overrides. A bad value is warned
    about (once, through the cache) and ignored rather than stopping a service."""
    level = _level_named(log_level or "INFO")
    if level is None or level == logging.NOTSET:
        # NOTSET on ``app`` would mean "whatever root says", not "everything".
        logging.getLogger(__name__).warning("LOG_LEVEL %r is not a level name; using INFO", log_level)
        level = logging.INFO
    overrides: dict[str, int] = {}
    for raw in log_levels.split(","):
        entry = raw.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        override = _level_named(value)
        if not sep or not name.strip() or override is None:
            logging.getLogger(__name__).warning("LOG_LEVELS entry %r is not name=LEVEL; skipped", entry)
            continue
        overrides[name.strip()] = override
    return level, overrides


@functools.lru_cache(maxsize=16)
def _parse_format(value: str) -> str:
    fmt = value.strip().lower() or "text"
    if fmt not in ("text", "json"):
        logging.getLogger(__name__).warning("LOG_FORMAT %r is not text or json; using text", value)
        return "text"
    return fmt


def _set_level(logger: logging.Logger, level: int) -> None:
    # setLevel clears every logger's level cache, so skip it when nothing changes.
    if logger.level != level:
        logger.setLevel(level)


def _service_level(level: int) -> int:
    # Below INFO only: at INFO and above the app loggers inherit root, which is
    # also what keeps pytest's caplog.set_level working for them.
    return level if level < logging.INFO else logging.NOTSET


def _apply_levels(level: int, overrides: dict[str, int], data_store: str) -> None:
    """Root never goes below INFO: some libraries log whole requests, prompts
    and bodies at DEBUG. ``LOG_LEVELS`` can still name any logger, root too."""
    _set_level(logging.getLogger(), max(level, logging.INFO))
    for name in ("app", *_SERVICE_LOGGERS):
        _set_level(logging.getLogger(name), _service_level(level))
    # opensearch-py logs every request at INFO; neo4j warns about missing labels.
    defaults = {"opensearch": logging.WARNING}
    if (data_store or "arangodb").lower() == "neo4j":
        defaults["neo4j.notifications"] = logging.ERROR
    for name, override in {**defaults, **overrides}.items():
        _set_level(logging.getLogger(name), override)


def _sync_from_env(*, force: bool = False) -> None:
    """Apply the logging env when it has changed since the last call (a ``.env``
    is loaded after this module is imported). ``force`` re-applies the
    ``LOG_LEVELS`` entries, which uvicorn's logging config resets."""
    env = tuple(os.getenv(key, "") for key in _ENV_KEYS)
    level, overrides = _parse_levels(env[0], env[1])
    previous = _synced.get("env")
    if env != previous:
        format_changed = previous is not None and previous[2] != env[2]
        _synced["env"] = env
        _apply_levels(level, overrides, env[3])
        if format_changed:
            _reset_console_formatter()
    elif force:
        for name, override in overrides.items():
            _set_level(logging.getLogger(name), override)


def _isatty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


def _formatter(*, color: bool) -> logging.Formatter:
    if _parse_format(os.getenv("LOG_FORMAT", "")) == "json":
        return JsonFormatter()
    return TextFormatter(color=color)


def _console_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_pipeshub_console", False)]


def _install_console_handler() -> None:
    """Put the one stdout handler on root. A reload replaces it. A root that
    already has other handlers (pytest's, or a script's own ``basicConfig``)
    is left alone, as ``basicConfig`` would."""
    root = logging.getLogger()
    ours = _console_handlers()
    if not ours and root.handlers:
        return
    for handler in ours:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_formatter(color=_isatty(sys.stdout)))
    handler._pipeshub_console = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def _reset_console_formatter() -> None:
    """After a LOG_FORMAT change: the new default, offered to the provider again
    by the next routing."""
    for handler in _console_handlers():
        handler.setFormatter(_formatter(color=_isatty(getattr(handler, "stream", None))))
        handler._pipeshub_provider_routed = False  # type: ignore[attr-defined]


_install_console_handler()
_sync_from_env()
_redact_all_handlers()

# Suppress /health* endpoint noise from uvicorn access logs (process_monitor polling)
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
logging.getLogger("uvicorn.access").addFilter(AccessLogRedactionFilter())

# httpx logs every outbound request at INFO. Keep the failures, drop the 2xx. httpx2 (the MCP 2.x client) is a
# second copy of httpx with its own logger and URL type.
logging.getLogger("httpx").addFilter(HttpxSuccessFilter())
logging.getLogger("httpx2").addFilter(HttpxSuccessFilter())


def _provider_formatter(
    provider: StructuredLoggingProvider, default: logging.Formatter
) -> logging.Formatter:
    """The provider's console formatter, still redacting: a provider that returns
    a plain formatter must not drop the redaction ``install_log_redaction`` set up."""
    formatter = provider.console_formatter(default)
    if getattr(formatter, "_pipeshub_redacting", False):
        return formatter
    return _RedactingWrapper(formatter)


def _route_root_handlers(provider: StructuredLoggingProvider) -> None:
    """Give the root handlers (third-party libraries log through them) the same
    console formatter as the service loggers. Runs from create_logger, not at
    import, because the provider must not be resolved at import time; each
    handler is marked so a later call does not wrap its formatter again."""
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_pipeshub_provider_routed", False):
            continue
        handler.setFormatter(
            _provider_formatter(provider, handler.formatter or logging.Formatter())
        )
        handler._pipeshub_provider_routed = True  # type: ignore[attr-defined]


def _route_uvicorn_handlers(provider: StructuredLoggingProvider) -> None:
    """Under LOG_FORMAT=json, uvicorn's own handlers write JSON too, so stdout
    holds nothing else. Their streams, filters and levels are kept."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if getattr(handler, "_pipeshub_provider_routed", False):
                continue
            handler.setFormatter(_provider_formatter(provider, JsonFormatter()))
            handler._pipeshub_provider_routed = True  # type: ignore[attr-defined]


def install_log_redaction() -> None:
    """Post-server-start logging sync, called from each service's lifespan.

    uvicorn configures logging after this module is imported: it resets its
    loggers' levels and installs its own handlers. This re-applies the
    ``LOG_LEVELS`` entries, gives uvicorn's handlers the JSON formatter under
    ``LOG_FORMAT=json``, and makes every handler redact, with the secret env
    values re-read.
    """
    _sync_from_env(force=True)
    provider = get_provider()
    # After a LOG_FORMAT change the console handler holds the plain new default until routed again.
    _route_root_handlers(provider)
    if _parse_format(os.getenv("LOG_FORMAT", "")) == "json":
        _route_uvicorn_handlers(provider)
    _redact_all_handlers()


def create_logger(service_name: str) -> logging.Logger:
    """The logger named ``service_name``. Its lines go to the one console
    handler on root, and to ``logs/<service_name>.log`` when the logging
    provider keeps files."""
    logger = logging.getLogger(service_name)
    _sync_from_env()
    if service_name not in _SERVICE_LOGGERS and service_name != "app" and not service_name.startswith("app."):
        _SERVICE_LOGGERS.add(service_name)
        level, overrides = _parse_levels(*_synced["env"][:2])
        _set_level(logger, overrides.get(service_name, _service_level(level)))

    provider = get_provider()
    _route_root_handlers(provider)
    if not logger.handlers and provider.file_logging_enabled():
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(log_dir, f"{service_name}.log"), encoding="utf-8")
        file_handler.setFormatter(_formatter(color=False))
        logger.addHandler(file_handler)
    return logger
