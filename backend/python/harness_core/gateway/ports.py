"""Ports of the tool gateway: the endpoint a transport serves, where citations go, where keys come from."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.gateway.types import Citation, GatewayClaims, McpResult, McpTool
    from harness_core.journal.types import NewEvent

# Called with the seconds a call has been running, while it is held or executing.
type Progress = Callable[[float], Awaitable[None]]


class ToolEndpoint(Protocol):
    """One run's (or one request's) tools as an MCP server sees them.

    Every call names the verified claims of the token it came with; a binding
    other than the endpoint's is refused before anything else happens.
    """

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]: ...

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult: ...


@runtime_checkable
class NotesConnects(Protocol):
    """An endpoint told each MCP handshake the transport answered for it: an agent that connects while it opens its
    session but lists the tools only when a prompt arrives (Goose) is witnessed by its handshake, not a list."""

    def note_connect(self) -> None: ...


@runtime_checkable
class GrantedTools(Protocol):
    """A run's whole grant, as names, descriptions and schemas: what ``search_tools`` and ``call_tool`` reach and
    what an engine's tool index lists. Not ``tools/list``: reading it is not the engine listing its tools."""

    def granted_tools(self) -> tuple[McpTool, ...]: ...


class CitationSink(Protocol):
    """The run's citation state, as the gateway reads and records it.

    ``cite`` resolves the ``refN`` a result's text uses through the run's own
    mapper; a ref the mapper does not hold (invented, or another run's) is
    dropped. ``record`` returns what the call that just ran added, as events
    journaled with its outcome; ``()`` when nothing was added or it was already
    recorded.
    """

    def cite(self, text: str) -> tuple[Citation, ...]: ...

    def record(self, invocation_id: str) -> tuple[NewEvent, ...]: ...


@runtime_checkable
class NamesUntrusted(Protocol):
    """A ``CitationSink`` facet (KN-08): the sources a call's recorded events name that people outside the
    organization can write (an email, a chat message, a web page), as a person names them (``Slack``); ``()`` when
    every source is the organization's own. The gateway marks such a result untrusted and labels its outcome
    ``integrity: external``, the ``untrusted_content_read`` taint the flow rule reads."""

    def untrusted_sources(self, events: tuple[NewEvent, ...]) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class PublishedKey:
    kid: str
    public_key_pem: str
    # Epoch milliseconds; a retired key verifies only within its retention.
    retired_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class PublishedKeys:
    issuer: str
    audience: str
    keys: tuple[PublishedKey, ...]


class GatewayKeySource(Protocol):
    """The public half of the issuer's keyring. ``refresh`` bypasses any cache
    (a token names a ``kid`` this replica has not seen yet); ``None``: not provisioned."""

    async def keys(self, *, refresh: bool = False) -> PublishedKeys | None: ...


__all__ = [
    "CitationSink",
    "NamesUntrusted",
    "GatewayKeySource",
    "GrantedTools",
    "NotesConnects",
    "Progress",
    "PublishedKey",
    "PublishedKeys",
    "ToolEndpoint",
]
