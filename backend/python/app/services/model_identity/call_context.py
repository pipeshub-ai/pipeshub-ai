"""The identity a model call is made under, carried in a ``ContextVar``.

Shared model objects (``RetrievalService.llm``, the dense embeddings client) are
process-wide, so identity cannot live on them. It is bound here at the request or
event boundary and read by the HTTP hook as each request is sent.

A ``ContextVar`` follows ``asyncio.create_task``, ``asyncio.to_thread`` and
streaming generators that bind inside their own body; it does not cross
processes. For that, see ``propagation``.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.usage_source import current_llm_usage_source
from app.services.model_identity import metrics
from app.services.model_identity.principal import Principal, service_principal
from app.services.model_identity.settings import (
    DEFAULT_GOVERNANCE,
    IdentityGovernancePolicy,
)
from app.services.model_identity.workload import Workload, WorkloadClass, workload_class

if TYPE_CHECKING:
    from app.services.model_identity.groups import GroupsHandle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCallContext:
    principal: Principal
    workload: Workload
    request_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    record_id: str | None = None
    connector_id: str | None = None
    groups: GroupsHandle | None = field(default=None, compare=False, repr=False)
    governance: IdentityGovernancePolicy = field(default=DEFAULT_GOVERNANCE, compare=False, repr=False)
    pseudonym_key: bytes | None = field(default=None, compare=False, repr=False)
    # Header sets already rendered for this context, keyed by connection and
    # sub-purpose. ``replace()`` does not copy it (init=False), so a narrowed
    # context never reuses headers rendered for the wider one.
    memo: dict[Any, Any] = field(default_factory=dict, init=False, compare=False, repr=False)

    @property
    def workload_class(self) -> WorkloadClass:
        return workload_class(self.workload)


class _Frame:
    """One binding in a chain. A ContextVar holds the innermost open frame."""

    __slots__ = ("ctx", "parent", "closed")

    def __init__(self, ctx: ModelCallContext, parent: "_Frame" | None) -> None:
        self.ctx = ctx
        self.parent = parent
        self.closed = False


_frame: contextvars.ContextVar[_Frame | None] = contextvars.ContextVar(
    "model_call_context", default=None
)


def _open_frame() -> _Frame | None:
    """The innermost frame that is still open.

    A task copies the variable when it starts, so one that outlives the request
    that bound its identity still points at that closed frame.
    """
    frame = _frame.get()
    while frame is not None and frame.closed:
        frame = frame.parent
    return frame


def peek_call_context() -> ModelCallContext | None:
    """The bound context, or None. Does not count a missing binding."""
    frame = _open_frame()
    return frame.ctx if frame is not None else None


def current_call_context() -> ModelCallContext:
    """The bound context, or a service context when nothing is bound.

    A model call with no binding is a boundary that was never wired. It still
    goes out, as PipesHub itself, and is counted so the gap is visible.
    """
    frame = _open_frame()
    if frame is not None:
        return frame.ctx
    metrics.inc(metrics.IDENTITY_MISSING, Workload.SYSTEM.value)
    return ModelCallContext(principal=service_principal(), workload=Workload.SYSTEM)


def current_llm_purpose() -> str:
    """The sub-purpose of the call, from the agent loop's usage-source label."""
    return current_llm_usage_source()


class _Binding:
    """Context manager for both ``with`` and ``async with``.

    Exits need not be strictly nested. An async generator that binds inside its
    body can be closed later, from a different place, after another request
    bound its own identity on top. Resetting a ``ContextVar`` token then would
    put the stale value back and either drop the newer identity or revive a
    finished one. Instead each binding is a frame; closing marks it closed and
    only moves the variable if this frame is still the innermost, and then to the
    nearest frame that is still open.
    """

    def __init__(self, ctx: ModelCallContext) -> None:
        self._ctx = ctx
        self._frame: _Frame | None = None

    def __enter__(self) -> ModelCallContext:
        self._frame = _Frame(self._ctx, _frame.get())
        _frame.set(self._frame)
        return self._ctx

    def __exit__(self, *exc_info: object) -> None:
        frame, self._frame = self._frame, None
        if frame is None:
            return
        frame.closed = True
        if _frame.get() is not frame:
            return
        parent = frame.parent
        while parent is not None and parent.closed:
            parent = parent.parent
        _frame.set(parent)

    async def __aenter__(self) -> ModelCallContext:
        return self.__enter__()

    async def __aexit__(self, *exc_info: object) -> None:
        self.__exit__(*exc_info)


def bind_call_context(ctx: ModelCallContext) -> _Binding:
    """Bind ``ctx`` for the block. Use ``with`` or ``async with``."""
    return _Binding(ctx)


def workload_scope(workload: Workload) -> _Binding:
    """Narrow the bound context to a more specific workload.

    The principal, groups and request metadata stay; only the workload changes,
    for example chat to query embedding. A scope never moves a call between
    classes, so a background job cannot present itself as interactive.
    """
    current = current_call_context()
    if workload_class(workload) is not current.workload_class:
        raise ValueError(
            f"cannot scope a {current.workload_class.value} call to {workload.value}"
        )
    return _Binding(replace(current, workload=workload))


def narrow_workload(workload: Workload) -> AbstractContextManager[Any]:
    """``workload_scope`` for code shared by interactive and background callers.

    A search runs under chat and under unbound system work; the same helper
    marks its embedding step in both without raising in the second.
    """
    if workload_class(workload) is current_call_context().workload_class:
        return workload_scope(workload)
    return nullcontext()
