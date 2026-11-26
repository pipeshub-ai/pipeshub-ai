"""`TieredRecordAuthorizer` — cheapest-first, no bypass.

Three tiers, each a real check:

0. The record is live. A record in the trash keeps its permission edges so it
   can be restored, and tier 2 would otherwise grant access from the edge alone.
   Raises `RecordNotFoundError`: to the reader the item is gone.
1. Org scope: `record.org_id == actor.org_id`. O(1), always runs.
2. The batch access check (`IGraphDBProvider.check_access`, main #4255):
   connector and collection records by the permission model, and artifacts
   and chat attachments (records outside every App) by a grant on the record
   itself. Every id of a call is decided in one check (SYNC4 S8).

A direct user-to-record edge is not accepted on its own: it would skip the
STRICT/RESTRICTED rule and the connector gate, and these bytes leave the
system. No bypass path exists; service-account callers
follow the same gate (see plan §Authorization — Open decision).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from app.harness.contracts.generated.pipeshub.citation_source import (
    PipeshubLiveSource,
    PipeshubRecordSource,
)
from app.models.citation_source import indexed_copy_connectors
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.graph_db.common.record_visibility import is_live_record

from .models import (
    GRAPH_CALLER_BUGS,
    RecordAccessDeniedError,
    RecordNotFoundError,
    graph_did_not_answer,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.harness.knowledge.service import KnowledgePublications
    from app.services.artifact_registry.models import Actor

SourceStatus = Literal["readable", "denied", "unresolved"]

logger = logging.getLogger(__name__)

__all__ = ["SourceStatus", "TieredRecordAuthorizer"]


class TieredRecordAuthorizer:
    """The authorization gate for the record-content kernel.

    Constructed once per resolver instance; thread-safe and stateless
    beyond the injected graph_provider.
    """

    def __init__(self, graph_provider: Any) -> None:
        self._graph = graph_provider

    async def authorize(self, actor: Any, record: Any) -> None:
        """Authorize `actor` to read `record`. Raises `RecordAccessDeniedError`
        on denial; returns `None` on success.

        Raises `RecordNotFoundError` for a record in the trash, and
        `RecordContentUnavailableError` when the graph could not answer the
        check. Our own bugs (`GRAPH_CALLER_BUGS`) propagate as they are.
        """
        if not is_live_record(record):
            raise RecordNotFoundError(f"Record {_record_id(record)} was deleted")
        try:
            permitted = await self.permits(actor, record)
        except GRAPH_CALLER_BUGS:
            raise
        except Exception as exc:
            raise graph_did_not_answer(f"Could not check access to record {_record_id(record)}") from exc
        if permitted:
            return
        record_id = _record_id(record)
        logger.warning(
            "access_denied actor=%s record=%s",
            actor.user_id,
            record_id,
        )
        raise RecordAccessDeniedError(
            f"Actor {actor.user_id} is not authorized to read record {record_id}"
        )

    async def permits(self, actor: Actor, record: object) -> bool:
        """The tiers without the denial log or exception; a record in the trash is never permitted."""
        # Tier 0 — the record is live
        if not is_live_record(record):
            return False
        # Tier 1 — org scope (O(1), always runs)
        if not _same_org(actor, record):
            return False
        record_id = _record_id(record)
        return record_id is not None and record_id in await self._granted(actor, [record_id])

    async def readable(self, actor: Actor, record_ids: list[str]) -> set[str]:
        """The ids among `record_ids` that `actor` may read now: one batched
        record read, the user resolved once, then every record through the
        same tiers. A record that does not exist is not readable. I/O errors
        propagate, so a caller that cannot get an answer fails closed."""
        readable, _, _ = await self._check(actor, record_ids)
        return readable

    async def decide(
        self,
        actor: Actor,
        sources: Sequence[PipeshubRecordSource | PipeshubLiveSource],
        *,
        publications: KnowledgePublications | None = None,
    ) -> list[SourceStatus]:
        """One status per source (P1b C-18). A record is decided by its own
        ACL: ``denied`` when the actor may not read it, whether or not it
        exists. A live item by the ACL of its indexed copies in the actor's
        org: readable through any of them, ``unresolved`` when there is none.
        The copies are found in one query, then every record id is checked in
        one batch as ``readable`` does. I/O errors propagate."""
        live = [s for s in sources if isinstance(s, PipeshubLiveSource)]
        copies = await self._indexed_copies(actor, live)
        record_ids = [s.record_id for s in sources if isinstance(s, PipeshubRecordSource)]
        readable, in_org, records = await self._check(
            actor, record_ids + [rid for ids in copies.values() for rid in ids]
        )

        def status(ids: list[str]) -> SourceStatus:
            if any(rid in readable for rid in ids):
                return "readable"
            return "denied" if any(rid in in_org for rid in ids) else "unresolved"

        def record_status(source: PipeshubRecordSource) -> SourceStatus:
            # One answer for an unreadable record and an absent one: a viewer names any id it likes (QA27-F3).
            if status([source.record_id]) != "readable":
                return "denied"
            # A reference naming content (an attachment's virtualRecordId) is read by it:
            # it must be this record's own, or a readable id would open another's file.
            if source.virtual_record_id is not None:
                bound = _virtual_record_id(records.get(source.record_id))
                return "readable" if bound == source.virtual_record_id else "denied"
            return "readable"

        statuses = [
            record_status(source)
            if isinstance(source, PipeshubRecordSource)
            else status(copies.get(_live_key(source), []))
            for source in sources
        ]
        if publications is not None:
            statuses = await _through_publications(actor, sources, statuses, publications)
        return statuses

    async def _indexed_copies(
        self, actor: Actor, live: list[PipeshubLiveSource]
    ) -> dict[tuple[str, str], list[str]]:
        """Record ids of each live item's indexed copies, keyed by (connector kind, external id)."""
        if not live:
            return {}
        kinds = {source.connector: indexed_copy_connectors(source.connector) for source in live}
        rows = await self._graph.get_records_by_external_ids(
            actor.org_id,
            [source.external_record_id for source in live],
            sorted({kind for family in kinds.values() for kind in family}),
        )
        copies: dict[tuple[str, str], list[str]] = {}
        for source in live:
            key = _live_key(source)
            copies[key] = [
                str(row["id"])
                for row in rows
                if row.get("externalRecordId") == source.external_record_id
                and row.get("connectorName") in kinds[source.connector]
            ]
        return copies

    async def _check(
        self, actor: Actor, record_ids: list[str]
    ) -> tuple[set[str], set[str], dict[str, object]]:
        """(readable, in the actor's org, the records read) among `record_ids`; see `readable`."""
        wanted = list(dict.fromkeys(rid for rid in record_ids if rid))
        if not wanted:
            return set(), set(), {}
        records = await self._graph.get_typed_records_batch(wanted)
        in_org = {rid for rid in wanted if rid in records and _same_org(actor, records[rid])}
        live = [rid for rid in wanted if rid in in_org and is_live_record(records[rid])]
        return await self._granted(actor, live), in_org, dict(records)

    async def _granted(self, actor: Actor, record_ids: list[str]) -> set[str]:
        """Tier 2 — the ids the permission model admits, all in one ``check_access``."""
        if not record_ids:
            return set()
        user_key = await self._user_key(actor)
        if not user_key:
            return set()
        check = await self._graph.check_access(user_key, actor.org_id, node_ids=record_ids)
        return set(check.node_ids) & set(record_ids)

    async def _user_key(self, actor: Actor) -> str | None:
        """The actor's graph key; `None` when it has no user node (a service account), which reads nothing."""
        try:
            from app.services.artifact_registry.access import AccessPolicy
            return await AccessPolicy(self._graph).resolve_user_key(actor)
        except ArtifactNotFoundError:
            return None


