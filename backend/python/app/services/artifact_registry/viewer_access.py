"""Whether a reader who does not own an artifact may see it (SS0).

An artifact shown in a conversation someone else owns is shown only when its
provenance is known and the reader can read every source record in it, through
the same tiered record authorization citations use (C-6). Anything less is
hidden: unknown provenance, a missing artifact, one of another org.

Provenance comes from an `ArtifactProvenanceSource`. `RecordedProvenance`
reads what `record_derivation` stored per version (P3.5): an artifact the
harness publisher, the coding sandbox, `save_artifact`/`update_artifact`, image
generation or a legacy code toolset made (`artifact_provenance`) has known
sources; one saved before provenance was recorded, or whose request saw
something no record names (a connector action, MCP, a web fetch, an
operator's PostgreSQL), stays unknown, so hidden.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.config.constants.arangodb import CollectionNames
from app.models.entities import RecordType
from app.services.record_content.authorizer import SourceStatus, TieredRecordAuthorizer

from .access import AccessPolicy, ArtifactNotFoundError
from .lineage import LineageTracker

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .models import Actor

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactProvenanceSource",
    "ArtifactViewerAccess",
    "RecordedProvenance",
    "artifact_viewer_access",
    "is_artifact_record",
]


class ArtifactProvenanceSource(Protocol):
    async def sources_of(
        self, org_id: str, artifact_ids: Sequence[str]
    ) -> Mapping[str, frozenset[str] | None]:
        """Source record ids per artifact; `None` (or no entry) when its provenance is unknown."""
        ...


class RecordedProvenance:
    """The sources `record_derivation` stored for every version; unknown otherwise."""

    def __init__(self, graph_provider: Any) -> None:  # noqa: ANN401 - IGraphDBProvider
        self._lineage = LineageTracker(graph_provider)

    async def sources_of(
        self, org_id: str, artifact_ids: Sequence[str]
    ) -> Mapping[str, frozenset[str] | None]:
        return await self._lineage.sources_of(org_id, artifact_ids)


class ArtifactViewerAccess:
    def __init__(self, graph_provider: Any, provenance: ArtifactProvenanceSource) -> None:  # noqa: ANN401 - IGraphDBProvider
        self._graph = graph_provider
        self._provenance = provenance
        self._records = TieredRecordAuthorizer(graph_provider)

    async def decide(self, actor: Actor, artifact_ids: Sequence[str]) -> dict[str, SourceStatus]:
        """One status per distinct id. I/O errors propagate so a caller fails closed."""
        wanted = list(dict.fromkeys(aid for aid in artifact_ids if aid))
        if not wanted:
            return {}
        records = await self._graph.get_typed_records_batch(wanted)
        in_org = [aid for aid in wanted if _is_artifact_of(records.get(aid), actor.org_id)]
        provenance: Mapping[str, frozenset[str] | None] = (
            await self._provenance.sources_of(actor.org_id, in_org) if in_org else {}
        )
        known: dict[str, frozenset[str]] = {
            aid: sources for aid in in_org if (sources := provenance.get(aid)) is not None
        }
        readable = await self._records.readable(
            actor, sorted({rid for sources in known.values() for rid in sources})
        )
        statuses: dict[str, SourceStatus] = {}
        for aid in wanted:
            sources = known.get(aid)
            if sources is None:
                statuses[aid] = "unresolved"
            else:
                statuses[aid] = "readable" if sources <= readable else "denied"
        counts = Counter(statuses.values())
        logger.info(
            "artifacts.viewer_access_checked requested=%d readable=%d denied=%d unresolved=%d unknownProvenance=%d",
            len(wanted),
            counts["readable"],
            counts["denied"],
            counts["unresolved"],
            len(in_org) - len(known),
        )
        return statuses

    async def may_stream(self, actor: Actor, artifact_id: str) -> bool:
        """The artifact's owner always; anyone else only when `decide` finds it readable."""
        if await self._is_owner(actor, artifact_id):
            return True
        return (await self.decide(actor, [artifact_id])).get(artifact_id) == "readable"

    async def _is_owner(self, actor: Actor, artifact_id: str) -> bool:
        try:
            user_key = await AccessPolicy(self._graph).resolve_user_key(actor)
        except ArtifactNotFoundError:
            return False
        edge = await self._graph.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=artifact_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        return bool(edge) and edge.get("role") == "OWNER"


def artifact_viewer_access(graph_provider: Any) -> ArtifactViewerAccess:  # noqa: ANN401 - IGraphDBProvider
    """The production wiring: the one place the provenance source is chosen."""
    return ArtifactViewerAccess(graph_provider, RecordedProvenance(graph_provider))


def is_artifact_record(record: object) -> bool:
    record_type = _field(record, "record_type", "recordType")
    return record_type in (RecordType.ARTIFACT, RecordType.ARTIFACT.value)


def _is_artifact_of(record: object, org_id: str) -> bool:
    return record is not None and is_artifact_record(record) and _field(record, "org_id", "orgId") == org_id


def _field(record: object, attribute: str, key: str) -> object:
    """A typed record's attribute, or the raw document's key."""
    value: object = getattr(record, attribute, None)
    if value is None and isinstance(record, dict):
        value = cast("dict[str, object]", record).get(key)
    return value
