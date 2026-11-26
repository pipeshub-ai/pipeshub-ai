"""Process-wide bindings an embedding application installs at composition time.

The kernel is product-neutral, but a few seams need behaviour only the host
product knows: how to repair a malformed tool call, where learned provider
API modes are recorded, which events are derived from each emitted event,
which tool argument streams as answer text, how spawn batches are scheduled,
which builtin special-route handlers exist, and how the kernel's log lines
are shaped. Each seam left as ``None``
gives the neutral behaviour. Seams tied to one ``AgentRuntime`` are copied
onto the runtime when it is built, so a runtime can still opt out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

if TYPE_CHECKING:
    import asyncio

    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.scope import RunScope
    from harness_core.kernel.core.types import Goal
    from harness_core.kernel.events import AgentEvent
    from harness_core.log_lines import LogLines
    from harness_core.models.output_bound import OutputLimits
    from harness_core.tools.special_route import SpecialRouteHandler


class ApiModeStore(Protocol):
    async def record(self, model_key: str | None, model_name: str, mode: str) -> None: ...


class SpawnBatchScheduler(Protocol):
    async def schedule(
        self,
        agent: object,
        runtime: object,
        calls: list[ToolCall],
        run_scope: RunScope | None,
        *,
        goal: Goal,
        turn_index: int,
        started_at: str | None,
    ) -> dict[str, asyncio.Task[Any]]: ...

    async def cancel_pending(self, tasks: dict[str, asyncio.Task[Any]]) -> None: ...


# Returns the recovery function, or raises when it is unavailable; the
# transport then falls back to a plain JSON parse.
InvalidToolCallRecovery: TypeAlias = "Callable[[], Callable[[Mapping[str, Any]], ToolCall]]"
EventDeriver: TypeAlias = "Callable[[AgentEvent], list[AgentEvent]]"
StreamedAnswerArgument: TypeAlias = Callable[[str], str | None]
SpecialRouteFallback: TypeAlias = "Callable[[str, object | None], SpecialRouteHandler | None]"


@dataclass(frozen=True)
class KernelBindings:
    invalid_tool_call_recovery: InvalidToolCallRecovery | None = None
    api_mode_store: Callable[[], ApiModeStore | None] | None = None
    event_deriver: EventDeriver | None = None
    streamed_answer_argument: StreamedAnswerArgument | None = None
    spawn_scheduler: SpawnBatchScheduler | None = None
    special_route_fallback: SpecialRouteFallback | None = None
    # Read per call, so a model built before the host's settings changed still follows them (P4.17); ``None``
    # gives ``OutputLimits()``.
    output_limits: Callable[[], OutputLimits] | None = None
    # The host's log line shapes (`harness_core.log_lines`); ``None`` writes plain standard library lines.
    log_lines: LogLines | None = None


NEUTRAL = KernelBindings()

_installed: list[KernelBindings] = [NEUTRAL]
_scoped: ContextVar[KernelBindings | None] = ContextVar(
    "harness_core_bindings", default=None
)


def current() -> KernelBindings:
    """The bindings of the enclosing ``scoped`` block, else the installed ones."""
    return _scoped.get() or _installed[0]


def install(bindings: KernelBindings) -> None:
    _installed[0] = bindings


@contextmanager
def scoped(bindings: KernelBindings) -> Iterator[KernelBindings]:
    """Run a block, and every task it starts, on explicit bindings.

    A durable run is built this way so it never inherits whatever a legacy
    composition root installed process-wide, whichever was imported first.
    """
    token = _scoped.set(bindings)
    try:
        yield bindings
    finally:
        _scoped.reset(token)
