"""In-memory ``PublicationDirectory``: the reference adapter for tests and Loom."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from harness_core.knowledge.publications import Publication

if TYPE_CHECKING:
    from harness_core.knowledge.publications import PublicationDraft


class InMemoryPublicationDirectory:
    def __init__(self) -> None:
        self._items: dict[str, Publication] = {}

    async def create(self, draft: PublicationDraft, *, publication_id: str, now_ms: int) -> Publication:
        publication = Publication(
            id=publication_id,
            org_id=draft.org_id,
            container_id=draft.container_id,
            audience=draft.audience,
            mode=draft.mode,
            created_by=draft.created_by,
            created_at_ms=now_ms,
            expires_at_ms=draft.expires_at_ms,
        )
        self._items[publication_id] = publication
        return publication

    async def get(self, org_id: str, publication_id: str) -> Publication | None:
        found = self._items.get(publication_id)
        return found if found is not None and found.org_id == org_id else None

    async def list_for_container(self, org_id: str, container_id: str) -> list[Publication]:
        return sorted(
            (p for p in self._items.values() if p.org_id == org_id and p.container_id == container_id),
            key=lambda p: p.id,
        )

    async def list_for_org(self, org_id: str) -> list[Publication]:
        return sorted((p for p in self._items.values() if p.org_id == org_id), key=lambda p: p.id)

    async def revoke(self, org_id: str, publication_id: str, *, by: str, now_ms: int) -> Publication | None:
        found = await self.get(org_id, publication_id)
        if found is None or found.revoked_at_ms is not None:
            return found
        revoked = replace(found, revoked_at_ms=now_ms, revoked_by=by)
        self._items[publication_id] = revoked
        return revoked

    async def erase_container(self, org_id: str, container_id: str) -> int:
        doomed = [p.id for p in await self.list_for_container(org_id, container_id)]
        for key in doomed:
            del self._items[key]
        return len(doomed)

    async def erase_org(self, org_id: str) -> int:
        doomed = [p.id for p in await self.list_for_org(org_id)]
        for key in doomed:
            del self._items[key]
        return len(doomed)


__all__ = ["InMemoryPublicationDirectory"]
