"""`@tool` decorator: a lightweight alternative to subclassing `Tool`.

Useful for simple, single-function tools where a full class definition would
be pure boilerplate::

    @tool(
        path="/toolsets/web/echo",
        short_description="Echo input back",
        description="Returns the given text unchanged.",
        parameters=[ToolParameter("text", ParameterType.STRING, "Text to echo")],
    )
    async def echo(text: str) -> ToolOutput:
        return ToolOutput(success=True, data=text)

    registry.register_tool(echo)               # echo is now a Tool instance
    result = await echo(text="hi")              # still directly callable
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput, ToolParameter

__all__ = ["tool", "FunctionTool"]

ToolFunction = Callable[..., Awaitable[ToolOutput]]


class FunctionTool(Tool):
    """A `Tool` implementation that wraps a single async function.

    The original function is preserved on `self.func` so it can be invoked
    directly (bypassing validation/middleware) in tests or scripts, e.g.
    ``await my_tool.func(query="x")``. Calling the tool instance itself
    (``await my_tool(query="x")``) goes through `Tool.__call__`, which
    validates/normalizes arguments first, exactly like any other `Tool`.
    """

    def __init__(
        self,
        func: ToolFunction,
        *,
        path: str,
        short_description: str,
        description: str,
        parameters: list[ToolParameter] | None = None,
        tags: list[Tag] | None = None,
    ) -> None:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"@tool can only wrap async functions; {func.__name__!r} is not a "
                "coroutine function (define it with 'async def')."
            )
        self.func = func
        self._path = path
        self._name = path.rsplit("/", 1)[-1]
        self._short_description = short_description
        self._description = description
        self._parameters = list(parameters or [])
        self._tags = list(tags or [])

        self.__name__ = getattr(func, "__name__", self._name)
        self.__doc__ = func.__doc__

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._short_description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return self._path

    @property
    def tags(self) -> list[Tag]:
        return list(self._tags)

    @property
    def parameters(self) -> list[ToolParameter]:
        return list(self._parameters)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return await self.func(**kwargs)

    def __repr__(self) -> str:
        return f"FunctionTool(path={self._path!r})"


def tool(
    *,
    path: str,
    short_description: str,
    description: str,
    parameters: list[ToolParameter] | None = None,
    tags: list[Tag] | None = None,
) -> Callable[[ToolFunction], FunctionTool]:
    """Decorator factory that turns an async function into a `FunctionTool`."""

    def decorator(func: ToolFunction) -> FunctionTool:
        return FunctionTool(
            func,
            path=path,
            short_description=short_description,
            description=description,
            parameters=parameters,
            tags=tags,
        )

    return decorator
