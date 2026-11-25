"""``PublicationDirectory`` over ``IGraphDBProvider``'s generic node and edge methods, so Neo4j and ArangoDB alike.

A publication is a ``knowledgePublications`` node with a ``publishedVia`` edge from its container (``apps/<kb>`` or
``recordGroups/<id>``, the qualified id the core keeps opaque). Neo4j refuses a map as a node property, so the
audience is stored as flat lists. Reads go through ``get_nodes_by_filters`` without ``return_fields``: ArangoDB
returns ``_key`` and Neo4j ``id`` then, and the id is read as either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.config.constants.arangodb import CollectionNames
from app.harness.knowledge.graph import Doc, doc, docs, graph_of, text
from harness_core.knowledge.publications import (
    MODES,
    Audience,
    Publication,
)

if TYPE_CHECKING:
    from harness_core.knowledge.publications import PublicationDraft

PUBLICATIONS = CollectionNames.KNOWLEDGE_PUBLICATIONS.value
PUBLISHED_VIA = CollectionNames.PUBLISHED_VIA.value
CONTAINER_COLLECTIONS = frozenset({CollectionNames.APPS.value, CollectionNames.RECORD_GROUPS.value})


class PublicationStoreError(RuntimeError):
    """The graph did not take a write; nothing is reported as published."""


def split_container(container_id: str) -> tuple[str, str]:
    """``apps/<kb>`` → (``apps``, ``<kb>``); raises ``ValueError`` for anything that is not a container."""
    collection, _, key = container_id.partition("/")
    if collection not in CONTAINER_COLLECTIONS or not key or "/" in key:
        raise ValueError(f"not a container id: {container_id!r}")
    return collection, key


def _strings(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in cast("list[object]", value) if isinstance(item, str) and item)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def from_document(document: Doc) -> Publication | None:
    """A stored publication, or ``None`` when the document is not one (it then grants nothing)."""
    pid = text(document, "id", "_key")
    mode = document.get("mode")
    created_at = _int(document.get("createdAtTimestamp"))
    org, container, created_by = (text(document, k) for k in ("orgId", "containerId", "createdBy"))
    if pid is None or created_at is None or org is None or container is None or created_by is None:
        return None
    if mode not in MODES:
        return None
    return Publication(
        id=pid,
        org_id=org,
        container_id=container,
        audience=Audience(
            user_ids=_strings(document.get("audienceUserIds")),
            group_ids=_strings(document.get("audienceTeamIds")),
            org=document.get("audienceOrg") is True,
        ),
        mode=mode,
        created_by=created_by,
        created_at_ms=created_at,
        expires_at_ms=_int(document.get("expiresAt")),
        revoked_at_ms=_int(document.get("revokedAt")),
        revoked_by=text(document, "revokedBy"),
    )


def _document(publication: Publication) -> Doc:
    document: Doc = {
        "id": publication.id,
        "orgId": publication.org_id,
        "containerId": publication.container_id,
        "audienceUserIds": sorted(publication.audience.user_ids),
        "audienceTeamIds": sorted(publication.audience.group_ids),
        "audienceOrg": publication.audience.org,
        "mode": publication.mode,
        "createdBy": publication.created_by,
        "createdAtTimestamp": publication.created_at_ms,
    }
    if publication.expires_at_ms is not None:
        document["expiresAt"] = publication.expires_at_ms
    return document


class GraphPublicationDirectory:
    def __init__(self, graph: object) -> None:
        self._graph = graph_of(graph)

    async def create(self, draft: PublicationDraft, *, publication_id: str, now_ms: int) -> Publication:
        collection, key = split_container(draft.container_id)
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
        if await self._graph.batch_upsert_nodes([_document(publication)], PUBLICATIONS) is False:
            raise PublicationStoreError("the publication was not written")
        edge: Doc = {
            "from_id": key,
            "from_collection": collection,
            "to_id": publication_id,
            "to_collection": PUBLICATIONS,
            "createdAtTimestamp": now_ms,
        }
        if not await self._graph.batch_create_edges([edge], PUBLISHED_VIA):
            # A node without its edge would never be found from the container; take it back.
            _ = await self._graph.delete_nodes([publication_id], PUBLICATIONS)
            raise PublicationStoreError("the container edge was not written")
        return publication

    async def get(self, org_id: str, publication_id: str) -> Publication | None:
        found = doc(await self._graph.get_document(publication_id, PUBLICATIONS, raise_on_error=True))
        publication = from_document(found) if found is not None else None
        return publication if publication is not None and publication.org_id == org_id else None

    async def _list(self, filters: Doc) -> list[Publication]:
        found = (from_document(d) for d in docs(await self._graph.get_nodes_by_filters(PUBLICATIONS, filters)))
        return sorted((p for p in found if p is not None), key=lambda p: p.id)

    async def list_for_container(self, org_id: str, container_id: str) -> list[Publication]:
        return await self._list({"orgId": org_id, "containerId": container_id})

    async def list_for_org(self, org_id: str) -> list[Publication]:
        return await self._list({"orgId": org_id})

    async def revoke(self, org_id: str, publication_id: str, *, by: str, now_ms: int) -> Publication | None:
        found = await self.get(org_id, publication_id)
        if found is None or found.revoked_at_ms is not None:
            return found
        if not await self._graph.update_node(publication_id, PUBLICATIONS, {"revokedAt": now_ms, "revokedBy": by}):
            raise PublicationStoreError("the revocation was not written")
        return await self.get(org_id, publication_id)

    async def _erase(self, publications: list[Publication]) -> int:
        for publication in publications:
            _ = await self._graph.delete_edges_to(publication.id, PUBLICATIONS, PUBLISHED_VIA)
        if publications:
            _ = await self._graph.delete_nodes([p.id for p in publications], PUBLICATIONS)
        return len(publications)

    async def erase_container(self, org_id: str, container_id: str) -> int:
        return await self._erase(await self.list_for_container(org_id, container_id))

    async def erase_org(self, org_id: str) -> int:
        return await self._erase(await self.list_for_org(org_id))


__all__ = ["CONTAINER_COLLECTIONS", "GraphPublicationDirectory", "PublicationStoreError", "from_document", "split_container"]
