"""`Wrapper`: nested-middleware composition for retry/failover/caching.

`Pipeline` (see `pipeline.py`) is a *single-pass* `next()` continuation: once
a middleware calls `next()`, that step of the chain is consumed and can't be
re-invoked — which is exactly right for gate/observe/reducer semantics (a
tool call happens once; a message list is transformed once), but wrong for a
middleware like a retry policy that needs to call "the rest of the chain"
an arbitrary number of times.

`Wrapper` instead composes middleware as literal nested closures — the same
model `HookChain.call_model`/`call_tool` used before this module existed.
`compose(terminal)` builds one callable where the first-registered wrapper is
outermost (its code before calling the inner callable runs first; its code
after the inner callable returns runs last), so a wrapper is free to invoke
the inner callable zero, one, or many times.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

__all__ = ["Wrapper", "WrapMiddleware", "WrapNext"]

T = TypeVar("T")

WrapNext = Callable[[], Awaitable[T]]
WrapMiddleware = Callable[[WrapNext[T]], Awaitable[T]]


class Wrapper(Generic[T]):
    """Ordered stack of wrap-style middleware, composed onion-style."""

    def __init__(self) -> None:
        self._stack: list["WrapMiddleware[T]"] = []

    def use(self, mw: "WrapMiddleware[T]") -> "Wrapper[T]":
        """Register a wrap middleware. First-registered = outermost."""
        self._stack.append(mw)
        return self

    def compose(self, terminal: "WrapNext[T]") -> "WrapNext[T]":
        """Build the fully-nested callable, with `terminal` at the core.

        `terminal` is the actual action being wrapped (e.g. the real LLM
        call) — it takes no arguments and returns the result. Each
        registered middleware receives a `next` callable representing
        everything inside it (either the next middleware, or `terminal`
        itself for the innermost one).
        """
        handler = terminal
        for mw in reversed(self._stack):
            handler = self._bind(mw, handler)
        return handler

    @staticmethod
    def _bind(mw: "WrapMiddleware[T]", next_handler: "WrapNext[T]") -> "WrapNext[T]":
        async def bound() -> T:
            return await mw(next_handler)
        return bound
