"""The `Model` abstraction: the high-level "answer my calls" interface
`Agent` depends on, distinct from `LLMTransport` (the low-level "speak this
provider's wire protocol" implementation). Before this module, `Agent`
depended directly on a concrete `LLMTransport` — collapsing the
provider-protocol concern and the "what does an agent need from an LLM"
concern into one interface, and making it impossible to compose
cross-cutting behavior (retry, caching, fallback-to-a-second-model) as a
`Model` decorator without reaching back down into transport internals.

Split into three single-method Protocols (Interface Segregation) plus the
concrete `Model` ABC that composes all three: most callers only need ONE of
`complete`/`complete_structured`/`stream` (e.g. `Planner`/`Critic`/
`IntentParser` only ever call `complete_structured`) and should depend on
exactly that, not on every capability a full `Model` happens to offer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

__all__ = ["SupportsComplete", "SupportsStructuredComplete", "SupportsStreaming", "Model"]


@runtime_checkable
class SupportsComplete(Protocol):
    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> "ModelResponse": ...


@runtime_checkable
class SupportsStructuredComplete(Protocol):
    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse": ...


@runtime_checkable
class SupportsStreaming(Protocol):
    def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator["StreamEvent"]: ...


class Model(SupportsComplete, SupportsStructuredComplete, SupportsStreaming, ABC):
    """A named, callable LLM — the interface `Agent` actually depends on.
    `TransportModel` (see `models/transport.py`) adapts any `LLMTransport`
    to this shape; nothing stops a caller from implementing `Model` directly
    (e.g. a retrying/caching/fallback decorator over another `Model`)."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Which concrete model this instance calls, e.g. 'claude-sonnet-4-6'."""

    @abstractmethod
    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> "ModelResponse": ...

    @abstractmethod
    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse": ...

    @abstractmethod
    def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator["StreamEvent"]: ...
