"""Protocol (structural) interfaces for the record-content resolution kernel.

Using `typing.Protocol` means implementations don't need to import or inherit
from this module — the coupling is one-way and easy to mock in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import aiohttp

if TYPE_CHECKING:
    from app.models.entities import Record
    from app.services.artifact_registry.models import Actor

    from .models import ResolvedRecordContent

__all__ = [
    "IRecordAuthorizer",
    "IRecordContentStrategy",
    "IRecordContentResolver",
]


@runtime_checkable
class IRecordAuthorizer(Protocol):
    """Single method interface for the authorization gate.

    Raises `RecordAccessDeniedError` (or a subclass) when the actor may not
    read the record. Implementations should be tiered (cheap first) and must
    not raise for any reason other than a confirmed denial — unexpected
    errors should propagate as their own exception types.
    """

    async def authorize(self, actor: "Actor", record: "Record") -> None:
        """Authorize read access. Raises on denial, returns None on success."""
        ...


@runtime_checkable
class IRecordContentStrategy(Protocol):
    """One content-fetch backend (blob storage or connector service)."""

    def supports(self, record: "Record") -> bool:
        """True when this strategy owns the given record's content."""
        ...

    async def fetch(
        self,
        record: "Record",
        *,
        actor: "Actor",
        version: int | None,
        max_bytes: int,
        session: aiohttp.ClientSession | None,
    ) -> bytes:
        """Fetch and return raw bytes, enforcing `max_bytes`.

        Raises `RecordTooLargeError` when the content exceeds `max_bytes`.
        Raises `RecordContentUnavailableError` on transport / connector errors.
        """
        ...


@runtime_checkable
class IRecordContentResolver(Protocol):
    """Façade that tools call — one method, full auth + dispatch."""

    async def resolve(
        self,
        *,
        actor: "Actor",
        ref: str,
        version: int | None = None,
        conversation_id: str | None = None,
        max_bytes: int,
        session: aiohttp.ClientSession | None = None,
    ) -> "ResolvedRecordContent":
        """Resolve `ref` to bytes under `actor`'s permissions.

        `ref` is a PipesHub record ID, artifact ID, or artifact logical name
        (the last two are resolved via `ArtifactRegistryService` when
        `conversation_id` is provided).

        Raises:
            RecordNotFoundError — ref does not map to any known record.
            RecordAccessDeniedError — actor is not authorized.
            RecordTooLargeError — content exceeds `max_bytes`.
            RecordContentUnavailableError — transport / connector failure.
        """
        ...
