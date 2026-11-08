"""Ambient org identity for the work in flight (ContextVar).

Set only at trusted entry points: verified JWT / service token (auth), the
consumer envelope wrapper, and jobs (``for_org`` / ``system``). Never from a
request body, query string or header other than ``Authorization``.

Missing context fails closed: :func:`current` raises. Long-lived work (loops,
consumers, lazily created clients) must be started with :func:`spawn_detached`,
because ``asyncio.create_task`` copies the creating request's context and the
task would otherwise serve every later event as that request's org.
"""

import asyncio
import contextvars
import functools
import inspect
import logging
import os
import sys
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from app.telemetry.backend import METRICS_BACKEND
from app.utils.request_context import get_context


class MissingOrgContext(RuntimeError):
    """No org context is bound; this is a bug in the caller, not a user error."""


class SystemContextNotAllowed(RuntimeError):
    """A system (cross-org) context reached code that needs a single org."""


class OrgMismatch(RuntimeError):
    """A nested bind or explicit org id names a different org than the ambient one."""


@dataclass(frozen=True, slots=True)
class OrgContext:
    org_id: str | None  # None only for kind == "system"
    user_id: str | None
    kind: Literal["user", "service", "job", "system"]
    source: str  # "jwt" | "scoped_jwt" | "envelope" | "legacy-payload" | "job:<name>" | "system:<reason>"
    request_id: str

    def __post_init__(self) -> None:
        if (self.kind == "system") != (self.org_id is None):
            raise ValueError("org_id is required unless kind == 'system'")


_ctx: contextvars.ContextVar[OrgContext | None] = contextvars.ContextVar("org_ctx", default=None)


def _request_id() -> str:
    rc = get_context()
    return rc.root_id if rc else "-"


def current_or_system() -> OrgContext:
    """The ambient context, including a system one. Jobs only."""
    ctx = _ctx.get()
    if ctx is None:
        raise MissingOrgContext("no org context is bound")
    return ctx


def current() -> OrgContext:
    ctx = current_or_system()
    if ctx.kind == "system":
        raise SystemContextNotAllowed(f"system context ({ctx.source}) cannot act for one org")
    return ctx


def current_org_id() -> str:
    org_id = current().org_id
    assert org_id is not None
    return org_id


def set_request_context(ctx: OrgContext) -> None:
    """Bind for the rest of the current request task (auth entry points).

    Each request runs in its own task, so no reset is needed; a second bind for
    the same request (global middleware, then a router dependency) must name the
    same org.
    """
    _check_nested(ctx)
    _ctx.set(ctx)


def _check_nested(ctx: OrgContext) -> None:
    outer = _ctx.get()
    if outer and outer.org_id == SYSTEM_ORG_ID and outer.kind == "service" and ctx.kind == "system":
        return  # the org-less bootstrap service token (Node's schedule sweep) may widen into an explicit cross-org scope; no user token, no real org may
    if outer and outer.org_id and ctx.org_id != outer.org_id:
        raise OrgMismatch(f"cannot bind {ctx.org_id!r} inside {outer.org_id!r}")


@contextmanager
def bind(ctx: OrgContext) -> Iterator[OrgContext]:
    _check_nested(ctx)
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def push(ctx: OrgContext | None) -> contextvars.Token:
    """Token-style bind for code that cannot use ``with`` (paired with :func:`pop`). None clears."""
    if ctx is not None:
        _check_nested(ctx)
    return _ctx.set(ctx)


def pop(token: contextvars.Token) -> None:
    _ctx.reset(token)


@contextmanager
def bind_optional(ctx: OrgContext | None) -> Iterator[OrgContext | None]:
    """Consumer wrapper: an unsigned message with no org stays unbound and fails closed."""
    if ctx is None:
        yield None
    else:
        with bind(ctx):
            yield ctx


@contextmanager
def for_org(org_id: str | None, *, job: str) -> Iterator[OrgContext]:
    """Per-org iteration inside a job. Binds from the job's own enumeration, never from input.

    Raises :class:`MissingOrgContext` for an empty org id: callers decide whether to skip the item.
    """
    if not org_id:
        raise MissingOrgContext(f"job {job!r} has no org id")
    ctx = OrgContext(org_id, None, "job", f"job:{job}", _request_id())
    with bind(ctx):
        yield ctx