def _live_key(source: PipeshubLiveSource) -> tuple[str, str]:
    return source.connector, source.external_record_id


async def _through_publications(
    actor: Actor,
    sources: Sequence[PipeshubRecordSource | PipeshubLiveSource],
    statuses: list[SourceStatus],
    publications: KnowledgePublications,
) -> list[SourceStatus]:
    """A denied source that names its publication (P2.13) is readable while that publication still discloses the
    record to the actor. Nothing else changes: a source without one, or naming content, keeps its own decision."""
    asked = [
        (index, source)
        for index, (source, decided) in enumerate(zip(sources, statuses, strict=True))
        if decided == "denied"
        and isinstance(source, PipeshubRecordSource)
        and source.publication_id
        and source.virtual_record_id is None
    ]
    if not asked or not await publications.enabled():
        return statuses
    principal = await publications.principal(actor.user_id, actor.org_id)
    if principal is None:
        return statuses
    pairs = [(source.record_id, str(source.publication_id)) for _, source in asked]
    through = await publications.readable_through(principal, pairs)
    out = list(statuses)
    for (index, _), pair in zip(asked, pairs, strict=True):
        if pair in through:
            out[index] = "readable"
    return out


def _same_org(actor: Actor, record: object) -> bool:
    record_org = getattr(record, "org_id", None) or (
        record.get("orgId") if isinstance(record, dict) else None
    )
    return not record_org or record_org == actor.org_id


def _virtual_record_id(record: object) -> str | None:
    return getattr(record, "virtual_record_id", None) or (
        record.get("virtualRecordId") if isinstance(record, dict) else None
    )


def _record_id(record: object) -> str | None:
    return getattr(record, "id", None) or (
        record.get("_key") if isinstance(record, dict) else None
    )
