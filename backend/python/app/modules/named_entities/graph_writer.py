"""Create named-entity nodes outside the record transaction, then reconcile edges inside it."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.named_entities.domain.kinds import (
    VALUE_KINDS,
    VECTOR_KINDS,
    EntityKind,
    spec_for,
)
from app.modules.named_entities.domain.models import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    NamedEntity,
)
from app.modules.named_entities.domain.values import (
    DateRangeValue,
    DurationValue,
    MoneyValue,
    PercentValue,
    QuantityValue,
)
from app.modules.named_entities.keys import KEY_SCHEME, value_mention_key
from app.modules.named_entities.resolution import ResolvedEntity
from app.modules.named_entities.write_retry import retry_named_entity_write
from app.modules.transformers.edge_reconciler import EdgeReconciler
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_EDGE = CollectionNames.MENTIONS_ENTITY.value
_NODES = CollectionNames.NAMED_ENTITIES.value
def _value_fields(entity: NamedEntity) -> dict[str, Any]:
    value = entity.value
    if isinstance(value, DateRangeValue) and not value.ambiguous and value.start_ms is not None:
        return {
            "startMs": value.start_ms,
            "endMs": value.end_ms,
            "granularity": value.granularity,
            "timex": value.timex,
        }
    if isinstance(value, MoneyValue):
        return {
            "amount": str(value.amount),
            "amountFloat": value.amount_float,
            "currency": value.currency,
            "numericValue": value.amount_float,
        }
    if isinstance(value, QuantityValue):
        return {
            "numericValue": float(value.value),
            "unit": value.unit,
            "dimension": value.dimension,
            "siValue": value.si_value,
        }
    if isinstance(value, PercentValue):
        return {"numericValue": float(value.value)}
    if isinstance(value, DurationValue):
        return {"durationIso": value.iso, "durationSeconds": value.seconds}
    return {}


def node_document(org_id: str, resolved: ResolvedEntity, now: int) -> dict[str, Any]:
    entity = resolved.entity
    spec = spec_for(entity.kind)
    return {
        "id": resolved.graph_key,
        "orgId": org_id,
        "kind": entity.kind.value,
        "category": spec.category.value if spec else "",
        "tags": list(entity.tags)[:8],
        "name": entity.display_name[:256],
        "normKey": entity.norm_key,
        "aliases": [entity.display_name[:256]],
        "normalizedAliases": [entity.norm_key],
        "schemaVersion": SCHEMA_VERSION,
        "keyScheme": KEY_SCHEME,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }


def edge_payload(org_id: str, entity: NamedEntity, now: int) -> dict[str, Any]:
    mentions = entity.mentions
    names = []
    for mention in mentions:
        if mention.surface not in names:
            names.append(mention.surface[:256])
        if len(names) >= 10:
            break
    if entity.kind is EntityKind.URL:
        # A raw link can carry credentials; the graph keeps only the canonical one.
        names = [entity.display_name[:256]]
    blocks = []
    block_ids = []
    for mention in mentions:
        if mention.block_index not in blocks:
            blocks.append(mention.block_index)
            if mention.block_id:
                block_ids.append(mention.block_id)
        if len(blocks) >= 50:
            break
    extractors = sorted({mention.extractor for mention in mentions})
    evidence = max((mention.evidence_score for mention in mentions), default=0.0)
    return {
        "orgId": org_id,
        "updatedAtTimestamp": now,
        "mentionCount": len(mentions),
        "maxEvidence": evidence,
        "extractors": extractors,
        "extractedNames": names,
        "blockIndexes": blocks,
        # Block ids survive a partial reindex that shifts block indexes.
        "blockIds": block_ids,
        "extractorVersion": EXTRACTOR_VERSION,
    }


def value_document(org_id: str, record_id: str, entity: NamedEntity, now: int) -> dict[str, Any]:
    doc = {
        "id": value_mention_key(record_id, entity.kind.value, entity.norm_key),
        "recordId": record_id,
        "kind": entity.kind.value,
        "normKey": entity.norm_key,
        "name": entity.display_name[:256],
        "schemaVersion": SCHEMA_VERSION,
        "createdAtTimestamp": now,
    }
    doc.update(edge_payload(org_id, entity, now))
    doc.update(_value_fields(entity))
    return doc


class PersistRetrySupersededError(Exception):
    """A retry's write found its retry gone or rescheduled: a newer write of the
    record committed first, so the retry's older extraction must not land."""


