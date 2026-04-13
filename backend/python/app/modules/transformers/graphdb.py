import uuid
from typing import Dict, List, Optional

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import SemanticMetadata
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class GraphDBTransformer(Transformer):
    def __init__(self, graph_provider: IGraphDBProvider, logger) -> None:
        super().__init__()
        self.logger = logger
        self.graph_data_store = GraphDataStore(logger, graph_provider)

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        metadata = record.semantic_metadata
        virtual_record_id = record.virtual_record_id
        record_id = record.id

        if metadata is None:
            try:
                async with self.graph_data_store.transaction() as tx_store:
                    # Update extraction status for the record
                    timestamp = get_epoch_timestamp_in_ms()
                    # Update indexing status for the record
                    status_doc = {
                        "id": record_id,
                        "extractionStatus": "FAILED",
                        "lastExtractionTimestamp": timestamp,
                        "indexingStatus": "COMPLETED",
                        "isDirty": False,
                        "virtualRecordId": virtual_record_id,
                        "lastIndexTimestamp": timestamp,
                    }
                    self.logger.info(
                        "🎯 Upserting indexing status metadata for document"
                    )
                    await tx_store.batch_upsert_nodes(
                        [status_doc], CollectionNames.RECORDS.value
                    )
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
    def _node_key(doc: Dict) -> str:
        """Extract the node key from a document returned by the graph provider."""
        return doc.get("_key") or doc.get("id")

    async def _find_or_create_node(
        self,
        tx_store,
        collection: str,
        filter_field: str,
        filter_value: str,
    ) -> str:
        """
        Look up a node by a single field; create it if it does not exist.
        Returns the node key.
        """
        results = await tx_store.get_nodes_by_filters(
            collection, {filter_field: filter_value}
        )
        if results:
            return self._node_key(results[0])

        new_key = str(uuid.uuid4())
        await tx_store.batch_upsert_nodes(
            [{"id": new_key, "name": filter_value}],
            collection,
        )
        return new_key

    async def _reconcile_edges(
        self,
        tx_store,
        record_id: str,
        record_from: str,
        edge_collection: str,
        new_tos: Dict[str, str],
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
        existing_by_to: Dict[str, Dict] = {e["_to"]: e for e in existing_edges}

        # 2. Create edges that are new (in new but not in existing)
        edges_to_create: List[Dict] = []
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
                self.logger.info(f"🔗 Created {label} edge: {record_id} -> {name}")
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

    # ------------------------------------------------------------------
    # main persistence logic
    # ------------------------------------------------------------------

    async def save_metadata_to_db(
        self, record_id: str, metadata: SemanticMetadata, virtual_record_id: str, is_vlm_ocr_processed: bool = False
    ) -> None:
        """
        Extract metadata from a document and create department relationships.
        Uses reconciliation logic: fetch existing edges, compare with new, create new ones, delete stale ones.
        """

        self.logger.info("🚀 Saving metadata to graph database")
        async with self.graph_data_store.transaction() as tx_store:
            try:
                # Retrieve the document content from graph database
                record = await tx_store.get_record_by_key(
                    record_id
                )

                if record is None:
                    self.logger.error(f"❌ Record {record_id} not found in database")
                    raise Exception(f"Record {record_id} not found in database")

                record_from = f"{CollectionNames.RECORDS.value}/{record_id}"

                # --- Reconcile department edges ---
                new_dept_tos: Dict[str, str] = {}
                for department in metadata.departments:
                    try:
                        results = await tx_store.get_nodes_by_filters(
                            CollectionNames.DEPARTMENTS.value,
                            {"departmentName": department},
                        )
                        if results:
                            dept_key = self._node_key(results[0])
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

                # --- Reconcile category edges ---
                new_cat_tos: Dict[str, str] = {}

                # Handle primary category
                category_key = await self._find_or_create_node(
                    tx_store, CollectionNames.CATEGORIES.value, "name", metadata.categories[0]
                )
                cat_to = f"{CollectionNames.CATEGORIES.value}/{category_key}"
                new_cat_tos[cat_to] = metadata.categories[0]

                # Handle subcategories
                async def handle_subcategory(
                    name: str, level: str, parent_key: str, parent_collection: str
                ) -> str:
                    collection_name = getattr(CollectionNames, f"SUBCATEGORIES{level}").value
                    key = await self._find_or_create_node(tx_store, collection_name, "name", name)

                    sub_to = f"{collection_name}/{key}"
                    new_cat_tos[sub_to] = name

                    # Create hierarchy relationship (inter-category)
                    # batch_create_edges uses UPSERT so this is idempotent
                    if parent_key:
                        await tx_store.batch_create_edges(
                            [{
                                "from_id": key,
                                "from_collection": collection_name,
                                "to_id": parent_key,
                                "to_collection": parent_collection,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            }],
                            CollectionNames.INTER_CATEGORY_RELATIONS.value,
                        )
                    return key

                # Process subcategories
                sub1_key: Optional[str] = None
                sub2_key: Optional[str] = None
                if metadata.sub_category_level_1:
                    sub1_key = await handle_subcategory(
                        metadata.sub_category_level_1, "1",
                        category_key, CollectionNames.CATEGORIES.value,
                    )
                if metadata.sub_category_level_2 and sub1_key:
                    sub2_key = await handle_subcategory(
                        metadata.sub_category_level_2, "2",
                        sub1_key, CollectionNames.SUBCATEGORIES1.value,
                    )
                if metadata.sub_category_level_3 and sub2_key:
                    await handle_subcategory(
                        metadata.sub_category_level_3, "3",
                        sub2_key, CollectionNames.SUBCATEGORIES2.value,
                    )

                # Reconcile category edges (convert set to dict for _reconcile_edges)
                await self._reconcile_edges(
                    tx_store, record_id, record_from,
                    CollectionNames.BELONGS_TO_CATEGORY.value,
                    new_cat_tos, "category",
                )

                # --- Reconcile language edges ---
                new_lang_tos: Dict[str, str] = {}
                for language in metadata.languages:
                    lang_key = await self._find_or_create_node(
                        tx_store, CollectionNames.LANGUAGES.value, "name", language
                    )
                    lang_to = f"{CollectionNames.LANGUAGES.value}/{lang_key}"
                    new_lang_tos[lang_to] = language

                await self._reconcile_edges(
                    tx_store, record_id, record_from,
                    CollectionNames.BELONGS_TO_LANGUAGE.value,
                    new_lang_tos, "language",
                )

                # --- Reconcile topic edges ---
                new_topic_tos: Dict[str, str] = {}
                for topic in metadata.topics:
                    topic_key = await self._find_or_create_node(
                        tx_store, CollectionNames.TOPICS.value, "name", topic
                    )
                    topic_to = f"{CollectionNames.TOPICS.value}/{topic_key}"
                    new_topic_tos[topic_to] = topic

                await self._reconcile_edges(
                    tx_store, record_id, record_from,
                    CollectionNames.BELONGS_TO_TOPIC.value,
                    new_topic_tos, "topic",
                )

                self.logger.info(
                    "🚀 Metadata saved successfully for document"
                )

                # Update extraction status for the record
                timestamp = get_epoch_timestamp_in_ms()
                status_doc = {
                    "id": record_id,
                    "extractionStatus": "COMPLETED",
                    "lastExtractionTimestamp": timestamp,
                    "indexingStatus": "COMPLETED",
                    "isDirty": False,
                    "virtualRecordId": virtual_record_id,
                    "lastIndexTimestamp": timestamp,
                }

                if is_vlm_ocr_processed:
                    status_doc["isVLMOcrProcessed"] = True

                self.logger.info(
                    "🎯 Upserting extraction status metadata for document"
                )
                await tx_store.batch_upsert_nodes(
                    [status_doc], CollectionNames.RECORDS.value
                )

            except Exception as e:
                self.logger.error(f"❌ Error saving metadata to graph database: {str(e)}")
                raise

