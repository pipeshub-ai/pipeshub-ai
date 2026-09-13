import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.config.constants.arangodb import (
    CollectionNames,
    ProgressStatus,
)
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    TransactionStore,
)
from app.models.blocks import SemanticMetadata
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_TAXONOMY_KEY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "pipeshub:taxonomy-node")

_SUBCATEGORY_COLLECTIONS = (
    CollectionNames.SUBCATEGORIES1.value,
    CollectionNames.SUBCATEGORIES2.value,
    CollectionNames.SUBCATEGORIES3.value,
)


def normalize_taxonomy_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def taxonomy_node_key(org_id: str, collection: str, name: str) -> str:
    """Key of the taxonomy node for *name* in *org_id*.

    Derived rather than looked up: concurrent writers of the same term agree on
    one node without a read, and the same term in another org is another node.
    """
    return str(
        uuid.uuid5(
            _TAXONOMY_KEY_NAMESPACE,
            f"{org_id}|{collection}|{normalize_taxonomy_name(name)}",
        )
    )


@dataclass(frozen=True)
class TaxonomyNode:
    collection: str
    key: str
    name: str

    @property
    def graph_id(self) -> str:
        return f"{self.collection}/{self.key}"


class GraphDBTransformer(Transformer):
    def __init__(self, graph_provider: IGraphDBProvider, logger: logging.Logger) -> None:
        super().__init__()
        self.logger = logger
        self.graph_provider = graph_provider
        self.graph_data_store = GraphDataStore(logger, graph_provider)

    async def apply(self, ctx: TransformContext) -> None:
        """Persist semantic metadata to the graph and update extractionStatus.

        ``indexingStatus`` is intentionally **not** touched here — that is set
        by :meth:`SinkOrchestrator.index` (via ``_update_indexing_status``)
        *before* this method is called.  Keeping the two statuses independent
        allows the index phase to complete and make the document searchable
        before enrichment runs.
        """
        record = ctx.record
        metadata = record.semantic_metadata
        virtual_record_id = record.virtual_record_id
        record_id = record.id

        if metadata is None:
            try:
                async with self.graph_data_store.transaction() as tx_store:
                    timestamp = get_epoch_timestamp_in_ms()
                    # Only update extractionStatus — indexingStatus was already
                    # set to COMPLETED by SinkOrchestrator.index().
                    status_doc: dict[str, Any] = {
                        "id": record_id,
                        "extractionStatus": (
                            ProgressStatus.SKIPPED.value if ctx.extraction_skip_reason else ProgressStatus.FAILED.value
                        ),
                        "lastExtractionTimestamp": timestamp,
                        "isDirty": False,
                        "virtualRecordId": virtual_record_id,
                    }
                    if ctx.extraction_skip_reason:
                        status_doc["reason"] = ctx.extraction_skip_reason
                    self.logger.debug(
                        "🎯 Upserting extraction status for document"
                    )
                    # batch_update_nodes returns bool only (not updated docs): True if all
                    # nodes matched, False if any record was missing (see provider warning log).
                    success = await tx_store.batch_update_nodes(
                        [status_doc], CollectionNames.RECORDS.value
                    )
                    if not success:
                        self.logger.warning(
                            "⚠️ Failed to update indexing status for record %s - record may not exist",
                            record_id,
                        )
                        return
            except Exception as e:
                self.logger.error(f"❌ Error saving metadata to graph database: {str(e)}")
                raise
        else:
            is_vlm_ocr_processed = getattr(record, 'is_vlm_ocr_processed', False)
            await self.save_metadata_to_db(record_id, metadata, virtual_record_id, is_vlm_ocr_processed)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_key(doc: dict[str, Any]) -> str | None:
        """Extract the node key from a document returned by the graph provider."""
        key = doc.get("_key") or doc.get("id")
        return str(key) if key else None

    @staticmethod
    def _taxonomy_nodes(
        org_id: str, collection: str, names: list[str]
    ) -> list[TaxonomyNode]:
        nodes: dict[str, TaxonomyNode] = {}
        for name in names:
            if name and name.strip():
                key = taxonomy_node_key(org_id, collection, name)
                nodes.setdefault(key, TaxonomyNode(collection, key, name.strip()))
        return list(nodes.values())

    @classmethod
    def _category_chain(cls, org_id: str, metadata: SemanticMetadata) -> list[TaxonomyNode]:
        """Category, then each subcategory level, stopping at the first missing level."""
        primary = next(iter(metadata.categories or []), None)
        if not primary or not primary.strip():
            return []
        chain = cls._taxonomy_nodes(org_id, CollectionNames.CATEGORIES.value, [primary])
        levels = (
            metadata.sub_category_level_1,
            metadata.sub_category_level_2,
            metadata.sub_category_level_3,
        )
        for collection, name in zip(_SUBCATEGORY_COLLECTIONS, levels):
            if not name or not name.strip():
                break
            chain.extend(cls._taxonomy_nodes(org_id, collection, [name]))
        return chain

    async def _ensure_taxonomy_nodes(self, org_id: str, nodes: list[TaxonomyNode]) -> None:
        """Create the nodes that don't exist yet, outside the record transaction.

        Taxonomy nodes are shared by every record that mentions the term; writing
        them inside each record's transaction made hot terms a write-write
        conflict. Created here they are idempotent, and an orphan left by a
        failed transaction is simply reused by the next record.
        """
        by_collection: dict[str, list[dict[str, str]]] = {}
        for node in nodes:
            by_collection.setdefault(node.collection, []).append({
                "id": node.key,
                "name": node.name,
                "normalizedName": normalize_taxonomy_name(node.name),
                "orgId": org_id,
            })
        for collection, docs in by_collection.items():
            await self.graph_provider.ensure_nodes(docs, collection)

    async def _reconcile_edges(
        self,
        tx_store,
        record_id: str,
        record_from: str,
        edge_collection: str,
        new_tos: dict[str, str],
        label: str,
    ) -> None:
        """
        Generic reconciliation: create new edges, delete stale ones.

        Args:
            tx_store: Transaction store (handles transaction passing automatically)
            record_id: record key (for logging)
            record_from: full from-id, e.g. "records/<key>"
            edge_collection: the edge collection name
            new_tos: mapping of full-to-id -> human-readable name
            label: label for log messages (e.g. "department")
        """
        # 1. Fetch existing edges for this record
        existing_edges = await tx_store.get_edges_from_node_with_target_name(
            record_from, edge_collection
        )
        self.logger.debug(f"Existing edges with adjacent node names : {existing_edges}")
        existing_by_to: dict[str, dict[str, Any]] = {e["_to"]: e for e in existing_edges}

        # 2. Create edges that are new (in new but not in existing)
        edges_to_create: list[dict[str, Any]] = []
        sorted_new_targets = sorted(
            new_tos.items(),
            key=lambda item: item[1],
        )
        for to_full, name in sorted_new_targets:
            if to_full not in existing_by_to:
                to_collection, to_id = to_full.split("/", 1)
                edges_to_create.append({
                    "from_id": record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": to_id,
                    "to_collection": to_collection,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                })
                self.logger.debug(f"🔗 Created {label} edge: {record_id} -> {name}")
        if edges_to_create:
            await tx_store.batch_create_edges(
                edges_to_create, edge_collection
            )

        # 3. Delete edges that are stale (in existing but not in new)
        stale_tos = [
            to_full for to_full in existing_by_to
            if to_full not in new_tos
        ]
        if stale_tos:
            stale_tos = sorted(
                stale_tos,
                key=lambda to_full: existing_by_to[to_full]["name"],
            )
            from_collection, from_id = record_from.split("/", 1)
            stale_edges = []
            for to_full in stale_tos:
                to_collection, to_id = to_full.split("/", 1)
                stale_edges.append(
                    {
                        "from_id": from_id,
                        "from_collection": from_collection,
                        "to_id": to_id,
                        "to_collection": to_collection,
                    }
                )

            deleted_count = await tx_store.batch_delete_edges(stale_edges, edge_collection)
            for to_full in stale_tos:
                self.logger.info(f"🗑️ Deleted stale {label} edge: {record_id} -> {to_full}")
            self.logger.info(
                f"🧹 Deleted {deleted_count} stale {label} edges for record {record_id}"
            )

    async def _link_category_hierarchy(
        self, tx_store: TransactionStore, chain: list[TaxonomyNode]
    ) -> None:
        """Child → parent edges between category levels, written only when missing.

        Skipping existing edges avoids the UPSERT UPDATE branch that takes a write
        lock on a shared row (ArangoDB errorNum 1200 under concurrent indexing).
        """
        for child, parent in zip(chain[1:], chain):
            existing_edge: dict[str, Any] | None = await tx_store.get_edge(
                child.key, child.collection,
                parent.key, parent.collection,
                CollectionNames.INTER_CATEGORY_RELATIONS.value,
            )
            if existing_edge is None:
                await tx_store.batch_create_edges(
                    [{
                        "from_id": child.key,
                        "from_collection": child.collection,
                        "to_id": parent.key,
                        "to_collection": parent.collection,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }],
                    CollectionNames.INTER_CATEGORY_RELATIONS.value,
                )

    # ------------------------------------------------------------------
    # main persistence logic
    # ------------------------------------------------------------------

    async def save_metadata_to_db(
        self, record_id: str, metadata: SemanticMetadata, virtual_record_id: str, is_vlm_ocr_processed: bool = False
    ) -> None:
        """Reconcile the record's taxonomy edges with *metadata* and mark extraction COMPLETED."""
        await self._save_metadata(
            record_id, metadata, virtual_record_id=virtual_record_id,
            is_vlm_ocr_processed=is_vlm_ocr_processed, write_status=True,
        )

    async def write_taxonomy(self, record_id: str, metadata: SemanticMetadata) -> None:
        """Reconcile the record's taxonomy edges only; the caller owns ``extractionStatus``."""
        await self._save_metadata(
            record_id, metadata, virtual_record_id=None, is_vlm_ocr_processed=False, write_status=False
        )

    async def _save_metadata(
        self,
        record_id: str,
        metadata: SemanticMetadata,
        *,
        virtual_record_id: str | None,
        is_vlm_ocr_processed: bool,
        write_status: bool,
    ) -> None:
        """Reconcile the record's taxonomy edges with *metadata*, and optionally mark extraction COMPLETED.

        A field that is ``None`` (or an empty category) is unknown — typically a
        fallback summary — and leaves that field's existing edges untouched; an
        empty list is a known "none" and removes them.
        """
        self.logger.debug("🚀 Saving metadata to graph database")
        record: dict[str, Any] | None = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record is None:
            self.logger.error(f"❌ Record {record_id} not found in database")
            raise Exception(f"Record {record_id} not found in database")
        org_id = record.get("orgId") or ""
        if not org_id:
            self.logger.warning("⚠️ Record %s has no orgId; taxonomy nodes will not be org-scoped", record_id)

        category_chain = self._category_chain(org_id, metadata)
        languages = (
            self._taxonomy_nodes(org_id, CollectionNames.LANGUAGES.value, metadata.languages)
            if metadata.languages is not None else None
        )
        topics = (
            self._taxonomy_nodes(org_id, CollectionNames.TOPICS.value, metadata.topics)
            if metadata.topics is not None else None
        )
        await self._ensure_taxonomy_nodes(
            org_id, [*category_chain, *(languages or []), *(topics or [])]
        )

        async with self.graph_data_store.transaction() as tx_store:
            try:
                record_from = f"{CollectionNames.RECORDS.value}/{record_id}"

                if metadata.departments is not None:
                    new_dept_tos: dict[str, str] = {}
                    for department in metadata.departments:
                        try:
                            results = await tx_store.get_nodes_by_filters(
                                CollectionNames.DEPARTMENTS.value,
                                {"departmentName": department},
                            )
                            dept_key = self._node_key(results[0]) if results else None
                            if dept_key:
                                dept_to = f"{CollectionNames.DEPARTMENTS.value}/{dept_key}"
                                new_dept_tos[dept_to] = department
                            else:
                                self.logger.warning(f"⚠️ No department found for: {department}")
                        except Exception as e:
                            self.logger.error(f"❌ Error resolving department {department}: {str(e)}")

                    await self._reconcile_edges(
                        tx_store, record_id, record_from,
                        CollectionNames.BELONGS_TO_DEPARTMENT.value,
                        new_dept_tos, "department",
                    )

                if category_chain:
                    await self._link_category_hierarchy(tx_store, category_chain)
                    await self._reconcile_edges(
                        tx_store, record_id, record_from,
                        CollectionNames.BELONGS_TO_CATEGORY.value,
                        {node.graph_id: node.name for node in category_chain}, "category",
                    )

                if languages is not None:
                    await self._reconcile_edges(
                        tx_store, record_id, record_from,
                        CollectionNames.BELONGS_TO_LANGUAGE.value,
                        {node.graph_id: node.name for node in languages}, "language",
                    )

                if topics is not None:
                    await self._reconcile_edges(
                        tx_store, record_id, record_from,
                        CollectionNames.BELONGS_TO_TOPIC.value,
                        {node.graph_id: node.name for node in topics}, "topic",
                    )

                self.logger.debug(
                    "🚀 Metadata saved successfully for document"
                )

                if write_status:
                    # Update only extractionStatus — indexingStatus is managed
                    # independently by SinkOrchestrator.index().
                    timestamp = get_epoch_timestamp_in_ms()
                    status_doc = {
                        "id": record_id,
                        "extractionStatus": "COMPLETED",
                        "lastExtractionTimestamp": timestamp,
                        "isDirty": False,
                        "virtualRecordId": virtual_record_id,
                    }

                    if is_vlm_ocr_processed:
                        status_doc["isVLMOcrProcessed"] = True

                    self.logger.debug(
                        "🎯 Upserting extraction status (COMPLETED) for document"
                    )
                    success = await tx_store.batch_update_nodes(
                        [status_doc], CollectionNames.RECORDS.value
                    )
                    if not success:
                        self.logger.warning(
                            "⚠️ Failed to update extraction status for record %s - record may not exist",
                            record_id,
                        )
                        return

            except Exception as e:
                self.logger.error(f"❌ Error saving metadata to graph database: {str(e)}")
                raise
