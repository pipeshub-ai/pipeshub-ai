"""Where publications are kept (C-2: ``PublicationDirectoryContract``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.knowledge.publications import Publication, PublicationDraft


class PublicationDirectory(Protocol):
    """Publications of an org's containers. Every read is scoped by org: an id of another org reads as absent.

    Revocation keeps the publication (audit) with ``revoked_at_ms``; erasure removes it. Ids and time are the
    caller's (injected), so a host's adapter stays deterministic under test.
    """

    async def create(self, draft: PublicationDraft, *, publication_id: str, now_ms: int) -> Publication: ...

    async def get(self, org_id: str, publication_id: str) -> Publication | None: ...

    async def list_for_container(self, org_id: str, container_id: str) -> list[Publication]: ...

    async def list_for_org(self, org_id: str) -> list[Publication]: ...

    async def revoke(self, org_id: str, publication_id: str, *, by: str, now_ms: int) -> Publication | None:
        """The publication as revoked; a second revoke keeps the first time and author. ``None``: not found."""
        ...

    async def erase_container(self, org_id: str, container_id: str) -> int:
        """Removes the container's publications; returns how many."""
        ...

    async def erase_org(self, org_id: str) -> int: ...


__all__ = ["PublicationDirectory"]
