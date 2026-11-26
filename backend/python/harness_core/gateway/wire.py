"""What a model-gateway wire module provides: one engine-facing API format (E3, E3b).

``ModelGateway`` admits every call the same way (token, grant, resolver, budget,
spend) and hands the wire only what differs: how a request becomes kernel messages,
and how a ``ModelResponse`` or its ``StreamEvent``s go back on that wire (E3c: and
how a refusal or failure reads on it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.access.types import ModelApi
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import ModelResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolSchema

type Json = dict[str, object]


class WireRequest(Protocol):
    """A parsed engine request, in the kernel's types every ``LLMTransport`` speaks."""

    @property
    def model(self) -> str: ...
    @property
    def system(self) -> str | None: ...
    @property
    def messages(self) -> tuple[Message, ...]: ...
    @property
    def tools(self) -> tuple[ToolSchema, ...]: ...
    @property
    def stream(self) -> bool: ...
    @property
    def reasoning_effort(self) -> str | None: ...
    @property
    def output_limit(self) -> int | None:
        """The engine's own output limit, whatever the wire calls it; the gateway sends at most it (P4.17)."""
        ...
    @property
    def dropped(self) -> tuple[str, ...]: ...
    @property
    def reasoning_items(self) -> int:
        """Reasoning items in the history; those with encrypted content ride on their assistant turn's
        ``provider_state`` (WP-62), the rest a provider cannot read back."""
        ...
    @property
    def encrypted_reasoning(self) -> bool:
        """The caller asked for the provider's encrypted reasoning, answered where the transport carries it."""
        ...


@dataclass(frozen=True, slots=True)
class WireCall:
    """What a wire needs to name the answer: the neutral model id, the gateway's id for it, its time, and the tag
    the provider's reasoning state is sealed with (``reasoning_state``; empty: none goes out)."""

    model_id: str
    response_id: str
    created: int
    state_tag: str = ""


class StreamEncoder(Protocol):
    def start(self) -> list[Json]: ...
    def on_event(self, event: StreamEvent) -> list[Json]: ...
    def failed(self, code: str, message: str) -> list[Json]:
        """The last events of a stream the provider broke after it started."""
        ...


class GatewayWire[R: WireRequest](Protocol):
    @property
    def api(self) -> ModelApi: ...
    @property
    def id_prefix(self) -> str: ...
    @property
    def terminal(self) -> bytes:
        """Written after the last event of a stream."""
        ...
    @property
    def keepalive(self) -> bytes | None:
        """Written while a stream has had nothing to send for a heartbeat (ADR-GW-01): a frame the wire's clients
        skip. ``None``: the wire has none its clients are known to skip."""
        ...
    @property
    def provider_state(self) -> str | None:
        """The provider-state wire (``models.provider_state``) this API hands to an engine and takes back sealed
        (Responses reasoning, Anthropic thinking); ``None``: it carries none."""
        ...

    def parse(self, body: object, headers: Mapping[str, str] | None = None) -> R:
        """``headers``: the request's, lower-cased; an API that versions itself in headers (Messages) reads them."""
        ...
    def body(self, request: R, response: ModelResponse, call: WireCall) -> Json: ...
    def encoder(self, request: R, call: WireCall) -> StreamEncoder: ...
    def frame(self, event: Json) -> bytes: ...
    def error(
        self, status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
    ) -> tuple[Json, dict[str, str]]:
        """The wire's error body for a refusal (``provider``: a provider failure), and any headers its clients act on."""
        ...


__all__ = ["GatewayWire", "Json", "StreamEncoder", "WireCall", "WireRequest"]
