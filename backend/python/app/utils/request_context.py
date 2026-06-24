"""Process-wide request id context for distributed tracing.

A ``ContextVar`` holds the root request id for the work in flight; the logging
layer reads it at emit time so call sites never pass the id. The per-service
suffix is a process-global set once per service via :func:`set_service_suffix`.
"""

import contextvars
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

HEADER_REQUEST_ID = "x-request-id"
ENVELOPE_REQUEST_ID = "requestId"
NO_CONTEXT = "-"

# `<objectId:24>-<nanoid:21>`
_MAX_ROOT_ID_LEN = 64
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._:-]")


def sanitize_root_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = _UNSAFE_ID_CHARS.sub("", raw)[:_MAX_ROOT_ID_LEN]
    return cleaned or None


@dataclass
class RequestContext:
    root_id: str


_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "request_ctx", default=None
)

_service_suffix: str = ""


def set_service_suffix(suffix: str) -> None:
    global _service_suffix
    _service_suffix = suffix or ""


def get_service_suffix() -> str:
    return _service_suffix


CtxToken = contextvars.Token["Optional[RequestContext]"]


def set_context(root_id: str) -> CtxToken:
    return _ctx.set(RequestContext(root_id=root_id))


def get_context() -> Optional[RequestContext]:
    return _ctx.get()


def reset_context(token: Optional[CtxToken]) -> None:
    if token is None:
        return
    try:
        _ctx.reset(token)
    except (ValueError, LookupError):
        # Token created in a different context (e.g. across a thread boundary).
        _ctx.set(None)


def new_system_root() -> str:
    """Root id for a system/automation-initiated unit of work."""
    return uuid.uuid4().hex


def new_anon_root() -> str:
    """Fallback root id when an inbound request carries none."""
    return f"anon-{uuid.uuid4().hex}"


def current_display_id() -> str:
    """The id stamped into log lines: ``<root><service-suffix>``, or ``-`` if none."""
    ctx = _ctx.get()
    if ctx is None:
        return NO_CONTEXT
    return f"{ctx.root_id}{_service_suffix}"


def inject_request_headers(
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    out: Dict[str, str] = dict(headers or {})
    ctx = _ctx.get()
    if ctx is not None:
        out.setdefault(HEADER_REQUEST_ID, ctx.root_id)
    return out


def inject_envelope(message: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``message`` with the root id stamped in (no mutation)."""
    ctx = _ctx.get()
    if ctx is None:
        return message
    out = dict(message)
    out.setdefault(ENVELOPE_REQUEST_ID, ctx.root_id)
    return out


def context_from_envelope(message: Dict[str, Any]) -> RequestContext:
    root_id = sanitize_root_id(message.get(ENVELOPE_REQUEST_ID))
    return RequestContext(root_id=root_id or new_system_root())
