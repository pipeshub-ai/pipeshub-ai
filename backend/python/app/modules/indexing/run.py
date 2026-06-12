from typing import Any, Dict, List

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.exceptions.indexing_exceptions import (
    EmbeddingDeletionError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.services.vector_db.interface.vector_db import IVectorDBService

# Module-level stub to allow tests to patch FastEmbedSparse even though
# it is only lazily imported inside VectorStore (not used here directly).
try:
    from fastembed import SparseTextEmbedding as FastEmbedSparse  # noqa: F401
except ImportError:
    FastEmbedSparse = None  # type: ignore[assignment,misc]

# Constants for bulk deletion
QDRANT_BULK_DELETE_BATCH_SIZE = 100


class IndexingPipeline:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        graph_provider,
        collection_name: str,
        vector_db_service: IVectorDBService,
    ) -> None:
        """Initialize the indexing pipeline with necessary configurations.

        Args:
            logger: Logger instance
            config_service: Configuration service
            graph_provider: Arango service
            collection_name: Name for the collection
            vector_db_service: Vector DB service
        """
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider

        try:
            self.vector_db_service = vector_db_service
            self.collection_name = collection_name

        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline: " + str(e),
                details={"error": str(e)},
            )

    async def bulk_delete_embeddings(self, virtual_record_ids: List[str]) -> Dict[str, Any]:
        """
        Bulk delete embeddings for multiple records in a single operation.
        Uses filter-based deletion for efficiency.

        This is used when deleting a connector instance and all its records.

        Args:
            virtual_record_ids: List of virtual record IDs to delete embeddings for

        Returns:
            Dict with deletion statistics:
                - virtual_record_ids_processed: Number of virtual record IDs eligible for deletion
                - success: Boolean indicating success

        Raises:
            EmbeddingDeletionError: If there's an error during the deletion process
        """
        try:
            if not virtual_record_ids:
                self.logger.info("No virtual record IDs provided for bulk deletion")
                return {"virtual_record_ids_processed": 0, "success": True}

            # Normalize IDs: remove empty values and deduplicate while preserving order
            normalized_virtual_record_ids = list(
                dict.fromkeys(
                    virtual_record_id.strip()
                    for virtual_record_id in virtual_record_ids
                    if isinstance(virtual_record_id, str) and virtual_record_id.strip()
                )
            )

            if not normalized_virtual_record_ids:
                self.logger.info("No valid virtual record IDs provided for bulk deletion")
                return {"virtual_record_ids_processed": 0, "success": True}

            self.logger.info(
                f"🗑️ Starting bulk deletion candidate evaluation for {len(normalized_virtual_record_ids)} virtual record IDs"
            )

            safe_virtual_record_ids: List[str] = []
            skipped_virtual_record_ids: List[str] = []

            for virtual_record_id in normalized_virtual_record_ids:
                try:
                    remaining_records = await self.graph_provider.get_records_by_virtual_record_id(
                        virtual_record_id=virtual_record_id
                    )
                    if remaining_records:
                        skipped_virtual_record_ids.append(virtual_record_id)
                        self.logger.info(
                            f"⏭️ Skipping bulk deletion for virtual_record_id {virtual_record_id} "
                            f"because it is still referenced by records: {remaining_records}"
                        )
                        continue

                    safe_virtual_record_ids.append(virtual_record_id)
                except Exception as e:
                    skipped_virtual_record_ids.append(virtual_record_id)
                    self.logger.error(
                        f"❌ Failed to validate virtual_record_id {virtual_record_id} before bulk deletion: {e}. "
                        f"Skipping this ID to avoid accidental data loss."
                    )

            if skipped_virtual_record_ids:
                self.logger.info(
                    f"⏭️ Skipped {len(skipped_virtual_record_ids)} virtual record IDs during bulk deletion safety checks"
                )

            if not safe_virtual_record_ids:
                self.logger.info(
                    "No virtual record IDs are eligible for bulk deletion after safety checks"
                )
                return {"virtual_record_ids_processed": 0, "success": True}

            self.logger.info(
                f"🗑️ Proceeding with bulk deletion for {len(safe_virtual_record_ids)} safe virtual record IDs"
            )

            # Delete from virtualRecordToDocIdMapping collection in batch
            try:
                await self.graph_provider.delete_nodes(
                    keys=safe_virtual_record_ids,
                    collection=CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                )
                self.logger.info(
                    f"✅ Deleted {len(safe_virtual_record_ids)} entries from virtualRecordToDocIdMapping"
                )
            except Exception as e:
                # This is critical for data consistency - log as error
                self.logger.error(
                    f"❌ Failed to delete from virtualRecordToDocIdMapping: {e}. "
                    f"This may lead to orphaned entries in ArangoDB."
                )
                # Continue with vector store cleanup as primary goal

            # Process in batches to avoid filter size limits
            for i in range(0, len(safe_virtual_record_ids), QDRANT_BULK_DELETE_BATCH_SIZE):
                batch = safe_virtual_record_ids[i:i + QDRANT_BULK_DELETE_BATCH_SIZE]
                batch_num = i // QDRANT_BULK_DELETE_BATCH_SIZE + 1

                try:
                    filter_dict = await self.vector_db_service.filter_collection(
                        should={"virtualRecordId": batch}
                    )

                    await self.vector_db_service.delete_points(
                        collection_name=self.collection_name,
                        filter=filter_dict,
                    )
                    self.logger.info(f"✅ Deleted embeddings for batch {batch_num}")

                except Exception as e:
                    self.logger.error(f"❌ Failed to delete embeddings for batch {batch_num}: {e}")
                    # Continue with next batch even if one fails
                    continue

            self.logger.info(
                f"✅ Bulk deletion complete: embeddings deleted for {len(safe_virtual_record_ids)} virtual record IDs"
            )

            return {
                "virtual_record_ids_processed": len(safe_virtual_record_ids),
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to bulk delete embeddings: {str(e)}")
            raise EmbeddingDeletionError(
                f"Bulk embedding deletion failed: {str(e)}",
                record_id="bulk_delete",
                details={"error": str(e), "count": len(virtual_record_ids) if virtual_record_ids else 0}
            )

    def _process_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and enhance document metadata.

        Args:
            metadata: Original metadata dictionary

        Returns:
            Dict[str, Any]: Enhanced metadata

        Raises:
            MetadataProcessingError: If there's an error processing the metadata
        """
        try:
            block_type = meta.get("blockType", "text")
            virtual_record_id = meta.get("virtualRecordId", "")
            record_name = meta.get("recordName", "")
            if isinstance(block_type, list):
                block_type = block_type[0]

            enhanced_metadata = {
                "orgId": meta.get("orgId", ""),
                "virtualRecordId": virtual_record_id,
                "recordName": record_name,
                "recordType": meta.get("recordType", ""),
                "recordVersion": meta.get("version", ""),
                "origin": meta.get("origin", ""),
                "connector": meta.get("connectorName", ""),
                "blockNum": meta.get("blockNum", [0]),
                "blockText": meta.get("blockText", ""),
                "blockType": str(block_type),
                "departments": meta.get("departments", ""),
                "topics": meta.get("topics", ""),
                "categories": meta.get("categories", ""),
                "subcategoryLevel1": meta.get("subcategoryLevel1", ""),
                "subcategoryLevel2": meta.get("subcategoryLevel2", ""),
                "subcategoryLevel3": meta.get("subcategoryLevel3", ""),
                "languages": meta.get("languages", ""),
                "extension": meta.get("extension", ""),
                "mimeType": meta.get("mimeType", ""),
            }

            if meta.get("bounding_box"):
                enhanced_metadata["bounding_box"] = meta.get("bounding_box")
            if meta.get("sheetName"):
                enhanced_metadata["sheetName"] = meta.get("sheetName")
            if meta.get("sheetNum"):
                enhanced_metadata["sheetNum"] = meta.get("sheetNum")
            if meta.get("pageNum"):
                enhanced_metadata["pageNum"] = meta.get("pageNum")

            return enhanced_metadata

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                f"Unexpected error processing metadata: {str(e)}",
                details={"error_type": type(e).__name__},
            )
