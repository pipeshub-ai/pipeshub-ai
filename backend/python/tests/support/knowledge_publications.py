"""The real ``KnowledgePublications`` over an in-memory directory, with its graph reads (principal, container,
container records) answered from a table and counted. For tests of what reads a publication decides."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.harness.knowledge.service import Container, KnowledgePublications
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import Audience, Principal, PublicationDraft

if TYPE_CHECKING:
    from harness_core.knowledge.publications import PublicationMode

ORG = "org-1"
NOW = 1_800_000_000_000


@dataclass
class Reads:
    principals: int = 0
    listings: dict[str, int] = field(default_factory=dict[str, int])


def publications_over(
    graph: object, containers: dict[str, set[str]], reads: Reads | None = None, *, now_ms: int = NOW
) -> KnowledgePublications:
    """``containers``: qualified container id → the record ids it holds (virtual id ``v-<id>``)."""
    counted = reads or Reads()
    service = KnowledgePublications(graph, None, directory=InMemoryPublicationDirectory(), now_ms=lambda: now_ms)  # type: ignore[arg-type]

    async def enabled() -> bool:
        return True

    async def principal(user_id: str | None, org_id: str) -> Principal | None:
        counted.principals += 1
        return Principal(org_id=org_id, user_id=user_id) if user_id else None

    async def container(org_id: str, qualified: str) -> Container | None:
        collection, key = qualified.split("/", 1)
        return Container(qualified, collection, key, key) if qualified in containers else None

    async def container_records(org_id: str, found: Container, cap: int) -> dict[str, str]:
        counted.listings[found.id] = counted.listings.get(found.id, 0) + 1
        return {f"v-{rid}": rid for rid in containers[found.id]}

    service.enabled = enabled  # type: ignore[method-assign]
    service.principal = principal  # type: ignore[method-assign]
    service.container = container  # type: ignore[method-assign]
    service.container_records = container_records  # type: ignore[method-assign]
    return service


async def publish(
    service: KnowledgePublications,
    container: str,
    users: set[str],
    mode: PublicationMode,
    *,
    pid: str,
    expires: int | None = None,
    now_ms: int = NOW,
) -> None:
    draft = PublicationDraft(ORG, container, Audience(user_ids=frozenset(users)), mode, "owner", expires)
    _ = await service.directory.create(draft, publication_id=pid, now_ms=now_ms - 10)


__all__ = ["NOW", "ORG", "Reads", "publications_over", "publish"]
