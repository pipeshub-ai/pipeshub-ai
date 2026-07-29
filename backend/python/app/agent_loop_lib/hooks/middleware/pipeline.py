"""Generic, Express-style middleware dispatch engine.

A `Pipeline` is an ordered stack of `(matcher, middleware)` pairs. On
dispatch, it filters to the middleware whose matcher fires for the given
context, then walks them one at a time via an explicit `next()` continuation
— the same model Express uses for `app.use()` handlers. The exact same
engine powers every lifecycle event exposed by `agent_loop.hooks.HookRegistry`
(PreToolUse, PostToolUse, PreAgent, PostAgent, PreTurn, PostTurn, PreModel,
guardrails, ...); only the context type, terminal-decision predicate, and
error policy differ per instantiation.

Composability primitives:
    - `use(mw)`               — global middleware, applies to every context.
    - `use(pattern, mw)`      — glob-, tag-, or predicate-scoped middleware
      (`pattern` can be a path glob string, a `by_tag`/`by_tags` matcher, or
      any custom predicate — see `agent_loop.hooks.middleware.routing`).
    - `tool(path, *mws)`      — one or more middleware for one exact tool path.
    - `mount(prefix, sub)`    — graft a fully-built sub-pipeline under a path
      prefix, enabling plugin/toolkit-style bundles that ship their own
      middleware alongside their tools.

Safety invariant: once `is_terminal(ctx)` is true (e.g. the context's decision
reached DENY), `next()` refuses to advance further down the stack — a
terminal decision can never be "un-decided" by a later middleware, regardless
of registration order.

Because each middleware fully controls whether/when it calls `next()`, this
same engine can express "before/after" (onion) semantics identical to
Express/koa-style wrap middleware: a middleware that does work before
`await next()` and more work after it returns runs its "before" half first
(outermost) and its "after" half last, matching first-registered = outermost.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from app.agent_loop_lib.hooks.middleware.routing import (
    Matcher,
    match_all,
    prefixed,
    to_matcher,
)

__all__ = ["Pipeline", "Next", "Middleware"]

TCtx = TypeVar("TCtx")

Next = Callable[[], Awaitable[None]]
Middleware = Callable[[TCtx, Next], Awaitable[None]]


class Pipeline(Generic[TCtx]):
    """Ordered, path-matched middleware stack with `next()`-chained dispatch."""

    def __init__(
        self,
        *,
        is_terminal: Callable[[TCtx], bool],
        fail_closed: bool = True,
    ) -> None:
        """
        Args:
            is_terminal: predicate over the context; once true, `next()` stops
                advancing (e.g. `lambda ctx: ctx.decision == PreDecision.DENY`).
                Pass `lambda ctx: False` for pure reducer/transform pipelines
                that have no notion of a terminal decision (e.g. PreModel).
            fail_closed: if True, an uncaught exception in a middleware is
                treated as a denial (`ctx.deny(...)`, when the context type
                supports it) — the safe default for pre-execution gates. If
                False, the error is recorded in `ctx.metadata["middleware_errors"]`
                and the chain continues — appropriate for post-execution
                middleware where the tool has already run and a broken
                formatter/logger shouldn't destroy a valid result.
        """
        self._stack: list[tuple[Matcher, "Middleware[TCtx]"]] = []
        self._is_terminal = is_terminal
        self._fail_closed = fail_closed

    def use(
        self,
        matcher_or_mw: "str | Matcher | Middleware[TCtx]",
        mw: "Middleware[TCtx] | None" = None,
    ) -> "Pipeline[TCtx]":
        """Register middleware, either globally or scoped to a path pattern.

        Examples:
            pipeline.use(audit_log)                            # every context
            pipeline.use(by_tag("category", "write"), approval) # tag-scoped
            pipeline.use("/toolsets/jira/**", rate_limit)       # subtree-scoped
            pipeline.use(lambda ctx: "rm" in str(ctx.tool_input), block_rm)
        """
        if mw is None:
            matcher: Matcher = match_all()
            middleware = matcher_or_mw
        else:
            matcher = to_matcher(matcher_or_mw)
            middleware = mw
        self._stack.append((matcher, middleware))
        return self

    def tool(self, path: str, *mws: "Middleware[TCtx]") -> "Pipeline[TCtx]":
        """Register one or more middleware for one exact tool path, in order."""
        matcher = to_matcher(path)
        for mw in mws:
            self._stack.append((matcher, mw))
        return self

    def mount(self, prefix: str, sub: "Pipeline[TCtx]") -> "Pipeline[TCtx]":
        """Compose a sub-pipeline's stack under a path prefix.

        Every matcher registered on `sub` is wrapped so it only fires for
        paths under `prefix`, then appended (in `sub`'s original order) to
        this pipeline's stack — analogous to Express's `app.use(prefix, router)`.
        """
        for matcher, mw in sub._stack:
            self._stack.append((prefixed(prefix, matcher), mw))
        return self

    async def dispatch(self, ctx: TCtx) -> TCtx:
        """Walk the middleware matched against `ctx`, mutating it in place.

        Returns the same context object (now carrying the final decision,
        reason, and any metadata middleware attached) for the caller to
        inspect.
        """
        matched = [mw for matcher, mw in self._stack if matcher(ctx)]
        idx = -1

        async def next_fn() -> None:
            nonlocal idx
            if self._is_terminal(ctx):
                return  # hard stop: a terminal decision was already reached
            idx += 1
            if idx >= len(matched):
                return
            try:
                await matched[idx](ctx, next_fn)
            except Exception as exc:
                self._handle_middleware_error(ctx, exc)
                if not self._is_terminal(ctx):
                    await next_fn()

        await next_fn()
        return ctx

    def _handle_middleware_error(self, ctx: TCtx, exc: Exception) -> None:
        metadata = getattr(ctx, "metadata", None)
        if isinstance(metadata, dict):
            metadata.setdefault("middleware_errors", []).append(str(exc))

        if self._fail_closed:
            deny = getattr(ctx, "deny", None)
            if callable(deny):
                deny(f"middleware error: {exc}")
            # If the context type has no `deny` (e.g. a future event whose
            # decision model differs), the error is still recorded above;
            # callers should supply an `is_terminal` predicate appropriate
            # to that context type to enforce fail-closed behavior there.