@contextmanager
def system(reason: str) -> Iterator[OrgContext]:
    """Explicit cross-org scope (enumeration, boot). A read of one org's data must still be inside ``for_org``."""
    with bind(OrgContext(None, None, "system", f"system:{reason}", _request_id())) as ctx:
        yield ctx


def spawn_detached(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Start long-lived work with an empty context (loops, consumers, lazily created clients)."""
    return asyncio.get_running_loop().create_task(coro, name=name, context=contextvars.Context())


def spawn_in_request(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Start request-scoped child work, explicitly keeping the caller's context."""
    return asyncio.get_running_loop().create_task(coro, name=name, context=contextvars.copy_context())


# --- shadow / enforce -------------------------------------------------------
# `shadow` (default) logs and counts an access with no context, or with one for a
# different org, and lets it through. `enforce` fails closed. Flip per deployment
# with ORG_CONTEXT_MODE once the shadow counters stay at zero.

_logger = logging.getLogger(__name__)
VIOLATIONS = METRICS_BACKEND.counter(
    "pipeshub_org_context_violations_total",
    "Org-scoped accesses with no ambient org (missing) or a different one (mismatch)",
    ["kind", "site"],
)
_logged: set[tuple[str, str]] = set()
SYSTEM_ORG_ID = "system"


def validate_mode() -> bool:
    """True in enforce mode. An unknown ``ORG_CONTEXT_MODE`` raises ValueError (never silently shadow)."""
    mode = os.getenv("ORG_CONTEXT_MODE", "shadow").strip().lower() or "shadow"
    if mode not in ("shadow", "enforce"):
        raise ValueError(f"ORG_CONTEXT_MODE must be 'shadow' or 'enforce', got {mode!r}")
    return mode == "enforce"


enforcing = validate_mode


def _caller(depth: int) -> str:
    try:
        f = sys._getframe(depth)
        return f"{f.f_code.co_filename.rsplit('/app/', 1)[-1]}:{f.f_lineno}"
    except ValueError:
        return "?"


def ambient_org(explicit_org_id: str | None, *, site: str, caller: Callable[[], str] | str | None = None) -> str | None:
    """Check an explicit org argument against the ambient context at an org-scoped choke point.

    Returns the explicit org (or the ambient one when none was given). Shadow mode
    never raises; the first violation per (kind, site) is logged with the call site,
    every one is counted.
    """
    ctx = _ctx.get()
    kind = None
    if ctx is None or ctx.org_id is None:
        kind = "missing"
    elif explicit_org_id is not None and explicit_org_id != ctx.org_id:
        # The org-less bootstrap token names the literal org "system": counted apart from real mismatches,
        # still refused in enforce mode, so such a call must bind the org it acts for.
        kind = "system_token" if ctx.org_id == SYSTEM_ORG_ID else "mismatch"
    if kind is None:
        return explicit_org_id or (ctx.org_id if ctx else None)
    VIOLATIONS.inc(kind, site)
    if enforcing():
        raise MissingOrgContext(f"{site}: no org context") if kind == "missing" else OrgMismatch(
            f"{site}: explicit org differs from the ambient org"
        )
    if (kind, site) not in _logged:
        _logged.add((kind, site))
        _logger.warning("org_context_%s site=%s caller=%s", kind, site, caller() if callable(caller) else caller or _caller(2))
    return explicit_org_id


def instrument_org_methods(cls: type) -> None:
    """Wrap the coroutine methods `cls` defines that take an `org_id` argument with :func:`ambient_org`."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("_") or not inspect.iscoroutinefunction(fn) or getattr(fn, "_org_checked", False):
            continue
        params = list(inspect.signature(fn).parameters)
        if "org_id" not in params:
            continue
        idx = params.index("org_id")

        def make(fn: Callable = fn, idx: int = idx, site: str = f"{cls.__name__}.{name}") -> Callable:
            @functools.wraps(fn)
            async def wrapper(*args: object, **kwargs: object) -> object:
                explicit = kwargs["org_id"] if "org_id" in kwargs else (args[idx] if len(args) > idx else None)
                ambient_org(explicit, site=site, caller=functools.partial(_caller, 3))
                return await fn(*args, **kwargs)

            wrapper._org_checked = True
            return wrapper

        setattr(cls, name, make())