class _RetryClaim:
    """A retry's claim on its marker, across the attempts of one write.

    Where the claim auto-commits (Neo4j without explicit transactions), an attempt
    that claimed and then hit a deadlock leaves the marker gone. The next attempt
    must not read that as a newer write, so after a successful claim the later
    attempts only drop the marker, which also covers a rolled-back claim. The
    caller holds the record's lease, so no other write lands between attempts."""

    def __init__(self, due: int | None) -> None:
        self._due = due

    async def take(self, graph: IGraphDBProvider, record_id: str, transaction: str | None) -> None:
        claimed = await graph.clear_named_entity_persist_retry(record_id, self._due, transaction)
        if self._due is not None:
            if not claimed:
                raise PersistRetrySupersededError(record_id)
            self._due = None


class NamedEntityGraphWriter:
    """Writes a record's mention edges, value rows and its persist-retry marker in
    one transaction. A normal write supersedes a pending retry; a retry's write
    commits only while its retry is still the one it read."""

    def __init__(self, graph_provider, graph_data_store, logger_=None) -> None:
        self._graph = graph_provider
        self._store = graph_data_store
        self._logger = logger_ or logger

    async def write(
        self, org_id: str, record_id: str, resolved: list[ResolvedEntity], *, retry_due: int | None = None,
    ) -> list[EntityRecord]:
        now = get_epoch_timestamp_in_ms()
        values = [value_document(org_id, record_id, item.entity, now) for item in resolved if item.entity.kind in VALUE_KINDS]
        resolved = [item for item in resolved if item.entity.kind not in VALUE_KINDS]
        docs = [node_document(org_id, item, now) for item in resolved]
        claim = _RetryClaim(retry_due)

        async def claim_and_link() -> None:
            # Claiming the nodes takes back any orphan mark, so it collides with a
            # sweep deleting one of them; it runs again with the link on that conflict.
            if docs:
                await self._graph.create_named_entities_if_absent(docs)
                for item in resolved:
                    if item.merged_into:
                        await self._graph.add_named_entity_aliases(
                            org_id,
                            item.entity.kind.value,
                            item.graph_key,
                            [item.entity.display_name],
                            [item.entity.norm_key],
                        )
            await self._reconcile(org_id, record_id, resolved, values, now, claim)

        await self._with_retry(claim_and_link)
        return _embeddable(org_id, resolved)

    async def clear_for_record(self, record_id: str, *, retry_due: int | None = None) -> None:
        now = get_epoch_timestamp_in_ms()
        claim = _RetryClaim(retry_due)
        await self._with_retry(lambda: self._reconcile("", record_id, [], [], now, claim))

    async def _with_retry(self, step: Callable[[], Awaitable[None]]) -> None:
        await retry_named_entity_write(self._graph, step)

    async def _reconcile(
        self,
        org_id: str,
        record_id: str,
        resolved: list[ResolvedEntity],
        values: list[dict[str, Any]],
        now: int,
        claim: _RetryClaim,
    ) -> None:
        # The record's entity-extraction fields are not written: a build older than
        # their schema rejects every later update to a record that carries them.
        # One order for every writer, so two records locking the same entities do not
        # lock them in opposite orders.
        ordered = sorted(resolved, key=lambda item: item.graph_key)
        new_tos = {f"{_NODES}/{item.graph_key}": item.entity.display_name for item in ordered}
        properties = {
            f"{_NODES}/{item.graph_key}": edge_payload(org_id, item.entity, now) for item in ordered
        }
        record_from = f"{CollectionNames.RECORDS.value}/{record_id}"
        async with self._store.transaction() as tx:
            # First, so it holds even where a "transaction" is a series of auto-commits
            # (Neo4j without explicit transactions): a normal write drops any pending
            # retry, and a retry's write goes on only while its retry is still the one
            # it read. A claimed write that then fails is queued again by the caller.
            await claim.take(self._graph, record_id, tx.txn)
            await EdgeReconciler(self._logger).reconcile(
                tx,
                record_id=record_id,
                record_from=record_from,
                edge_collection=_EDGE,
                new_tos=new_tos,
                label="named entity",
                edge_properties=properties,
                update_existing=True,
            )
            await self._graph.replace_named_entity_values(record_id, values, tx.txn)


def _embeddable(org_id: str, resolved: list[ResolvedEntity]) -> list[EntityRecord]:
    records = []
    for item in resolved:
        if item.entity.kind not in VECTOR_KINDS:
            continue
        records.append(
            EntityRecord(
                entity_id=item.graph_key,
                entity_type=EntityType.NAMED_ENTITY,
                name=item.entity.display_name,
                org_id=org_id,
                kind=item.entity.kind.value,
                type_category=EntityTypeCategory.PREDEFINED,
            )
        )
    return records
