"""What one model call's caller asked of the provider beyond the messages (WP-62).

The model gateway serves many runs through one cached transport per configured model, so what an engine asked for
on a single request cannot be set on the transport. The gateway opens a scope around the call; a transport that can
put an option on its provider's wire reads it while it builds the request, and says which options it honours
(``LLMTransport.honoured_call_options``) so the gateway names the rest as dropped. Outside a scope (the loop, the
query service) nothing is set and every request is unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator

PARALLEL_TOOL_CALLS: Final = "parallel_tool_calls"
PROMPT_CACHE_KEY: Final = "prompt_cache_key"


@dataclass(frozen=True, slots=True)
class CallOptions:
    """``parallel_tool_calls``: whether the model may call several tools in one turn (``None``: the provider's
    default). ``cache_key``: the ``prompt_cache_key`` to send, already scoped by the caller; a transport sends it only
    where its own cache policy sends one (OpenAI's and Azure's hosts), in place of its default key."""

    parallel_tool_calls: bool | None = None
    cache_key: str | None = None

    @property
    def asked(self) -> frozenset[str]:
        names: set[str] = set()
        if self.parallel_tool_calls is not None:
            names.add(PARALLEL_TOOL_CALLS)
        if self.cache_key:
            names.add(PROMPT_CACHE_KEY)
        return frozenset(names)


NO_OPTIONS: Final = CallOptions()
_OPTIONS: ContextVar[CallOptions] = ContextVar("harness_model_call_options", default=NO_OPTIONS)


@contextmanager
def call_options(options: CallOptions) -> Generator[None]:
    token = _OPTIONS.set(options)
    try:
        yield
    finally:
        _OPTIONS.reset(token)


def current_call_options() -> CallOptions:
    return _OPTIONS.get()


__all__ = ["NO_OPTIONS", "PARALLEL_TOOL_CALLS", "PROMPT_CACHE_KEY", "CallOptions", "call_options", "current_call_options"]
