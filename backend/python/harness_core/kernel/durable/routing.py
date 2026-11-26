"""A model port over a run's pinned routes (P4.7): one port per route, the active one samples.

The kernel decides when to move (``DurableKernel.model_policy``) and journals it; this
port only holds the routes' ports and keeps each request valid for the route it goes
to. Raw provider state is restored only by the active route's own port, so another
provider's state is never sent; a request rendered before a switch is sent
normalized (no thinking, no provider state), and tool-call ids a route would refuse
are rewritten the same way in a call and its result.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from harness_core.kernel.core.messages import (
    AssistantMessage,
    ThinkingPart,
    ToolMessage,
)
from harness_core.models.errors import ModelRouteNotPinned

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.durable.ports import (
        DeltaKind,
        DurableModelPort,
        ModelCall,
        SampledTurn,
    )
    from harness_core.models.policy import ModelPolicy, PolicyRoute

# What every provider takes as a tool-call id (Anthropic and Bedrock: this alphabet;
# OpenAI chat: at most 40 characters).
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class RoutePorts(Protocol):
    """Builds the port of a pinned fallback route, when the run first needs it."""

    async def port(self, route: PolicyRoute) -> DurableModelPort: ...


def portable_id(call_id: str) -> str:
    """``call_id`` when every provider takes it, else a stable id derived from it."""
    if _SAFE_ID.match(call_id):
        return call_id
    return "call_" + hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:24]


def portable_messages(messages: list[Message], *, normalize: bool) -> list[Message]:
    """``messages`` as any route takes them: tool ids portable; with ``normalize``, assistant
    turns reduced to their text and tool calls (what the journal's ``messageRef`` holds)."""
    out: list[Message] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            update: dict[str, object] = {}
            if message.tool_calls and any(portable_id(c.id) != c.id for c in message.tool_calls):
                update["tool_calls"] = [c.model_copy(update={"id": portable_id(c.id)}) for c in message.tool_calls]
            if normalize and (message.provider_state is not None or any(isinstance(p, ThinkingPart) for p in message.content)):
                update["provider_state"] = None
                update["content"] = [p for p in message.content if not isinstance(p, ThinkingPart)]
            out.append(message.model_copy(update=update) if update else message)
        elif isinstance(message, ToolMessage) and message.tool_call_id and portable_id(message.tool_call_id) != message.tool_call_id:
            out.append(message.model_copy(update={"tool_call_id": portable_id(message.tool_call_id)}))
        else:
            out.append(message)
    return out


class RoutedModelPort:
    """``DurableModelPort`` over the routes of one ``ModelPolicy``; starts on the primary."""

    def __init__(self, policy: ModelPolicy, primary: DurableModelPort, ports: RoutePorts | None = None) -> None:
        self.policy = policy
        self._ports: dict[int, DurableModelPort] = {0: primary}
        self._factory = ports
        self._active = policy.primary
        # Set by a switch: the request in hand was rendered for the route before it.
        self._rendered_elsewhere = False

    @property
    def active(self) -> PolicyRoute:
        return self._active

    @property
    def provider(self) -> str:
        return getattr(self._ports[self._active.index], "provider", self._active.provider)

    async def select(self, route: PolicyRoute) -> None:
        if not self.policy.pinned(route):
            raise ModelRouteNotPinned(f"route {route.route_id!r} is not pinned for this run")
        if route == self._active:
            return
        if route.index not in self._ports:
            if self._factory is None:
                raise ModelRouteNotPinned(f"no port for pinned route {route.route_id!r}")
            self._ports[route.index] = await self._factory.port(route)
        self._active = route
        self._rendered_elsewhere = True

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        messages = portable_messages(call.messages, normalize=self._rendered_elsewhere)
        sampled = await self._ports[self._active.index].sample(replace(call, messages=messages), on_delta)
        self._rendered_elsewhere = False
        return sampled

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return self._ports[self._active.index].restore(raw, provider=provider, transport=transport)


__all__ = ["RoutePorts", "RoutedModelPort", "portable_id", "portable_messages"]
