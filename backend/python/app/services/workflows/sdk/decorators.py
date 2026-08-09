"""@workflow and @step decorators.

The actual execution behavior (journaling, replay) lives in Ctx and
CodeWorkflowRunner. These decorators are purely metadata markers.
"""
from __future__ import annotations

import functools
from enum import Enum
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class SideEffect(str, Enum):
    """Declared side-effect class for a @step.

    READ  — safe to re-execute on replay without user impact.
    WRITE — mutates external state; must not re-execute; raises
            ReplayDivergence if replayed without a journal hit.
    """
    READ = "read"
    WRITE = "write"
    NONE = "none"


class _WorkflowMeta:
    """Metadata attached to the decorated function by @workflow."""
    __slots__ = ("name", "inputs", "outputs", "on_event", "triggers")

    def __init__(
        self,
        name: str,
        inputs: Any = None,
        outputs: Any = None,
        on_event: Any = None,
        triggers: Any = None,
    ) -> None:
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.on_event = on_event
        self.triggers = list(triggers or [])


class _StepMeta:
    """Metadata attached to the decorated function by @step."""
    __slots__ = ("retries", "timeout_s", "side_effect")

    def __init__(self, retries: int = 0, timeout_s: float | None = None, side_effect: SideEffect = SideEffect.NONE) -> None:
        self.retries = retries
        self.timeout_s = timeout_s
        self.side_effect = side_effect


def workflow(
    _fn: F | None = None,
    *,
    name: str | None = None,
    inputs: Any = None,
    outputs: Any = None,
    on_event: Any = None,
    triggers: "list[Any] | None" = None,
) -> "F | Callable[[F], F]":
    """Mark an async function as a workflow entry point.

    Supports both ``@workflow`` (bare) and ``@workflow(name="...")`` (factory)
    forms.

    `triggers` is read statically from the source at generation time (see
    `ir.extractor.extract_trigger_specs`) and reconciled into real scheduler
    rows; it has no runtime effect here.

    Usage:
        @workflow
        async def triage(ctx: Ctx) -> None:
            ...

        @workflow(
            name="triage_support",
            inputs=EmailIn,
            outputs=TriageOut,
            triggers=[cron("0 9 * * 1-5", tz="America/New_York")],
        )
        async def triage(ctx: Ctx, inp: EmailIn) -> TriageOut:
            ...
    """
    def decorator(fn: F) -> F:
        effective_name = name or fn.__name__
        fn.__workflow_meta__ = _WorkflowMeta(  # type: ignore[attr-defined]
            name=effective_name, inputs=inputs, outputs=outputs, on_event=on_event,
            triggers=triggers,
        )
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)
        wrapper.__workflow_meta__ = fn.__workflow_meta__  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if _fn is not None:
        return decorator(_fn)
    return decorator


def step(
    _fn: F | None = None,
    *,
    retries: int = 0,
    timeout_s: float | None = None,
    side_effect: SideEffect = SideEffect.NONE,
) -> "F | Callable[[F], F]":
    """Mark an async function as a workflow step.

    Supports both ``@step`` (bare) and ``@step(retries=3)`` (factory) forms.

    Steps are automatically journaled via the `Ctx` passed as the first
    positional argument.  On replay they short-circuit to their journaled
    result.  `SideEffect.WRITE` steps raise `ReplayDivergence` if no journal
    entry exists during replay mode — the write already happened in a prior
    attempt and must not be repeated.

    Convention: the decorated function's first positional argument must be a
    `Ctx` instance (i.e. `async def my_step(ctx: Ctx, ...)`).  Functions that
    do not receive a `Ctx` are called directly without journaling.

    Usage:
        @step
        async def fetch_data(ctx: Ctx, query: str) -> list[dict]:
            return await ctx.tool("jira/search_issues", jql=query)

        @step(retries=3, timeout_s=60, side_effect=SideEffect.READ)
        async def fetch_data(ctx: Ctx, query: str) -> list[dict]:
            return await ctx.tool("jira/search_issues", jql=query)

        @step(side_effect=SideEffect.WRITE)
        async def send_email(ctx: Ctx, recipient: str) -> str:
            return await ctx.tool("gmail__send_email", to=recipient)
    """
    is_write = side_effect == SideEffect.WRITE
    step_meta = _StepMeta(retries=retries, timeout_s=timeout_s, side_effect=side_effect)

    def decorator(fn: F) -> F:
        fn.__step_meta__ = step_meta  # type: ignore[attr-defined]
        qualname = fn.__qualname__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from app.services.workflows.sdk.context import Ctx as _Ctx
            ctx: _Ctx | None = None
            if args and isinstance(args[0], _Ctx):
                ctx = args[0]

            if ctx is None:
                return await fn(*args, **kwargs)

            step_key = ctx._next_step_key(qualname)

            async def _execute() -> Any:
                return await fn(*args, **kwargs)

            return await ctx._journal_or_replay(
                step_key,
                "step",
                _execute,
                side_effect_write=is_write,
            )

        wrapper.__step_meta__ = step_meta  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if _fn is not None:
        return decorator(_fn)
    return decorator
