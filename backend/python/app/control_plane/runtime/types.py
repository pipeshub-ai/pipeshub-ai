"""Values the runtime ports exchange that never leave the Python process (what crosses comes from contracts)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Unsubscribe = Callable[[], None]
Closer = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunContext:
    """The run an item is loaded for: credential leases are bound to it (FR-31)."""

    org_id: str
    run_id: str
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedTool:
    """A pinned tool made usable: its id and digest from the pin, ``handle`` the kind's own tool object."""

    tool_id: str
    descriptor_digest: str
    handle: object = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class ItemLoad:
    """What loading pinned items of one kind produced; ``close`` releases what the load opened (R2 X-1)."""

    tools: tuple[LoadedTool, ...] = ()
    mounts: tuple[str, ...] = ()
    prompt_parts: tuple[str, ...] = ()
    closers: tuple[Closer, ...] = field(default=(), repr=False)

    async def close(self) -> None:
        for closer in reversed(self.closers):
            await closer()


@dataclass(frozen=True, slots=True)
class LiveDescriptor:
    """A tool's descriptor digest as the live server reports it at call time."""

    tool_id: str
    digest: str


__all__ = [
    "Closer",
    "ItemLoad",
    "LiveDescriptor",
    "LoadedTool",
    "RunContext",
    "Unsubscribe",
]
